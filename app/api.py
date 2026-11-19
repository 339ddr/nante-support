"""FastAPI 服务：RAG 问答、商品查询、会话管理。"""
from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent.graph import get_agent
from .config import BASE_DIR, CHUNKS_FILE, COLLECTION_NAME, DEFAULT_BASE_URL, DEFAULT_MODEL, SHOP
from .db import (
    append_message,
    clear_session as db_clear_session,
    db_health,
    ensure_session,
    get_history as db_history,
    init_db,
    list_sessions,
    save_feedback,
)
from .llm import normalize_credentials, test_connection
from .rag.knowledge import category_stats, load_products
from .rag.store import get_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
FEEDBACK_FILE = BASE_DIR / "data" / "feedback.jsonl"

STARTED_AT = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not CHUNKS_FILE.exists():
        logger.info("未检测到索引，开始构建知识库……")
        from .rag.build import build_index

        build_index()
    get_store()
    get_agent()
    init_db()
    logger.info(
        "南特金属智能客服已就绪 | 模型凭据=%s | MySQL=%s",
        "由网页弹窗提供",
        "on" if db_health()["connected"] else "off(fallback)",
    )
    yield


app = FastAPI(
    title="南特金属智能客服 API",
    description="基于 Chroma 混合检索 + LangGraph Agent 的汽配客服系统",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000, description="用户问题")
    session_id: str | None = Field(None, description="会话 ID，为空则自动创建")
    base_url: str | None = Field(None, description="OpenAI 兼容接口地址，由前端弹窗提供")
    api_key: str | None = Field(None, description="模型 API Key，由前端弹窗提供，仅本次请求使用")
    model: str | None = Field(None, description="模型名称")


class CredentialRequest(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class FeedbackRequest(BaseModel):
    session_id: str
    message_id: str | None = None
    rating: str = Field(..., pattern="^(up|down)$")
    comment: str = ""


class ResetRequest(BaseModel):
    session_id: str


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
def health():
    store = get_store()
    return {
        "status": "ok",
        "vector_store": {"collection": COLLECTION_NAME, "vectors": store.count()},
        "database": db_health(),
        "uptime_seconds": int(time.time() - STARTED_AT),
    }


@app.get("/api/llm/defaults")
def llm_defaults():
    """弹窗预填用的默认值，不含任何密钥。"""
    return {"base_url": DEFAULT_BASE_URL, "model": DEFAULT_MODEL}


@app.post("/api/llm/test")
def llm_test(req: CredentialRequest):
    """校验用户填写的凭据是否可用。key 仅在此次调用中使用，不保存。"""
    try:
        cred = normalize_credentials(req.base_url, req.api_key, req.model)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if cred is None:
        return {"ok": False, "error": "请先填写 base_url 和 API Key"}
    return test_connection(cred)


@app.get("/api/meta")
def meta():
    products = load_products()
    return {
        "shop": SHOP,
        "products_count": len(products),
        "categories": category_stats(products),
        "mode": "local",
        "model": "由网页弹窗配置",
        "database": db_health()["connected"],
        "quick_questions": [
            "宝马3系 前刹车片多少钱？",
            "七天无理由退货运费谁承担？",
            "买的刹车片200元包邮，能退多少钱？",
            "AGM电瓶和普通电瓶能互换吗？",
            "机油滤清器多久换一次？",
        ],
        "service_time": SHOP["service_time"],
    }


@app.get("/api/products")
def list_products(
    q: str = Query("", description="关键词"),
    category: str = Query("", description="系统分类"),
    limit: int = Query(30, ge=1, le=100),
):
    products = load_products()
    items = products
    if category:
        items = [p for p in items if category in f"{p.get('system', '')}{p.get('name', '')}"]
    if q:
        items = [
            p
            for p in items
            if q.lower() in f"{p.get('name', '')}{p.get('brand', '')}{p.get('fitment', '')}{p.get('category', '')}".lower()
        ]
    return {"total": len(items), "items": items[:limit]}


@app.get("/api/products/{product_id}")
def product_detail(product_id: str):
    for item in load_products():
        if item["id"].lower() == product_id.lower():
            return item
    raise HTTPException(status_code=404, detail="商品不存在")


@app.post("/api/chat")
def chat(req: ChatRequest):
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="消息不能为空")
    try:
        cred = normalize_credentials(req.base_url, req.api_key, req.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session_id = req.session_id or uuid.uuid4().hex
    started = time.perf_counter()
    ensure_session(session_id, message[:80])
    # 服务重启后内存状态丢失，从 MySQL 恢复最近几轮上下文
    seed = [] if get_agent().history(session_id) else db_history(session_id, limit=10)
    try:
        result = get_agent().chat(session_id, message, history=seed, cred=cred)
    except Exception as exc:  # noqa: BLE001
        logger.exception("对话处理失败")
        raise HTTPException(status_code=500, detail=f"服务暂时不可用：{exc}") from exc
    result["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    result["timestamp"] = datetime.now().isoformat(timespec="seconds")
    append_message(session_id, "user", message, intent=result.get("intent", ""))
    append_message(
        session_id,
        "assistant",
        result.get("answer", ""),
        intent=result.get("intent", ""),
        mode=result.get("mode", ""),
        latency_ms=int(result.get("latency_ms") or 0),
        cards=result.get("cards", []),
        citations=result.get("citations", []),
    )
    return result


@app.get("/api/session/{session_id}")
def session_history(session_id: str):
    messages = db_history(session_id, limit=50)
    if messages:
        return {"session_id": session_id, "messages": messages, "source": "mysql"}
    history = get_agent().history(session_id)
    return {"session_id": session_id, "messages": history, "source": "memory"}


@app.get("/api/sessions")
def sessions(limit: int = Query(20, ge=1, le=100)):
    items = list_sessions(limit)
    return {"total": len(items), "items": items, "source": "mysql" if db_health()["connected"] else "none"}


@app.post("/api/session/reset")
def reset_session(req: ResetRequest):
    db_clear_session(req.session_id)
    get_agent().reset(req.session_id)
    return {"ok": True, "session_id": req.session_id}


@app.post("/api/feedback")
def feedback(req: FeedbackRequest):
    saved = save_feedback(
        session_id=req.session_id,
        rating=req.rating,
        comment=req.comment,
        message_id=req.message_id or "",
    )
    if not saved:
        FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        record = {"time": datetime.now().isoformat(timespec="seconds"), **req.model_dump()}
        with FEEDBACK_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"ok": True, "storage": "mysql" if saved else "file"}


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
