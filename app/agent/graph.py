"""基于 LangGraph 的客服 Agent：分析 -> 检索 -> 工具 -> 生成。

状态通过 MemorySaver 按 session_id 持久化，支持多轮对话与断点续跑。
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from ..llm import LLMCredentials
from ..llm import chat as llm_chat
from ..rag.store import get_store
from .composer import compose
from .prompts import SYSTEM_PROMPT, build_context
from .tools import TOOL_SCHEMAS, execute_tool, extract_money, match_product

logger = logging.getLogger(__name__)

TOOL_INTENTS = {"product", "return", "warranty", "install", "handoff", "tech"}
DOC_FILTER = {
    "return": "returns",
    "warranty": "faq",
    "install": "faq",
    "logistics": "faq",
    "payment": "faq",
    "tech": "faq",
    "product": "products",
}

# 意图扩展词：把问题里的口语部分补齐成文档中的正式说法，提升检索命中率
QUERY_HINTS = {
    "return": "退货 退款 费用 运费 时效",
    "warranty": "保修政策 保修期限 保修范围",
    "install": "安装 难度 工具",
    "logistics": "发货 物流 快递 时效",
    "payment": "支付 发票 包邮 运费",
    "tech": "区别 原理 更换周期",
}


class AgentState(TypedDict, total=False):
    session_id: str
    question: str
    history: list[dict]
    intent: str
    entities: dict
    retrieved: list[dict]
    tool_results: list[dict]
    cards: list[dict]
    answer: str
    citations: list[dict]
    mode: str
    latency_ms: int
    # 本次请求携带的模型凭据（仅内存中流转，不持久化）
    cred: LLMCredentials


# ------------------------- 意图 & 实体 -------------------------
GREETINGS = ("你好", "您好", "hi", "hello", "哈喽", "在吗", "在不在", "早上好", "晚上好", "中午好")
THANKS = ("谢谢", "感谢", "辛苦了", "多谢", "好的谢谢", "thx")


def detect_intent(q: str) -> str:
    text = q.strip().lower()
    if any(word in text for word in ("转人工", "人工", "客服电话", "投诉", "差评", "联系你们", "加微信")):
        return "handoff"
    if any(
        word in text
        for word in (
            "退货", "退款", "退换", "换货", "无理由", "退回", "退掉", "退钱",
            "退多少", "能退", "怎么退", "退吗",
        )
    ):
        return "return"
    if any(word in text for word in ("保修", "质保", "包修", "保多久", "保修期")):
        return "warranty"
    if any(word in text for word in ("安装", "自己装", "自己换", "工具", "难度", "怎么换", "匹配", "复位", "故障灯")):
        return "install"
    if any(word in text for word in ("发货", "物流", "快递", "到货", "几天到", "签收", "到货", "运费到")):
        return "logistics"
    if any(word in text for word in ("发票", "支付", "付款", "花呗", "信用卡", "包邮", "运费怎么", "开票")):
        return "payment"
    if any(
        word in text
        for word in (
            "oem", "原厂件", "副厂件", "再制造", "翻新件", "陶瓷配方", "半金属", "5w-30", "5w-40",
            "agm", "区别", "对比", "更换周期", "多久换", "什么是", "什么意思", "靠不靠谱", "省油",
        )
    ):
        return "tech"
    if any(word in text for word in ("多少钱", "价格", "报价", "有货", "有吗", "适配", "适合", "型号", "推荐", "买什么")):
        return "product"
    if match_product(q):
        return "product"
    if any(word in text for word in GREETINGS):
        return "greeting"
    if any(word in text for word in THANKS):
        return "thanks"
    return "general"


CATEGORY_HINTS = [
    ("刹车", "制动系统"), ("制动", "制动系统"),
    ("发动机", "发动机系统"), ("点火", "发动机系统"), ("正时", "发动机系统"),
    ("电气", "电气系统"), ("电路", "电气系统"), ("电瓶", "电气系统"),
    ("悬挂", "悬挂转向系统"), ("底盘", "悬挂转向系统"), ("转向", "悬挂转向系统"),
    ("空调", "车身附件及空调系统"), ("车身", "车身附件及空调系统"),
    ("易损", "易损消耗件"), ("滤芯", "易损消耗件"),
]


def extract_entities(q: str) -> dict:
    item = match_product(q)
    entities: dict[str, Any] = {
        "product_id": item["id"] if item else "",
        "product_name": item["name"] if item else "",
        "category": item.get("category", "") if item else "",
        "system": item.get("system", "") if item else "",
        "car": "",
        "max_price": None,
        "amounts": extract_money(q),
    }
    if not entities["system"]:
        for hint, system in CATEGORY_HINTS:
            if hint in q:
                entities["system"] = system
                break

    for brand in ("丰田", "本田", "大众", "宝马", "奔驰", "奥迪", "日产", "别克", "雪佛兰", "现代", "起亚", "福特"):
        if brand in q:
            match = re.search(brand + r"[\u4e00-\u9fffA-Za-z0-9\-]{0,6}", q)
            entities["car"] = match.group(0) if match else brand
            break

    limit = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块)?\s*(?:以内|以下|之内|左右)", q)
    if not limit:
        limit = re.search(r"(?:预算|不超过|最多|低于)\s*(\d+(?:\.\d+)?)", q)
    if limit:
        entities["max_price"] = float(limit.group(1))
    return entities


# ------------------------- 节点 -------------------------
def analyze_node(state: AgentState) -> dict:
    question = (state.get("question") or "").strip()
    history = list(state.get("history") or [])
    previous = list(history)
    history.append({"role": "user", "content": question})
    intent = detect_intent(question)
    entities = extract_entities(question)

    # 代词指代（“这个 / 它 / 这款”）时，从上一轮问题继承商品
    if not entities.get("product_id") and any(word in question for word in ("这个", "它", "该", "这款", "那款", "刚才")):
        for msg in reversed(previous):
            if msg.get("role") != "user":
                continue
            item = match_product(msg.get("content", ""))
            if item:
                entities.update(
                    {
                        "product_id": item["id"],
                        "product_name": item["name"],
                        "category": item.get("category", ""),
                        "system": item.get("system", ""),
                    }
                )
                break

    return {
        "question": question,
        "history": history[-20:],
        "intent": intent,
        "entities": entities,
        # 每轮清空上一轮的中间结果，避免多轮会话串数据
        "retrieved": [],
        "tool_results": [],
        "cards": [],
        "citations": [],
        "answer": "",
        "mode": "local",
    }


def retrieve_node(state: AgentState) -> dict:
    question = state.get("question", "")
    intent = state.get("intent", "general")
    entities = state.get("entities", {})
    if intent in {"greeting", "thanks"}:
        return {"retrieved": []}
    query = question
    if entities.get("product_name"):
        query = f"{question} {entities['product_name']}"
    if QUERY_HINTS.get(intent):
        query = f"{query} {QUERY_HINTS[intent]}"

    doc = DOC_FILTER.get(intent)
    try:
        chunks = get_store().search(query, top_k=6, doc=doc)
        if len([c for c in chunks if c.get("score", 0) >= 0.1]) < 2 and doc:
            fallback = get_store().search(query, top_k=6, doc=None)
            seen = {c["id"] for c in chunks}
            chunks += [c for c in fallback if c["id"] not in seen]
        # 泛问且没有实义词命中时，视为知识库未覆盖，交给兜底话术而不是硬答
        if intent == "general" and chunks and max(c.get("lexical_coverage", 0) for c in chunks) < 0.25:
            chunks = []
    except Exception as exc:  # noqa: BLE001
        logger.warning("检索失败：%s", exc)
        chunks = []
    return {"retrieved": chunks}


def tools_node(state: AgentState) -> dict:
    intent = state.get("intent", "general")
    question = state.get("question", "")
    entities = state.get("entities", {})
    results: list[dict] = []

    def run(name: str, args: dict) -> None:
        result = execute_tool(name, args)
        results.append({"name": name, "args": args, "result": result})

    if intent == "product":
        wants_alternatives = any(
            word in question for word in ("推荐", "几款", "有哪些", "对比", "区别", "替代", "都有")
        )
        browse_category = False
        if entities.get("product_name") and not wants_alternatives:
            # 已经锁定具体商品：精确返回该商品，避免带出一堆无关配件
            keyword = entities["product_name"]
            if entities.get("max_price"):
                keyword = re.sub(r"[（(].*?[）)]", "", keyword).strip()
        elif wants_alternatives and entities.get("system") and not entities.get("product_name"):
            # 按系统分类浏览：不拿整句问题当关键词，保持商品自然顺序
            keyword = ""
            browse_category = True
        else:
            keyword = entities.get("product_name") or re.sub(
                r"(多少钱|什么价格|价格|报价|有货吗|有吗|适配|适合|推荐|吗|呢|\?|？)", "", question
            ).strip()
        if entities.get("max_price") and keyword:
            keyword = re.sub(r"[（(].*?[）)]", "", keyword).strip()
        args = {"keyword": keyword if (keyword or browse_category) else question, "limit": 5}
        if entities.get("system"):
            args["category"] = entities["system"]
        if entities.get("max_price"):
            args["max_price"] = entities["max_price"]
        if entities.get("car"):
            args["fitment"] = entities["car"]
        run("search_products", args)
        if entities.get("max_price") and results and results[0]["result"].get("found") == 0:
            alternative = execute_tool(
                "search_products",
                {"keyword": args.get("keyword", ""), "category": args.get("category", ""), "limit": 3},
            )
            if alternative.get("found"):
                summary = results[0]["result"].get("summary", "")
                summary += "\n\n预算内暂无匹配，接近的选项：\n" + alternative.get("summary", "")
                results[0]["result"]["summary"] = summary

    elif intent == "return":
        fee_question = any(
            word in question for word in ("退货运费", "寄回运费", "运费谁", "运费怎么", "运费多少", "谁承担运费")
        )
        refund_question = any(
            word in question for word in ("退多少", "退款多少", "能退多少", "退款金额", "退我多少", "退多少钱")
        )
        if not (fee_question and not refund_question):
            amounts = entities.get("amounts") or []
            args = _refund_args(question, amounts)
            if args:
                run("calculate_refund", args)

    elif intent == "warranty":
        if entities.get("product_name") or entities.get("category"):
            run(
                "check_warranty",
                {"product": entities.get("product_name", ""), "category": entities.get("category", "")},
            )

    elif intent == "install":
        if entities.get("product_name") or entities.get("category"):
            run(
                "install_guide",
                {"product": entities.get("product_name", ""), "category": entities.get("category", "")},
            )

    elif intent == "handoff":
        run("human_handoff", {"reason": question[:80]})

    elif intent == "tech" and any(
        word in question for word in ("多久", "更换周期", "多长时间", "几万公里", "多少公里", "什么时候换")
    ):
        run("maintenance_cycle", {"part": entities.get("product_name") or question})

    return {"tool_results": results}


def _refund_args(question: str, amounts: list[float]) -> dict | None:
    item_price = None
    order_total = None
    discount = 0.0
    shipping_fee = 0.0
    return_shipping = 0.0

    for pattern in (
        r"(\d+(?:\.\d+)?)\s*元(?:的)?(?:商品|东西|件|货|刹车片|机油|配件)",
        r"(?:商品|东西|件|买的是|买的|售价|价格|花了)\s*[^\d]{0,4}(\d+(?:\.\d+)?)",
    ):
        match = re.search(pattern, question)
        if match:
            item_price = float(match.group(1))
            break

    match = re.search(
        r"(?:订单(?:总额|总价|金额)|总价|总额|一共|总共)[^\d]{0,6}(\d+(?:\.\d+)?)", question
    )
    if match:
        order_total = float(match.group(1))

    match = re.search(r"(?:满减|优惠|折扣)[^\d]{0,6}(\d+(?:\.\d+)?)", question)
    if match:
        discount = float(match.group(1))

    match = re.search(r"(?:寄回|退回|退货运费)[^\d]{0,6}(\d+(?:\.\d+)?)", question)
    if match:
        return_shipping = float(match.group(1))
    else:
        match = re.search(r"(?:发货)?运费[^\d]{0,6}(\d+(?:\.\d+)?)", question)
        if match:
            shipping_fee = float(match.group(1))

    if item_price is None:
        used = {order_total, discount, shipping_fee, return_shipping}
        for match in re.finditer(r"(\d+(?:\.\d+)?)\s*元", question):
            value = float(match.group(1))
            if value in used:
                continue
            item_price = value
            break
    if item_price is None and len(amounts) == 1:
        item_price = amounts[0]
    if item_price is None:
        return None

    reason = "no_reason"
    if any(word in question for word in ("质量", "坏了", "故障", "损坏", "不工作", "漏油", "异响", "发错")):
        reason = "quality"
    if "发错" in question:
        reason = "wrong_item"

    return {
        "item_price": item_price,
        "order_total": order_total or item_price,
        "discount": discount,
        "shipping_fee": shipping_fee,
        "buyer_paid_shipping": any(word in question for word in ("运费我付", "付了运费", "运费自理")),
        "return_shipping": return_shipping,
        "reason": reason,
    }


def _build_citations(
    chunks: list[dict], product_id: str = "", intent: str = "", tool_names: tuple[str, ...] = ()
) -> list[dict]:
    if product_id:
        focused = [c for c in chunks if c.get("product_id") == product_id]
        if focused:
            chunks = focused
    if intent in {"warranty", "install"}:
        keyword = "保修" if intent == "warranty" else "安装"
        focused = [c for c in chunks if keyword in c.get("section", "")]
        if focused:
            chunks = focused
    elif "calculate_refund" in tool_names:
        focused = [
            c
            for c in chunks
            if any(word in c.get("section", "") for word in ("费用", "退款", "时效", "无理由"))
        ]
        if focused:
            chunks = focused
    elif "maintenance_cycle" in tool_names:
        focused = [
            c
            for c in chunks
            if "更换周期" in c.get("section", "") or "更换周期" in c.get("text", "")[:120]
        ]
        if focused:
            chunks = focused
    elif intent == "tech":
        focused = [c for c in chunks if c.get("lexical_coverage", 0) >= 0.4]
        if focused:
            chunks = focused
    citations = []
    seen: set[str] = set()
    for chunk in chunks:
        if chunk.get("score", 0) < 0.1 or chunk["id"] in seen:
            continue
        seen.add(chunk["id"])
        citations.append(
            {
                "id": chunk["id"],
                "title": chunk.get("doc_title", ""),
                "section": chunk.get("section", ""),
                "doc": chunk.get("doc", ""),
                "score": chunk.get("score", 0),
                "snippet": chunk.get("snippet", ""),
            }
        )
        if len(citations) >= 4:
            break
    return citations


def _build_cards(tool_results: list[dict]) -> list[dict]:
    cards: list[dict] = []
    seen: set[str] = set()
    for item in tool_results:
        result = item.get("result") or {}
        for card in result.get("products") or []:
            if card["id"] not in seen:
                seen.add(card["id"])
                cards.append(card)
    return cards[:6]


def _llm_answer(
    cred: LLMCredentials,
    question: str,
    history: list[dict],
    chunks: list[dict],
    tool_results: list[dict],
) -> tuple[str, list[dict]]:
    context = build_context(chunks, tool_results)
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history[-7:-1]:
        if msg.get("role") in {"user", "assistant"} and msg.get("content"):
            messages.append({"role": msg["role"], "content": msg["content"][:1200]})
    messages.append(
        {
            "role": "user",
            "content": f"用户问题：{question}\n\n{context}\n\n请严格依据以上资料回答，并标注引用序号。",
        }
    )

    collected: list[dict] = []
    for _ in range(2):
        msg = llm_chat(cred, messages, tools=TOOL_SCHEMAS)
        if not msg["tool_calls"]:
            return msg["content"].strip(), collected
        messages.append(
            {
                "role": "assistant",
                "content": msg["content"] or "",
                "tool_calls": [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {"name": call["name"], "arguments": call["arguments"]},
                    }
                    for call in msg["tool_calls"]
                ],
            }
        )
        for call in msg["tool_calls"]:
            try:
                args = json.loads(call["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            result = execute_tool(call["name"], args)
            collected.append({"name": call["name"], "args": args, "result": result})
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(result, ensure_ascii=False)[:2000],
                }
            )
    final = llm_chat(cred, messages)
    return final["content"].strip(), collected


def generate_node(state: AgentState) -> dict:
    started = time.perf_counter()
    question = state.get("question", "")
    intent = state.get("intent", "general")
    history = list(state.get("history") or [])
    chunks = state.get("retrieved") or []
    tool_results = list(state.get("tool_results") or [])
    handoff = next((item["result"] for item in tool_results if item["name"] == "human_handoff"), None)

    cred = state.get("cred")
    mode = "local"
    answer = ""
    if cred is not None and cred.complete and intent not in {"greeting", "thanks"}:
        try:
            answer, extra = _llm_answer(cred, question, history, chunks, tool_results)
            if extra:
                tool_results.extend(extra)
            mode = "llm" if answer else "local"
        except Exception as exc:  # noqa: BLE001
            logger.warning("大模型调用失败，降级本地回答：%s", exc)
            mode = "local-fallback"

    if not answer:
        answer = compose(question, intent, chunks, tool_results, handoff=handoff)

    history.append({"role": "assistant", "content": answer})
    entities = state.get("entities") or {}
    focus_id = entities.get("product_id", "") if intent == "product" else ""
    tool_names = tuple(item["name"] for item in tool_results)
    citations = _build_citations(chunks, focus_id, intent, tool_names)

    cycle = next((item["result"] for item in tool_results if item["name"] == "maintenance_cycle"), None)
    if cycle and cycle.get("table") and not any("更换周期" in c["section"] for c in citations):
        citations.insert(
            0,
            {
                "id": "faq-appendix-cycle",
                "title": "常见问题解答 FAQ V1.0",
                "section": "附录：常用配件更换周期参考表",
                "doc": "faq",
                "score": 1.0,
                "snippet": cycle["table"],
            },
        )

    cards = _build_cards(tool_results)
    if intent == "product":
        search_result = next(
            (item["result"] for item in tool_results if item["name"] == "search_products"), None
        )
        if search_result is not None and search_result.get("found") == 0:
            citations = []

    return {
        "answer": answer,
        "cards": cards,
        "citations": citations[:4],
        "tool_results": tool_results,
        "mode": mode,
        "history": history[-20:],
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


# ------------------------- 图 -------------------------
def _route(state: AgentState) -> str:
    return "tools" if state.get("intent") in TOOL_INTENTS else "generate"


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("analyze", analyze_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("tools", tools_node)
    graph.add_node("generate", generate_node)

    graph.set_entry_point("analyze")
    graph.add_edge("analyze", "retrieve")
    graph.add_conditional_edges("retrieve", _route, {"tools": "tools", "generate": "generate"})
    graph.add_edge("tools", "generate")
    graph.add_edge("generate", END)
    return graph.compile(checkpointer=MemorySaver())


class SupportAgent:
    def __init__(self) -> None:
        self.graph = build_graph()

    def chat(
        self,
        session_id: str,
        message: str,
        history: list[dict] | None = None,
        cred: LLMCredentials | None = None,
    ) -> dict:
        started = time.perf_counter()
        config = {"configurable": {"thread_id": session_id}}
        payload: dict = {"session_id": session_id, "question": message}
        if cred is not None and cred.complete:
            payload["cred"] = cred
        if history:
            # 服务重启后内存状态为空，用 MySQL 中的历史恢复多轮上下文
            try:
                snapshot = self.graph.get_state(config)
                has_state = bool(snapshot and snapshot.values)
            except Exception:  # noqa: BLE001
                has_state = False
            if not has_state:
                payload["history"] = [
                    {"role": item.get("role", "user"), "content": item.get("content", "")}
                    for item in history
                    if item.get("content")
                ][-20:]
        state = self.graph.invoke(payload, config=config)
        return {
            "session_id": session_id,
            "answer": state.get("answer", ""),
            "intent": state.get("intent", "general"),
            "citations": state.get("citations", []),
            "cards": state.get("cards", []),
            "tools": [
                {"name": item["name"], "args": item.get("args", {})}
                for item in state.get("tool_results", [])
            ],
            "mode": state.get("mode", "local"),
            "latency_ms": state.get("latency_ms") or int((time.perf_counter() - started) * 1000),
        }

    def history(self, session_id: str) -> list[dict]:
        config = {"configurable": {"thread_id": session_id}}
        snapshot = self.graph.get_state(config)
        return list(snapshot.values.get("history", [])) if snapshot and snapshot.values else []

    def reset(self, session_id: str) -> None:
        saver = getattr(self.graph, "checkpointer", None)
        if saver is not None and hasattr(saver, "delete_thread"):
            try:
                saver.delete_thread(session_id)
            except Exception:  # noqa: BLE001
                pass


_AGENT: SupportAgent | None = None


def get_agent() -> SupportAgent:
    global _AGENT
    if _AGENT is None:
        _AGENT = SupportAgent()
    return _AGENT
