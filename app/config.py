"""全局配置：路径、检索参数、店铺信息。

模型凭据不由本文件提供：它在网页弹窗中填写，随每次请求传入（见 app.llm）。
这里只保留与凭据无关的默认值。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "knowledge" / "raw"
MD_DIR = DATA_DIR / "knowledge" / "md"
CHROMA_DIR = DATA_DIR / "chroma"
PRODUCTS_FILE = DATA_DIR / "products.json"
CHUNKS_FILE = DATA_DIR / "chunks.jsonl"
IDF_FILE = DATA_DIR / "idf.json"

load_dotenv(BASE_DIR / ".env")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# ---- 模型默认值（仅作为弹窗的预填建议，不含任何密钥）----
DEFAULT_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").strip().rstrip("/")
DEFAULT_MODEL = os.getenv("LLM_MODEL", "deepseek-chat").strip()

# ---- MySQL 持久化（可选，连接失败自动降级为内存 + JSONL，不阻塞启动）----
MYSQL_ENABLED = os.getenv("MYSQL_ENABLED", "0").strip().lower() not in {"0", "false", "no", "off"}
MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1").strip()
MYSQL_PORT = _env_int("MYSQL_PORT", 3306)
MYSQL_USER = os.getenv("MYSQL_USER", "root").strip()
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "nante").strip()

# ---- 检索配置 ----
COLLECTION_NAME = "nante_auto_parts"
EMBED_DIM = 512
TOP_K = 6
MIN_SCORE = 0.12  # 低于该分数视为知识库未命中

SHOP = {
    "name": "南特金属",
    "slogan": "正品汽配 · 适配无忧",
    "phone": "400-888-8888",
    "wechat": "nante_parts",
    "email": "after-sales@nante.com",
    "service_time": "周一至周五 09:00-21:00 / 周末 10:00-18:00",
}
