"""离线可用的中文字符 n-gram 哈希向量化器。

不依赖 PyTorch / 在线模型下载，使用 char 1~3 gram + IDF + 哈希桶，
在汽配这种专业名词密集的小语料上召回效果稳定，且结果可复现。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

import numpy as np

from ..config import EMBED_DIM
from .text import clean_for_index

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_ASCII = re.compile(r"[a-z]+[a-z0-9\-\.]*|\d+(?:\.\d+)?")
_GRAM_WEIGHTS = {1: 0.6, 2: 1.4, 3: 0.9}


def _grams(text: str) -> list[str]:
    text = clean_for_index(text)
    grams: list[str] = list(_ASCII.findall(text))
    for run in _CJK_RUN.findall(text):
        n = len(run)
        for size in (1, 2, 3):
            for i in range(max(0, n - size + 1)):
                grams.append(run[i : i + size])
    return grams


def _bucket(gram: str, dim: int) -> int:
    digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "little") % dim


class HashingEmbedder:
    """带 IDF 加权的哈希向量器，输出 L2 归一化向量。"""

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self.dim = dim
        self.idf: dict[str, float] = {}
        self.n_docs = 0

    def fit(self, corpus: list[str]) -> "HashingEmbedder":
        df: Counter[str] = Counter()
        for text in corpus:
            df.update(set(_grams(text)))
        self.n_docs = max(1, len(corpus))
        self.idf = {
            gram: math.log((1 + self.n_docs) / (1 + freq)) + 1.0
            for gram, freq in df.items()
        }
        return self

    def encode(self, texts: list[str]) -> np.ndarray:
        matrix = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            grams = _grams(text)
            if not grams:
                continue
            counts = Counter(grams)
            for gram, tf in counts.items():
                weight = _GRAM_WEIGHTS.get(len(gram), 1.0) * self.idf.get(gram, 1.0)
                value = (1.0 + math.log(tf)) * weight
                matrix[row, _bucket(gram, self.dim)] += value
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return matrix / norms

    # ---- 持久化 IDF ----
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"dim": self.dim, "n_docs": self.n_docs, "idf": self.idf}, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path, dim: int = EMBED_DIM) -> "HashingEmbedder":
        obj = cls(dim=dim)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            obj.dim = int(data.get("dim", dim))
            obj.n_docs = int(data.get("n_docs", 0))
            obj.idf = {k: float(v) for k, v in data.get("idf", {}).items()}
        return obj
