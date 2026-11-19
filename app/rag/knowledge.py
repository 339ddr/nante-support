"""把三份业务文档解析成：结构化商品库 + 可检索的知识块。"""
from __future__ import annotations

import json
import re

from ..config import MD_DIR, PRODUCTS_FILE
from .text import normalize, split_long_text

DOC_META = {
    "products": ("products.md", "全店商品明细说明书 V2.0"),
    "returns": ("returns.md", "退货政策文档 V2.0"),
    "faq": ("faq.md", "常见问题解答 FAQ V1.0"),
}

_PRICE_RE = re.compile(r"([\d,]+(?:\.\d+)?)")


def _table_fields(lines: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in lines:
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        key, value = cells[0], " | ".join(cells[1:]).strip()
        if key in {"字段", "内容"} or set(key) <= {"-", " "}:
            continue
        fields[key] = value
    return fields


def _price(value: str) -> float | None:
    match = _PRICE_RE.search(value.replace("¥", "").replace(",", ""))
    return float(match.group(1)) if match else None


def _clean_heading(text: str) -> str:
    text = re.sub(r"^[一二三四五六七八九十]+、", "", text).strip()
    text = re.sub(r"[（(]共\d+件[）)]$", "", text).strip()
    return text


def parse_products(md_text: str) -> list[dict]:
    """解析商品说明书中的 30 个商品条目。"""
    lines = normalize(md_text).split("\n")
    system = ""
    products: list[dict] = []
    current: dict | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal current, buffer
        if current is not None:
            fields = _table_fields(buffer)
            raw_price = fields.get("价格", "")
            current.update(
                {
                    "price": _price(raw_price),
                    "price_text": raw_price or "询价",
                    "brand": fields.get("品牌", ""),
                    "category": fields.get("品类", ""),
                    "fitment": fields.get("适用车型", ""),
                    "attrs": fields.get("核心属性", ""),
                    "description": fields.get("商品描述", ""),
                    "package": fields.get("包装", "") or fields.get("套装包含", ""),
                    "warranty": fields.get("保修", ""),
                    "life": fields.get("寿命", ""),
                    "notes": fields.get("注意事项", "") or fields.get("特性", ""),
                    "fields": fields,
                }
            )
            products.append(current)
        current, buffer = None, []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("## "):
            flush()
            system = _clean_heading(stripped[3:])
            if system.startswith("附录"):
                system = ""
            continue
        if stripped.startswith("### "):
            flush()
            title = stripped[4:].strip()
            match = re.match(r"商品\s*(\d+)\s*[：:]\s*(.+)", title)
            if match:
                current = {
                    "index": int(match.group(1)),
                    "name": match.group(2).strip().replace("(", "（").replace(")", "）"),
                    "system": system,
                }
            continue
        if current is not None:
            buffer.append(stripped)
    flush()

    for item in products:
        item.setdefault("id", item.get("fields", {}).get("商品编号", ""))
        item["id"] = item.get("fields", {}).get("商品编号") or f"P{item['index']:03d}"
    return products


def _product_text(item: dict) -> str:
    rows = [
        f"商品：{item['name']}",
        f"商品编号：{item['id']} ｜ 价格：{item.get('price_text', '')} ｜ 品牌：{item.get('brand', '')}",
        f"系统分类：{item.get('system', '')} ｜ 品类：{item.get('category', '')}",
        f"适用车型：{item.get('fitment', '')}",
    ]
    for key in ("核心属性", "商品描述", "套装包含", "包装", "寿命", "保修", "特性", "注意事项"):
        value = item.get("fields", {}).get(key)
        if value:
            rows.append(f"{key}：{value}")
    return "\n".join(rows)


def _split_by_heading(md_text: str, level: str) -> list[dict]:
    """按指定级别标题切段，返回 {title, chapter, body_lines}。

    遇到同级或更高级标题时结束当前段落，避免附录内容被并进最后一条 FAQ / 条款。
    """
    lines = normalize(md_text).split("\n")
    sections: list[dict] = []
    chapter = ""
    current: dict | None = None

    def close() -> None:
        nonlocal current
        if current:
            sections.append(current)
            current = None

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#"):
            if current is not None:
                current["lines"].append(stripped)
            continue

        hashes = len(stripped) - len(stripped.lstrip("#"))
        title = stripped[hashes:].strip()

        if level == "## ":
            # 只收集二级标题（附录表格）
            if hashes == 2:
                close()
                current = {"title": title, "chapter": "", "lines": []}
            elif hashes < 2:
                close()
            elif current is not None:
                current["lines"].append(stripped)
            continue

        if hashes == 2:
            close()
            chapter = title
        elif hashes == 3:
            close()
            current = {"title": title, "chapter": chapter, "lines": []}
        elif hashes < 2:
            close()
        elif current is not None:
            current["lines"].append(stripped)

    close()
    return sections


def build_chunks(md_map: dict[str, str]) -> list[dict]:
    chunks: list[dict] = []
    order = 0

    # ---------- 商品 ----------
    products = parse_products(md_map.get("products", ""))
    for item in products:
        text = _product_text(item)
        pieces = split_long_text(text, max_len=700, overlap=0)
        for idx, piece in enumerate(pieces):
            order += 1
            chunks.append(
                {
                    "id": f"products-{item['id']}-{idx}",
                    "text": piece,
                    "doc": "products",
                    "doc_title": DOC_META["products"][1],
                    "section": item["name"],
                    "chapter": item.get("system", ""),
                    "kind": "product",
                    "product_id": item["id"],
                    "category": item.get("category", ""),
                    "brand": item.get("brand", ""),
                    "price": item.get("price") or 0.0,
                    "order": order,
                }
            )

    # ---------- 退货政策（按条） ----------
    for sec in _split_by_heading(md_map.get("returns", ""), "### "):
        body = "\n".join(sec["lines"])
        if len(body) < 4:
            continue
        head = f"【退货政策】{sec['title']}"
        if sec["chapter"]:
            head += f"（{sec['chapter']}）"
        text = f"{head}\n{body}"
        for idx, piece in enumerate(split_long_text(text, max_len=620, overlap=60)):
            order += 1
            chunks.append(
                {
                    "id": f"returns-{order}-{idx}",
                    "text": piece,
                    "doc": "returns",
                    "doc_title": DOC_META["returns"][1],
                    "section": sec["title"],
                    "chapter": sec["chapter"],
                    "kind": "policy",
                    "order": order,
                }
            )

    # ---------- FAQ（按 Q） ----------
    for sec in _split_by_heading(md_map.get("faq", ""), "### "):
        body = "\n".join(sec["lines"])
        if len(body) < 4:
            continue
        title = re.sub(r"^Q\d+[：:]\s*", "", sec["title"]).strip()
        text = f"【常见问题】{title}\n所属栏目：{sec['chapter']}\n{body}"
        for idx, piece in enumerate(split_long_text(text, max_len=680, overlap=60)):
            order += 1
            chunks.append(
                {
                    "id": f"faq-{order}-{idx}",
                    "text": piece,
                    "doc": "faq",
                    "doc_title": DOC_META["faq"][1],
                    "section": sec["title"],
                    "chapter": sec["chapter"],
                    "kind": "faq",
                    "order": order,
                }
            )

    # ---------- 附录表格 ----------
    for doc in ("products", "returns", "faq"):
        text = md_map.get(doc, "")
        for sec in _split_by_heading(text, "## "):
            if not sec["title"].startswith("附录"):
                continue
            body = "\n".join(sec["lines"])
            if len(body) < 10:
                continue
            chunk_text = f"【{DOC_META[doc][1]}】{sec['title']}\n{body}"
            for idx, piece in enumerate(split_long_text(chunk_text, max_len=900, overlap=0)):
                order += 1
                chunks.append(
                    {
                        "id": f"{doc}-appendix-{idx}",
                        "text": piece,
                        "doc": doc,
                        "doc_title": DOC_META[doc][1],
                        "section": sec["title"],
                        "chapter": "附录",
                        "kind": "table",
                        "order": order,
                    }
                )

    return chunks


def load_markdown() -> dict[str, str]:
    md_map: dict[str, str] = {}
    for key, (filename, _) in DOC_META.items():
        path = MD_DIR / filename
        if not path.exists():
            raise FileNotFoundError(f"缺少知识库文件：{path}")
        md_map[key] = path.read_text(encoding="utf-8")
    return md_map


def build_knowledge() -> tuple[list[dict], list[dict]]:
    md_map = load_markdown()
    products = parse_products(md_map["products"])
    chunks = build_chunks(md_map)
    PRODUCTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PRODUCTS_FILE.write_text(json.dumps(products, ensure_ascii=False, indent=2), encoding="utf-8")
    return products, chunks


def load_products() -> list[dict]:
    if not PRODUCTS_FILE.exists():
        build_knowledge()
    return json.loads(PRODUCTS_FILE.read_text(encoding="utf-8"))


def category_stats(products: list[dict]) -> list[dict]:
    stats: dict[str, dict] = {}
    for item in products:
        system = item.get("system") or "其他"
        bucket = stats.setdefault(system, {"name": system, "count": 0, "min": None, "max": None})
        bucket["count"] += 1
        price = item.get("price")
        if isinstance(price, (int, float)) and price > 0:
            bucket["min"] = price if bucket["min"] is None else min(bucket["min"], price)
            bucket["max"] = price if bucket["max"] is None else max(bucket["max"], price)
    return list(stats.values())
