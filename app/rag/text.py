"""轻量中文文本处理：规范化、分词、分句、关键词。无第三方依赖。"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable

_CJK = r"\u4e00-\u9fff"
_ASCII_WORD = re.compile(r"[a-zA-Z]+[a-zA-Z0-9\-\.]*|\d+(?:\.\d+)?")
_CJK_RUN = re.compile(f"[{_CJK}]+")
_NOISE = re.compile(r"[^\w\u4e00-\u9fff\.\-%]+")

STOPWORDS = {
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一", "一个",
    "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好",
    "自己", "这", "那", "什么", "怎么", "怎样", "如何", "可以", "能否", "请问", "一下",
    "吗", "呢", "吧", "啊", "呀", "请问", "帮", "帮我", "你们", "我们", "这个", "那个",
    "还", "请问", "需要", "是否", "以及", "或者", "但是", "因为", "所以",
}

SENTENCE_END = re.compile(r"(?<=[。！？!?；;\n])")


def normalize(text: str) -> str:
    """统一全半角、压缩空白，便于检索与展示。"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_for_index(text: str) -> str:
    text = normalize(text).lower()
    return _NOISE.sub(" ", text)


def words(text: str) -> list[str]:
    """抽取 ASCII 词/数字 + 中文单字，用于精确命中判断。"""
    text = clean_for_index(text)
    tokens = [w for w in _ASCII_WORD.findall(text)]
    for run in _CJK_RUN.findall(text):
        tokens.extend(list(run))
    return [t for t in tokens if t.strip()]


def tokens(text: str) -> list[str]:
    """BM25 分词：英文词 + 中文 1/2/3 元组（高频二元组覆盖商品名词）。"""
    text = clean_for_index(text)
    out: list[str] = []
    out.extend(_ASCII_WORD.findall(text))
    for run in _CJK_RUN.findall(text):
        n = len(run)
        for size, weight in ((1, 1), (2, 2), (3, 2)):
            for i in range(max(0, n - size + 1)):
                gram = run[i : i + size]
                if gram in STOPWORDS:
                    continue
                out.extend([gram] * weight)
    return out


def split_sentences(text: str) -> list[str]:
    """按中文句末标点和换行切句，保留 Markdown 表格行。"""
    parts: list[str] = []
    for line in normalize(text).split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("|"):
            parts.append(line)
            continue
        for seg in SENTENCE_END.split(line):
            seg = seg.strip()
            if seg:
                parts.append(seg)
    return parts


def split_long_text(text: str, max_len: int = 420, overlap: int = 60) -> list[str]:
    """按句子边界把长文本切成带重叠的块。"""
    text = normalize(text)
    if len(text) <= max_len:
        return [text] if text else []
    sents = split_sentences(text)
    chunks: list[str] = []
    buf = ""
    for sent in sents:
        if len(buf) + len(sent) + 1 <= max_len:
            buf = f"{buf}\n{sent}" if buf else sent
            continue
        if buf:
            chunks.append(buf)
            tail = buf[-overlap:] if overlap else ""
            buf = f"{tail}\n{sent}".strip()
        else:
            chunks.append(sent[:max_len])
            buf = sent[max_len - overlap :]
    if buf.strip():
        chunks.append(buf.strip())
    return chunks


def keyword_overlap(query: str, text: str) -> float:
    """查询词覆盖率：用于兜底排序与句子抽取。"""
    q = {w for w in words(query) if w not in STOPWORDS and len(w) > 0}
    if not q:
        return 0.0
    t = set(words(text))
    hit = len(q & t)
    return hit / len(q)


def bigrams(text: str) -> set[str]:
    text = clean_for_index(text)
    grams: set[str] = set()
    for run in _CJK_RUN.findall(text):
        for i in range(len(run) - 1):
            grams.add(run[i : i + 2])
    grams.update(_ASCII_WORD.findall(text))
    return grams


STOP_BIGRAMS = {
    "你们", "我们", "他们", "可以", "支持", "什么", "怎么", "怎样", "如何", "是否",
    "请问", "有吗", "能否", "这个", "那个", "一下", "帮我", "需要", "以及", "或者",
    "但是", "因为", "所以", "有没", "没有", "多少", "多久", "哪些", "介绍", "了解",
}


def content_bigrams(text: str) -> set[str]:
    """去掉“你们/可以/什么”等口语词后的实义词二元组，用于相关性门槛判断。"""
    grams = {g for g in bigrams(text) if g not in STOP_BIGRAMS}
    return {g for g in grams if not all(ch in STOPWORDS for ch in g)}


def highlight(text: str, query: str, max_len: int = 180) -> str:
    """截取与查询最相关的一段文本用于引用展示。"""
    text = normalize(text).replace("\n", " ")
    if len(text) <= max_len:
        return text
    q = bigrams(query)
    best, best_hit = 0, -1
    step = max(1, max_len // 2)
    for start in range(0, max(1, len(text) - max_len + 1), step):
        seg = text[start : start + max_len]
        hit = len(q & bigrams(seg))
        if hit > best_hit:
            best, best_hit = start, hit
    return ("…" if best else "") + text[best : best + max_len] + ("…" if best + max_len < len(text) else "")


def join_unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        item = (item or "").strip()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out
