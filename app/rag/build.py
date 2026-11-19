"""构建离线知识库索引：解析文档 -> 分块 -> 向量化 -> Chroma。"""
from __future__ import annotations

import json
import time

from ..config import CHUNKS_FILE, IDF_FILE
from .embedder import HashingEmbedder
from .knowledge import build_knowledge
from .store import HybridStore


def build_index(verbose: bool = True) -> dict:
    started = time.time()
    products, chunks = build_knowledge()
    if not chunks:
        raise RuntimeError("知识库为空，请检查 data/knowledge/md 下的文档")

    embedder = HashingEmbedder().fit([c["text"] for c in chunks])
    embedder.save(IDF_FILE)

    CHUNKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CHUNKS_FILE.open("w", encoding="utf-8") as fh:
        for chunk in chunks:
            fh.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    store = HybridStore(chunks, embedder)
    store.rebuild()

    stats = {
        "products": len(products),
        "chunks": len(chunks),
        "by_doc": {
            doc: sum(1 for c in chunks if c["doc"] == doc)
            for doc in ("products", "returns", "faq")
        },
        "seconds": round(time.time() - started, 2),
    }
    if verbose:
        print(
            f"[build] 商品 {stats['products']} 件 | 知识块 {stats['chunks']} 条 "
            f"({stats['by_doc']}) | 耗时 {stats['seconds']}s"
        )
    return stats


if __name__ == "__main__":
    build_index()
