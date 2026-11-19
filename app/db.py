"""MySQL 持久化层：商品、会话、消息、反馈。

设计原则：
- MySQL 不是强依赖，连接失败时自动降级为内存 + JSONL，服务仍可启动；
- 所有业务表由启动时自动建库建表，无需手工导入 SQL；
- 商品数据启动时从 JSON 全量同步，保证与知识库一致。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

import pymysql
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    create_engine,
    desc,
    func,
    select,
    text,
)
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.engine import Engine

from .config import (
    MYSQL_DATABASE,
    MYSQL_ENABLED,
    MYSQL_HOST,
    MYSQL_PASSWORD,
    MYSQL_PORT,
    MYSQL_USER,
)
from .rag.knowledge import load_products

logger = logging.getLogger(__name__)

metadata = MetaData()

products_table = Table(
    "products",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("name", String(160), nullable=False),
    Column("category", String(64), default=""),
    Column("system", String(64), default=""),
    Column("brand", String(64), default=""),
    Column("price", Numeric(10, 2), default=0),
    Column("price_text", String(32), default=""),
    Column("fitment", String(255), default=""),
    Column("stock", Integer, default=0),
    Column("warranty", String(160), default=""),
    Column("description", Text),
    Column("fields", JSON),
    Column("created_at", DateTime, server_default=func.now()),
    Column("updated_at", DateTime, server_default=func.now(), onupdate=func.now()),
    mysql_charset="utf8mb4",
)

chat_sessions = Table(
    "chat_sessions",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("title", String(200), default=""),
    Column("created_at", DateTime, server_default=func.now()),
    Column("updated_at", DateTime, server_default=func.now(), onupdate=func.now()),
    mysql_charset="utf8mb4",
)

chat_messages = Table(
    "chat_messages",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "session_id",
        String(64),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("role", Enum("user", "assistant"), nullable=False),
    Column("content", Text, nullable=False),
    Column("intent", String(32), default=""),
    Column("mode", String(32), default=""),
    Column("latency_ms", Integer, default=0),
    Column("cards", JSON),
    Column("citations", JSON),
    Column("created_at", DateTime, server_default=func.now()),
    Index("idx_messages_session", "session_id", "id"),
    mysql_charset="utf8mb4",
)

feedback_table = Table(
    "feedback",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String(64), default=""),
    Column("message_id", String(64), default=""),
    Column("rating", Enum("up", "down"), nullable=False),
    Column("comment", String(500), default=""),
    Column("created_at", DateTime, server_default=func.now()),
    Index("idx_feedback_session", "session_id"),
    mysql_charset="utf8mb4",
)

_lock = threading.Lock()
_engine: Engine | None = None
_ready = False
_error = ""
_version = ""
_product_count = 0


def _database_name() -> str:
    name = "".join(ch for ch in MYSQL_DATABASE if ch.isalnum() or ch == "_")
    return name or "nante"


def _server_url(database: str | None = None) -> str:
    user = quote_plus(MYSQL_USER)
    password = quote_plus(MYSQL_PASSWORD)
    db = f"/{database}" if database else "/"
    return (
        f"mysql+pymysql://{user}:{password}@{MYSQL_HOST}:{MYSQL_PORT}{db}"
        "?charset=utf8mb4"
    )


def _ensure_database() -> None:
    conn = pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        charset="utf8mb4",
        connect_timeout=5,
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{_database_name()}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    except pymysql.err.OperationalError as exc:
        # 受限账号可能没有 CREATE DATABASE 权限；只要库已存在即可继续
        if not exc.args or exc.args[0] != 1044:
            raise
        logger.info("当前账号无建库权限，将直接使用已存在的数据库 %s", _database_name())
    finally:
        conn.close()


def init_db(seed_products: bool = True) -> bool:
    """建库建表并同步商品。失败时返回 False，不阻塞应用启动。"""
    global _engine, _ready, _error, _version, _product_count

    with _lock:
        if _ready and _engine is not None:
            return True
        if not MYSQL_ENABLED:
            _ready = False
            _error = "MYSQL_ENABLED=0"
            logger.info("MySQL 已关闭，使用本地回退存储")
            return False

        try:
            _ensure_database()
            engine = create_engine(
                _server_url(_database_name()),
                pool_pre_ping=True,
                pool_recycle=1800,
                pool_size=5,
                max_overflow=5,
                connect_args={"connect_timeout": 5},
            )
            with engine.begin() as conn:
                conn.execute(text("SELECT 1"))
                metadata.create_all(conn)
                _version = str(conn.execute(text("SELECT VERSION()")).scalar() or "")
            _engine = engine
            _ready = True
            _error = ""
            logger.info(
                "MySQL 已连接 | %s:%s/%s | %s",
                MYSQL_HOST,
                MYSQL_PORT,
                _database_name(),
                _version,
            )
            if seed_products:
                _product_count = sync_products()
            return True
        except Exception as exc:  # noqa: BLE001
            _ready = False
            _engine = None
            _error = str(exc)[:300]
            logger.warning(
                "MySQL 连接失败，已降级为本地存储（%s）。请检查 .env 中的 MYSQL_* 配置。",
                _error,
            )
            return False


def is_ready() -> bool:
    return _ready and _engine is not None


def db_health() -> dict[str, Any]:
    return {
        "enabled": MYSQL_ENABLED,
        "connected": is_ready(),
        "database": _database_name(),
        "host": f"{MYSQL_HOST}:{MYSQL_PORT}",
        "version": _version,
        "products": _product_count,
        "error": _error,
    }


def _engine_or_none() -> Engine | None:
    return _engine if is_ready() else None


def sync_products() -> int:
    """把 JSON 商品全量 upsert 到 MySQL，返回商品数。"""
    engine = _engine_or_none()
    if engine is None:
        return 0
    items = load_products()
    if not items:
        return 0
    rows = []
    for item in items:
        rows.append(
            {
                "id": item.get("id", ""),
                "name": item.get("name", ""),
                "category": item.get("category", ""),
                "system": item.get("system", ""),
                "brand": item.get("brand", ""),
                "price": float(item.get("price") or 0),
                "price_text": item.get("price_text", ""),
                "fitment": item.get("fitment", ""),
                "stock": int(item.get("stock") or 0),
                "warranty": item.get("warranty", ""),
                "description": item.get("description", ""),
                "fields": item.get("fields") or {},
                "updated_at": datetime.now(),
            }
        )
    stmt = mysql_insert(products_table).values(rows)
    update_cols = {
        col.name: stmt.inserted[col.name]
        for col in products_table.columns
        if col.name not in {"id", "created_at"}
    }
    with engine.begin() as conn:
        conn.execute(stmt.on_duplicate_key_update(**update_cols))
    return len(rows)


def ensure_session(session_id: str, title: str = "") -> None:
    engine = _engine_or_none()
    if engine is None or not session_id:
        return
    try:
        with engine.begin() as conn:
            conn.execute(
                mysql_insert(chat_sessions)
                .prefix_with("IGNORE")
                .values(id=session_id, title=(title or "新会话")[:200])
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("创建会话失败：%s", exc)


def append_message(
    session_id: str,
    role: str,
    content: str,
    *,
    intent: str = "",
    mode: str = "",
    latency_ms: int = 0,
    cards: list[dict] | None = None,
    citations: list[dict] | None = None,
) -> None:
    engine = _engine_or_none()
    if engine is None or not session_id:
        return
    try:
        with engine.begin() as conn:
            conn.execute(
                mysql_insert(chat_sessions)
                .prefix_with("IGNORE")
                .values(
                    id=session_id,
                    title=(content if role == "user" else "新会话")[:200] or "新会话",
                )
            )
            conn.execute(
                chat_messages.insert().values(
                    session_id=session_id,
                    role=role,
                    content=content,
                    intent=intent,
                    mode=mode,
                    latency_ms=latency_ms,
                    cards=cards or [],
                    citations=citations or [],
                )
            )
            conn.execute(
                chat_sessions.update()
                .where(chat_sessions.c.id == session_id)
                .values(updated_at=func.now())
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("写入会话消息失败：%s", exc)


def get_history(session_id: str, limit: int = 50) -> list[dict[str, Any]]:
    engine = _engine_or_none()
    if engine is None or not session_id:
        return []
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                select(chat_messages)
                .where(chat_messages.c.session_id == session_id)
                .order_by(desc(chat_messages.c.id))
                .limit(limit)
            ).mappings()
            items = list(rows)[::-1]
        return [
            {
                "role": row["role"],
                "content": row["content"],
                "time": row["created_at"].isoformat(sep=" ", timespec="seconds")
                if row["created_at"]
                else "",
                "intent": row["intent"] or "",
                "mode": row["mode"] or "",
                "cards": row["cards"] or [],
                "citations": row["citations"] or [],
            }
            for row in items
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取会话历史失败：%s", exc)
        return []


def clear_session(session_id: str) -> None:
    engine = _engine_or_none()
    if engine is None or not session_id:
        return
    try:
        with engine.begin() as conn:
            # 先删消息，兼容未成功创建外键的旧库
            conn.execute(chat_messages.delete().where(chat_messages.c.session_id == session_id))
            conn.execute(chat_sessions.delete().where(chat_sessions.c.id == session_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("清空会话失败：%s", exc)


def save_feedback(
    session_id: str,
    rating: str,
    comment: str = "",
    message_id: str = "",
) -> bool:
    engine = _engine_or_none()
    if engine is None:
        return False
    try:
        with engine.begin() as conn:
            conn.execute(
                feedback_table.insert().values(
                    session_id=session_id or "",
                    message_id=message_id or "",
                    rating=rating,
                    comment=(comment or "")[:500],
                )
            )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("写入反馈失败：%s", exc)
        return False


def list_sessions(limit: int = 20) -> list[dict[str, Any]]:
    engine = _engine_or_none()
    if engine is None:
        return []
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                select(
                    chat_sessions.c.id,
                    chat_sessions.c.title,
                    chat_sessions.c.created_at,
                    chat_sessions.c.updated_at,
                    func.count(chat_messages.c.id).label("message_count"),
                )
                .select_from(
                    chat_sessions.outerjoin(
                        chat_messages, chat_messages.c.session_id == chat_sessions.c.id
                    )
                )
                .group_by(chat_sessions.c.id)
                .order_by(desc(chat_sessions.c.updated_at))
                .limit(limit)
            ).mappings()
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "messages": int(row["message_count"] or 0),
                "updated_at": row["updated_at"].isoformat(sep=" ", timespec="seconds")
                if row["updated_at"]
                else "",
            }
            for row in rows
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取会话列表失败：%s", exc)
        return []
