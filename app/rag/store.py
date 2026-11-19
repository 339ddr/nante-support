"""向量库 + BM25 混合检索。"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

import chromadb

from ..config import CHROMA_DIR, CHUNKS_FILE, COLLECTION_NAME, IDF_FILE, TOP_K
from .embedder import HashingEmbedder
from .text import content_bigrams, highlight, tokens


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.tokens = [tokens(doc) for doc in docs]
        self.lengths = [len(t) for t in self.tokens]
        self.avg_len = sum(self.lengths) / max(1, len(self.lengths))
        self.df: Counter[str] = Counter()
        for toks in self.tokens:
            self.df.update(set(toks))
        self.n = max(1, len(docs))

    def idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def scores(self, query: str) -> list[float]:
        q = tokens(query)
        results = []
        for toks, length in zip(self.tokens, self.lengths):
            tf = Counter(toks)
            score = 0.0
            for term in q:
                freq = tf.get(term, 0)
                if not freq:
                    continue
                denom = freq + self.k1 * (1 - self.b + self.b * length / max(1e-6, self.avg_len))
                score += self.idf(term) * freq * (self.k1 + 1) / denom
            results.append(score)
        return results


class HybridStore:
    """Chroma 向量召回 + BM25 词法召回，加权融合。"""

    def __init__(self, chunks: list[dict], embedder: HashingEmbedder) -> None:
        self.chunks = chunks
        self.by_id = {c["id"]: c for c in chunks}
        self.embedder = embedder
        self.bm25 = BM25([c["text"] for c in chunks])
        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        self.col = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    # ---------- 索引 ----------
    def count(self) -> int:
        return self.col.count()

    def rebuild(self) -> None:
        try:
            self.client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
        self.col = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        batch_size = 64
        for start in range(0, len(self.chunks), batch_size):
            batch = self.chunks[start : start + batch_size]
            vectors = self.embedder.encode([c["text"] for c in batch]).tolist()
            metadatas = [
                {
                    "doc": c.get("doc", ""),
                    "doc_title": c.get("doc_title", ""),
                    "section": c.get("section", ""),
                    "chapter": c.get("chapter", ""),
                    "kind": c.get("kind", ""),
                    "product_id": c.get("product_id", ""),
                    "category": c.get("category", ""),
                    "brand": c.get("brand", ""),
                    "price": float(c.get("price") or 0.0),
                    "order": int(c.get("order", 0)),
                }
                for c in batch
            ]
            self.col.upsert(
                ids=[c["id"] for c in batch],
                documents=[c["text"] for c in batch],
                embeddings=vectors,
                metadatas=metadatas,
            )

    # ---------- 检索 ----------
    def search(self, query: str, top_k: int = TOP_K, doc: str | None = None) -> list[dict]:
        if not query.strip() or not self.chunks:
            return []
        candidates = [c for c in self.chunks if not doc or c.get("doc") == doc]
        if not candidates:
            return []

        query_vec = self.embedder.encode([query]).tolist()
        where = {"doc": doc} if doc else None
        n = min(max(top_k * 3, 12), max(1, len(candidates)))
        try:
            raw = self.col.query(
                query_embeddings=query_vec,
                n_results=n,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception:
            raw = {"ids": [[]], "distances": [[]]}

        vector_scores: dict[str, float] = {}
        for idx, cid in enumerate(raw["ids"][0] if raw["ids"] else []):
            distance = raw["distances"][0][idx] if raw["distances"] else 1.0
            vector_scores[cid] = max(0.0, 1.0 - float(distance))

        bm25_all = self.bm25.scores(query)
        max_bm25 = max(bm25_all) if bm25_all else 0.0
        bm25_scores = {
            c["id"]: (bm25_all[i] / max_bm25 if max_bm25 > 0 else 0.0)
            for i, c in enumerate(self.chunks)
        }

        qgrams = content_bigrams(query)
        merged = []
        for chunk in candidates:
            cid = chunk["id"]
            vec = vector_scores.get(cid, 0.0)
            lex = bm25_scores.get(cid, 0.0)
            coverage = len(qgrams & content_bigrams(chunk["text"])) / max(1, len(qgrams))
            score = 0.50 * vec + 0.22 * lex + 0.28 * coverage
            if score <= 0:
                continue
            merged.append(
                {
                    **chunk,
                    "score": round(score, 4),
                    "vector_score": round(vec, 4),
                    "bm25_score": round(lex, 4),
                    "lexical_coverage": round(coverage, 4),
                    "snippet": highlight(chunk["text"], query, 200),
                }
            )
        merged.sort(key=lambda x: x["score"], reverse=True)
        return merged[:top_k]


_STORE: HybridStore | None = None


def load_chunks() -> list[dict]:
    if not CHUNKS_FILE.exists():
        return []
    chunks = []
    with CHUNKS_FILE.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def get_store() -> HybridStore:
    global _STORE
    if _STORE is None:
        if not CHUNKS_FILE.exists() or not Path(IDF_FILE).exists():
            from .build import build_index

            build_index(verbose=False)
        chunks = load_chunks()
        embedder = HashingEmbedder.load(Path(IDF_FILE))
        _STORE = HybridStore(chunks, embedder)
        if _STORE.count() != len(chunks):
            _STORE.rebuild()
    return _STORE


def reset_store() -> None:
    global _STORE
    _STORE = None
