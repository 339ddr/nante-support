"""本地检索式回答生成器（无 LLM Key 时的兜底，也可作为降级方案）。"""
from __future__ import annotations

from ..config import SHOP
from ..rag.text import bigrams, keyword_overlap
from .tools import check_warranty, get_product


def _fmt_price(value) -> str:
    if isinstance(value, (int, float)) and value > 0:
        return f"¥{value:,.2f}"
    return "询价"


def _source_line(chunks: list[dict]) -> str:
    if not chunks:
        return ""
    first = chunks[0]
    return f"\n> 依据：{first.get('doc_title', '')} · {first.get('section', '')}"


META_PREFIX = ("【", "文档：", "所属栏目", "所属章节")


def _blocks(chunk: dict) -> list[str]:
    text = chunk.get("text", "")
    # FAQ / 商品段落通常较短，整段返回，避免只截到半句话
    if chunk.get("kind") in {"faq", "product"} or len(text) <= 420:
        lines = [
            line.strip()
            for line in text.split("\n")
            if line.strip() and not line.strip().startswith(META_PREFIX)
        ]
        return ["\n".join(lines)] if lines else []

    lines = [line.strip() for line in text.split("\n")]
    blocks: list[str] = []
    table: list[str] = []

    def flush_table() -> None:
        nonlocal table
        if table:
            blocks.append("\n".join(table))
            table = []

    for line in lines:
        if line.startswith("|"):
            table.append(line)
            continue
        flush_table()
        if not line:
            continue
        if line.startswith(META_PREFIX):
            continue
        blocks.append(line)
    flush_table()
    return blocks


FOCUS_WORDS = (
    "多久", "更换", "周期", "条件", "费用", "运费", "流程", "时效",
    "保修", "安装", "适配", "区别", "对比", "互换", "标准",
)


def _filter_table(query: str, block: str) -> str | None:
    """表格只保留与问题相关的行，避免整张大表灌进回答。"""
    rows = [row for row in block.split("\n") if row.strip().startswith("|")]
    if len(rows) <= 1:
        return block
    header, body = rows[0], rows[1:]
    qgrams = bigrams(query)
    scored: list[tuple[float, str]] = []
    for row in body:
        ratio = len(qgrams & bigrams(row)) / max(1, len(qgrams))
        if ratio > 0:
            scored.append((ratio, row))
    if not scored:
        return None
    best = max(score for score, _ in scored)
    matched = [row for score, row in scored if score >= best * 0.8][:5]
    return "\n".join([header] + matched)


def _pick_blocks(query: str, chunks: list[dict], limit: int = 5, max_chunks: int = 2) -> list[str]:
    qgrams = bigrams(query)
    if not chunks:
        return []
    # 只保留与最佳结果同量级的文档，避免把弱相关条款一起塞进回答
    max_cov = max(chunk.get("lexical_coverage", 0) for chunk in chunks[:3])
    usable = []
    for chunk in chunks[:3]:
        coverage = chunk.get("lexical_coverage", 0)
        section_rel = len(qgrams & bigrams(chunk.get("section", ""))) / max(1, len(qgrams))
        if coverage >= max_cov * 0.6 and (section_rel >= 0.12 or coverage >= max_cov * 0.9):
            usable.append(chunk)
    usable = usable or chunks[:1]

    candidates: list[tuple[float, int, int, str]] = []
    for chunk_idx, chunk in enumerate(usable[:max_chunks]):
        section = chunk.get("section", "")
        section_bonus = len(qgrams & bigrams(section)) / max(1, len(qgrams))
        for block_idx, block in enumerate(_blocks(chunk)):
            if len(block) < 4:
                continue
            is_pure_table = block.startswith("|") and all(
                line.strip().startswith("|") for line in block.split("\n") if line.strip()
            )
            if is_pure_table:
                filtered = _filter_table(query, block)
                if not filtered:
                    continue
                block = filtered
            score = (
                keyword_overlap(query, block) * 2.2
                + len(qgrams & bigrams(block)) / max(1, len(qgrams))
                + section_bonus * 1.4
                + chunk.get("score", 0) * 0.25
            )
            # 问“多久换/条件/费用/怎么退”时，命中问题焦点的段落优先
            for focus in FOCUS_WORDS:
                if focus in query and focus in block:
                    score += 1.2
            if score <= 0:
                continue
            candidates.append((score, chunk_idx, block_idx, block))

    if not candidates:
        return []
    candidates.sort(key=lambda item: item[0], reverse=True)
    best = candidates[0][0]
    picked = [item for item in candidates if item[0] >= best * 0.5][:limit]
    picked.sort(key=lambda item: (item[1], item[2]))

    out: list[str] = []
    seen: set[str] = set()
    total = 0
    for _, _, _, block in picked:
        key = block[:40]
        if key in seen:
            continue
        seen.add(key)
        if total + len(block) > 1200:
            break
        total += len(block)
        out.append(block)
    return out


SHIPPING_RULES = """| 退货场景 | 退回运费 | 原订单运费 |
| --- | --- | --- |
| 七天无理由退货 | 买家承担 | 包邮订单需扣回实际发货运费 |
| 质量问题 / 发错货 | 卖家承担 | 卖家全额退还 |
| 换货（买家买错/不喜欢） | 买家承担 | 买家承担再次发货运费 |
| 换货（质量问题/发错货） | 卖家承担 | 卖家承担再次发货运费 |"""


def _return_guide() -> str:
    return (
        "**退货 / 换货办理指南**\n\n"
        "**1. 先确认是否符合条件**\n"
        "- 签收后 7 天内，商品未安装、未使用、未上车测试\n"
        "- 包装、附件、说明书、保修卡齐全，不影响二次销售\n"
        "- 油液类未开封；电子件未通电；非定制或特别订货商品\n\n"
        "**2. 提交申请**\n"
        "- 渠道：店铺在线客服 / 订单页「申请售后」/ 微信 nante_parts / 电话 400-888-8888\n"
        "- 需提供：订单号、退货原因、商品状态；质量问题需照片或视频\n\n"
        "**3. 寄回与验收**\n"
        "- 审核通过后 7 天内寄出，逾期申请自动关闭\n"
        "- 仓库验收 1-3 个工作日，通过后 24 小时内退款（原路退回）\n\n"
        "**4. 运费怎么算**\n"
        f"{SHIPPING_RULES}\n\n"
        "> 依据：退货政策 第六条、第十条、第十四至十九条"
    )


def _bullets(blocks: list[str]) -> str:
    parts = []
    for block in blocks:
        if block.startswith("|") or "\n" in block:
            parts.append(block)
        else:
            block = block.lstrip("-• ").strip()
            parts.append(f"- {block}")
    return "\n\n".join(parts)


def _product_detail(item: dict) -> str:
    rows = [
        ("商品编号", item.get("id", "")),
        ("价格", _fmt_price(item.get("price"))),
        ("品牌", item.get("brand", "")),
        ("系统 / 品类", f"{item.get('system', '')} / {item.get('category', '')}"),
        ("适配车型", item.get("fitment", "")),
        ("核心属性", item.get("attrs", "")),
        ("保修", item.get("warranty", "") or check_warranty(item["id"]).get("period", "见保修政策")),
    ]
    if item.get("package"):
        rows.append(("包装 / 套装", item["package"]))
    if item.get("life"):
        rows.append(("建议寿命", item["life"]))
    table = ["| 项目 | 参数 |", "| --- | --- |"]
    table += [f"| {k} | {str(v).replace('|', '/')} |" for k, v in rows if v]
    desc = item.get("description", "")
    body = "\n".join(table)
    if desc:
        body += f"\n\n{desc}"
    if item.get("notes"):
        body += f"\n\n**注意**：{item['notes']}"
    return body


def compose(
    question: str,
    intent: str,
    retrieved: list[dict],
    tool_results: list[dict],
    handoff: dict | None = None,
) -> str:
    tool_map = {item["name"]: item["result"] for item in tool_results}
    chunks = [item for item in retrieved if item.get("score", 0) >= 0.08]

    if intent == "greeting":
        return (
            f"你好，我是 **{SHOP['name']}** 智能客服。\n\n"
            "我可以帮你：\n"
            "- 查配件价格、品牌、适配车型与库存规则\n"
            "- 解读 7 天无理由退货、质量问题退换与运费承担\n"
            "- 查询保修期限、安装难度和更换周期\n"
            "- 测算退款金额（支持满减、包邮运费扣减）\n\n"
            "直接说车型 + 配件，例如「宝马3系 前刹车片多少钱」，或点下面的快捷问题。"
        )

    if intent == "thanks":
        return "不客气～ 如果还需要确认适配或售后，随时把车型、商品编号发我。"

    if intent == "handoff" or (handoff and intent == "handoff"):
        contact = (handoff or {}).get("contact", {})
        return (
            "好的，已为你记录转人工需求。\n\n"
            f"- 客服微信：**{contact.get('wechat', SHOP['wechat'])}**\n"
            f"- 客服电话：**{contact.get('phone', SHOP['phone'])}**\n"
            f"- 售后邮箱：{contact.get('email', SHOP['email'])}\n"
            f"- 服务时间：{contact.get('hours', SHOP['service_time'])}\n\n"
            "工作时间人工响应约 30 分钟内；非工作时间留言，次日 09:00 优先处理。"
        )

    if intent == "product":
        result = tool_map.get("search_products") or {}
        cards = result.get("products") or []
        if len(cards) == 1:
            item = get_product(cards[0]["id"])
            if item:
                return f"**{item['name']}** ｜ {_fmt_price(item.get('price'))}\n\n{_product_detail(item)}" + _source_line(chunks)
        if result.get("found") == 0:
            return result.get("summary", "没有找到符合条件的商品，可以放宽预算或联系客服帮你选型。")
        if cards:
            lines = [f"按你的条件找到 **{len(cards)}** 件商品：\n"]
            for idx, card in enumerate(cards, 1):
                lines.append(
                    f"{idx}. **{card['name']}** — {_fmt_price(card.get('price'))}\n"
                    f"   {card.get('brand', '')} ｜ 适配：{card.get('fitment', '')}"
                )
            lines.append("\n想看某一件的详细参数、适配确认或安装难度，把商品名告诉我就行。")
            return "\n".join(lines) + _source_line(chunks)
        if chunks:
            return _bullets(_pick_blocks(question, chunks)) + _source_line(chunks)

    if intent == "return":
        refund = tool_map.get("calculate_refund")
        if refund and refund.get("refund") is not None:
            detail = "\n".join(f"- {line}" for line in refund.get("details", []))
            return (
                f"**{refund['title']} · 退款测算**\n\n{detail}\n\n"
                f"**预计退款：¥{refund['refund']:.2f}**\n\n"
                f"> 依据：退货政策 {refund.get('policy', '')}。实际金额以仓库验收结果为准。"
            )
        if refund and refund.get("needs_input"):
            return refund["summary"]

        if any(word in question for word in ("质量", "坏了", "故障", "损坏", "漏油", "不工作")):
            return (
                "**质量问题退货（已安装也可申请）**\n\n"
                "**认定范围**：功能失效、性能不达标、材质缺陷（砂眼/裂纹/脱层）、尺寸不符、"
                "配件缺失、标识错误。\n\n"
                "**处理方式**\n"
                "| 方式 | 适用情形 | 时效 |\n"
                "| --- | --- | --- |\n"
                "| 全额退款 | 功能性故障、材质缺陷、严重外观瑕疵 | 验收后 24 小时内退款（含运费） |\n"
                "| 换新 | 仍需要该商品 | 收到故障品后 48 小时内发出 |\n"
                "| 先发后退 | 急用且商品价值 ≤¥500 | 先发新品，7 天内寄回旧件 |\n"
                "| 部分退款 | 轻微瑕疵且买家接受 | 协商补偿售价的 10%-30% |\n\n"
                "**费用承担**：退回运费、原订单运费、上门取件费、检测费均由卖家承担；"
                "已产生的安装工时费可凭票据协商补偿（通常不超过 ¥100）。\n\n"
                "**需要提供**：订单号、故障描述，以及故障部位的清晰照片或视频。"
                "人为损坏、安装不当、正常磨损不属于质量问题。\n\n"
                "> 依据：退货政策 第七条、第九条、第十条、第十九条"
            )

        if any(word in question for word in ("运费", "费用", "谁承担", "谁出", "扣钱", "扣运费")):
            return (
                "**退货 / 换货运费规则**\n\n"
                f"{SHIPPING_RULES}\n\n"
                "- 无理由退货：寄回运费由买家承担；原订单包邮的，退款时扣回发货实际运费\n"
                "- 选择平台上门取件：上门取件服务费由买家承担\n"
                "- 示例：商品 ¥200 包邮、实际发货运费 ¥15，退款 ¥185；寄回运费 ¥18 由买家另付\n\n"
                "> 依据：退货政策 第六条、第十条、第十三条"
            )

        if any(word in question for word in ("流程", "怎么退", "如何退", "我要退", "想退", "办理", "手续")):
            return _return_guide()

        blocks = _pick_blocks(question, chunks, limit=7)
        if blocks:
            head = "根据店铺退货政策，整理如下：\n\n"
            return head + _bullets(blocks) + _source_line(chunks)

    if intent == "warranty":
        warranty = tool_map.get("check_warranty")
        if warranty:
            details = "\n".join(f"- {line}" for line in warranty.get("details", []))
            extra = (
                "\n\n**以下情况不在保修范围**：安装不当、事故碰撞、私自改装、正常磨损、"
                "自行拆修，以及外观划痕等不影响使用的瑕疵。"
            )
            return f"**{warranty['name']} 保修说明**\n\n{details}{extra}\n\n> 依据：常见问题解答 FAQ · Q17 保修政策"

    if intent == "install":
        guide = tool_map.get("install_guide")
        if guide:
            details = "\n".join(f"- {line}" for line in guide.get("details", []))
            return f"**{guide['name']} 安装指引**\n\n{details}\n\n> 依据：常见问题解答 FAQ · Q13 安装难度"

    if intent == "tech":
        cycle = tool_map.get("maintenance_cycle")
        if cycle and cycle.get("table"):
            intro = "" if cycle.get("rows") else "没有单独匹配到该配件，下面是全店常用配件更换周期表。\n\n"
            return (
                f"**建议更换周期**\n\n{intro}{cycle['table']}\n\n"
                "> 依据：常见问题解答 FAQ · 附录 常用配件更换周期参考表"
            )

    blocks = _pick_blocks(question, chunks, limit=6)
    if blocks:
        return _bullets(blocks) + _source_line(chunks)

    return (
        "这个问题我在现有的《全店商品明细说明书》《退货政策》《FAQ》里没有找到确切依据，"
        "为避免给你错误信息，我先不乱猜。\n\n"
        "你可以补充 **车型 / 年款 / 商品编号**，或直接联系人工：\n"
        f"- 客服微信：**{SHOP['wechat']}**\n"
        f"- 客服电话：**{SHOP['phone']}**（{SHOP['service_time']}）"
    )
