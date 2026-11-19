"""客服 Agent 可调用的业务工具（Function Call）。

工具全部是纯函数：输入结构化参数，返回可展示的数据 + 供大模型阅读的摘要。
"""
from __future__ import annotations

import re
from typing import Any

from ..config import MD_DIR, SHOP
from ..rag.knowledge import load_products
from ..rag.text import keyword_overlap

_PRODUCTS_CACHE: list[dict] | None = None


def products() -> list[dict]:
    global _PRODUCTS_CACHE
    if _PRODUCTS_CACHE is None:
        _PRODUCTS_CACHE = load_products()
    return _PRODUCTS_CACHE


# 常见口语叫法 -> 商品编号（按长度倒序匹配，避免“机油”抢走“机油滤清器”）
ALIASES: dict[str, str] = {
    "火花塞": "YB-FS-001",
    "铱金火花塞": "YB-FS-001",
    "全合成机油": "YB-OIL-002",
    "机油滤清器": "YB-FL-003",
    "机滤": "YB-FL-003",
    "机油滤芯": "YB-FL-003",
    "正时皮带": "YB-TB-004",
    "正时皮带套装": "YB-TB-004",
    "节气门清洗剂": "YB-CLN-005",
    "节气门": "YB-CLN-005",
    "陶瓷刹车片": "YB-BP-006",
    "刹车片": "YB-BP-006",
    "刹车盘": "YB-BD-007",
    "打孔划线刹车盘": "YB-BD-007",
    "刹车油": "YB-BF-008",
    "制动液": "YB-BF-008",
    "电子手刹电机": "YB-EPB-009",
    "手刹电机": "YB-EPB-009",
    "真空助力器": "YB-BV-010",
    "刹车助力器": "YB-BV-010",
    "AGM电瓶": "YB-BAT-011",
    "启停电瓶": "YB-BAT-011",
    "电瓶": "YB-BAT-011",
    "蓄电池": "YB-BAT-011",
    "发电机": "YB-GEN-012",
    "启动马达": "YB-STR-013",
    "起动机": "YB-STR-013",
    "LED大灯": "YB-LED-014",
    "LED灯泡": "YB-LED-014",
    "大灯灯泡": "YB-LED-014",
    "灯泡": "YB-LED-014",
    "氧传感器": "YB-O2S-015",
    "减震器": "YB-SHOCK-016",
    "避震器": "YB-SHOCK-016",
    "下摆臂": "YB-CA-017",
    "摆臂": "YB-CA-017",
    "转向拉杆": "YB-TR-018",
    "拉杆球头": "YB-TR-018",
    "外球头": "YB-TR-018",
    "稳定杆连杆": "YB-SL-019",
    "李子串": "YB-SL-019",
    "方向机": "YB-RACK-020",
    "转向机": "YB-RACK-020",
    "冷凝器": "YB-COND-021",
    "空调压缩机": "YB-ACP-022",
    "压缩机": "YB-CP-022",
    "散热器风扇": "YB-FAN-023",
    "电子扇": "YB-FAN-023",
    "车门锁块": "YB-DL-024",
    "锁块": "YB-DL-024",
    "后视镜": "YB-MIR-025",
    "雨刮器": "YB-WIP-026",
    "雨刷": "YB-WIP-026",
    "空调滤清器": "YB-CF-027",
    "空调滤芯": "YB-CF-027",
    "空气滤清器": "YB-AF-028",
    "空气滤芯": "YB-AF-028",
    "空滤": "YB-AF-028",
    "燃油滤清器": "YB-FF-029",
    "汽油滤清器": "YB-FF-029",
    "气门嘴": "YB-TVM-030",
}
_ALIAS_ORDER = sorted(ALIASES, key=len, reverse=True)

BRANDS = [
    "丰田", "本田", "大众", "宝马", "奔驰", "奥迪", "日产", "别克", "雪佛兰", "现代",
    "起亚", "福特", "马自达", "斯柯达", "标致", "雪铁龙", "沃尔沃", "凯迪拉克",
    "雷克萨斯", "特斯拉", "保时捷", "路虎", "捷豹", "MINI", "五菱", "吉利", "比亚迪",
]
MODELS = [
    "卡罗拉", "雷凌", "思域", "迈腾", "帕萨特", "速腾", "高尔夫", "英朗", "科鲁兹",
    "蒙迪欧", "翼虎", "凯美瑞", "RAV4", "轩逸", "骐达", "奇骏", "雅阁", "途观L",
    "探岳", "ix35", "智跑", "桑塔纳", "POLO", "3系", "5系", "A4L", "A6L", "C级",
    "CR-V", "G20", "F10", "W205", "B8", "B9",
]

SYSTEM_EMOJI = {
    "发动机系统": "engine",
    "制动系统": "brake",
    "电气系统": "electric",
    "悬挂转向系统": "suspension",
    "车身附件及空调系统": "body",
    "易损消耗件": "wear",
}


def product_card(item: dict) -> dict:
    return {
        "id": item["id"],
        "name": item["name"],
        "price": item.get("price"),
        "price_text": item.get("price_text"),
        "brand": item.get("brand", ""),
        "category": item.get("category", ""),
        "system": item.get("system", ""),
        "fitment": item.get("fitment", ""),
        "warranty": item.get("warranty", "") or _warranty_text(item.get("category", "")),
        "icon": SYSTEM_EMOJI.get(item.get("system", ""), "part"),
    }


def match_product(text: str) -> dict | None:
    """从自然语言中识别商品，优先最长别名，其次商品全名。"""
    for alias in _ALIAS_ORDER:
        if alias in text:
            return get_product(ALIASES[alias])
    for item in products():
        core = re.sub(r"[（(].*?[）)]", "", item["name"])
        if core and core in text:
            return item
    return None


def get_product(keyword: str) -> dict | None:
    if not keyword:
        return None
    keyword = keyword.strip()
    for item in products():
        if keyword in {item["id"], item["name"]}:
            return item
    for alias, pid in ALIASES.items():
        if alias == keyword or alias in keyword:
            return next((p for p in products() if p["id"] == pid), None)
    best, best_score = None, 0.0
    for item in products():
        haystack = " ".join(
            [item["name"], item.get("brand", ""), item.get("category", ""), item.get("fitment", "")]
        )
        score = keyword_overlap(keyword, haystack)
        if keyword in haystack:
            score += 0.5
        if score > best_score:
            best, best_score = item, score
    return best if best_score >= 0.4 else None


def _car_hint(text: str) -> str:
    found = [b for b in BRANDS if b in text]
    found += [m for m in MODELS if m.lower() in text.lower()]
    return " ".join(found)


def search_products(
    keyword: str = "",
    category: str = "",
    brand: str = "",
    fitment: str = "",
    max_price: float | None = None,
    min_price: float | None = None,
    limit: int = 5,
) -> dict:
    # 命中唯一商品全名/编号时，直接返回该商品，避免推荐一堆不相干配件
    exact = get_product(keyword) if keyword else None
    has_filter = any([category, brand, fitment, max_price is not None, min_price is not None])
    if exact and not has_filter and (keyword.strip() == exact["name"] or keyword.strip().upper() == exact["id"]):
        return {
            "found": 1,
            "products": [product_card(exact)],
            "summary": f"命中商品：{exact['name']}（{exact['id']}）｜{exact.get('price_text', '')}",
        }

    query = f"{keyword} {fitment}".strip()
    car = _car_hint(fitment or keyword)
    # 命中明确别名时只保留对应商品，避免“刹车片”把“刹车油”也带出来
    alias_ids = {pid for alias, pid in ALIASES.items() if keyword and alias in keyword}
    scored: list[tuple[float, dict]] = []
    for item in products():
        if alias_ids and item["id"] not in alias_ids:
            continue
        if category and category not in f"{item.get('system', '')}{item.get('category', '')}{item.get('name', '')}":
            continue
        if brand and brand.lower() not in f"{item.get('brand', '')}{item.get('name', '')}".lower():
            continue
        price = item.get("price") or 0
        if max_price is not None and price > float(max_price):
            continue
        if min_price is not None and price < float(min_price):
            continue

        score = 0.0
        if query:
            haystack = " ".join(
                [
                    item["name"],
                    item.get("brand", ""),
                    item.get("category", ""),
                    item.get("system", ""),
                    item.get("fitment", ""),
                    item.get("description", ""),
                ]
            )
            score += keyword_overlap(query, haystack) * 3
            if keyword and keyword in item["name"]:
                score += 2
            if item["id"] in ALIASES.values() and keyword:
                for alias, pid in ALIASES.items():
                    if pid == item["id"] and alias in keyword:
                        score += 3
                        break
            if car:
                fit_hit = sum(1 for token in car.split() if token.lower() in item.get("fitment", "").lower())
                score += fit_hit * 2
        else:
            score = 1.0
        if category and category in f"{item.get('system', '')}{item.get('category', '')}":
            score += 1.5
        if brand and brand.lower() in item.get("brand", "").lower():
            score += 2
        if score > 0:
            scored.append((score, item))

    scored.sort(key=lambda pair: (-pair[0], pair[1]["index"]))
    top = [item for _, item in scored[: max(1, min(limit, 8))]]
    if not top:
        return {
            "found": 0,
            "products": [],
            "summary": "没有找到符合条件的商品。可以放宽价格或车型条件，或直接联系客服人工选型。",
        }
    lines = [f"按条件找到 {len(top)} 件商品："]
    for item in top:
        lines.append(
            f"- {item['name']}（{item['id']}）｜{item.get('price_text', '')}｜{item.get('brand', '')}"
            f"｜适配：{item.get('fitment', '')}"
        )
    return {"found": len(top), "products": [product_card(i) for i in top], "summary": "\n".join(lines)}


# ------------------------- 退款测算 -------------------------
_MONEY_RE = re.compile(r"(?:¥|￥)?\s*(\d+(?:\.\d+)?)\s*(?:元|块)?")


def extract_money(text: str) -> list[float]:
    values = []
    for raw in _MONEY_RE.findall(text or ""):
        try:
            values.append(float(raw))
        except ValueError:
            continue
    return values


def calculate_refund(
    item_price: float,
    order_total: float | None = None,
    paid_amount: float | None = None,
    discount: float = 0.0,
    shipping_fee: float = 0.0,
    buyer_paid_shipping: bool = False,
    return_shipping: float = 0.0,
    reason: str = "no_reason",
) -> dict:
    """按退货政策测算退款金额。

    reason: no_reason（七天无理由）/ quality（质量问题）/ wrong_item（发错货）
    """
    item_price = float(item_price or 0)
    if item_price <= 0:
        return {
            "needs_input": True,
            "summary": "请提供退货商品的实际成交价（以及订单是否包邮），我就能帮你算出准确退款金额。",
        }

    is_quality = reason in {"quality", "wrong_item"}
    order_total = float(order_total or item_price)
    discount = float(discount or 0)
    shipping_fee = float(shipping_fee or 0)
    return_shipping = float(return_shipping or 0)

    deduction_discount = 0.0
    if discount > 0 and order_total > 0:
        deduction_discount = round(item_price / order_total * discount, 2)

    lines: list[str] = []
    deductions: list[dict] = []

    if is_quality:
        refund = item_price - deduction_discount
        if buyer_paid_shipping and shipping_fee:
            refund += shipping_fee
        title = "质量问题退货"
        lines.append(f"商品实付金额：¥{item_price:.2f}")
        if deduction_discount:
            lines.append(f"按订单占比分摊满减：-¥{deduction_discount:.2f}")
            deductions.append({"name": "满减分摊", "amount": deduction_discount})
        if buyer_paid_shipping and shipping_fee:
            lines.append(f"退还买家支付的发货运费：+¥{shipping_fee:.2f}")
        lines.append("退回运费：由卖家承担（不扣减）")
        if return_shipping:
            lines.append(f"你垫付的退回运费 ¥{return_shipping:.2f} 可凭快递单报销")
    else:
        refund = item_price
        title = "七天无理由退货"
        lines.append(f"商品实付金额：¥{item_price:.2f}")
        if deduction_discount:
            refund -= deduction_discount
            lines.append(f"满减优惠按比例扣回：-¥{deduction_discount:.2f}")
            deductions.append({"name": "满减分摊", "amount": deduction_discount})
        if not buyer_paid_shipping and shipping_fee:
            refund -= shipping_fee
            lines.append(f"原订单包邮，扣回实际发货运费：-¥{shipping_fee:.2f}")
            deductions.append({"name": "原发货运费", "amount": shipping_fee})
        elif not buyer_paid_shipping and not shipping_fee:
            lines.append("提示：若原订单包邮且实际产生发货运费，按政策需从退款中扣回该笔运费")
        lines.append("退回运费：由买家承担（不从未退款中扣除）")

    refund = max(0.0, round(refund, 2))
    lines.append(f"预计退款金额：¥{refund:.2f}")
    if not is_quality:
        lines.append("到账时间：仓库验收通过后 24 小时内，按原支付方式退回")

    return {
        "title": title,
        "refund": refund,
        "deductions": deductions,
        "details": lines,
        "policy": "第六条 / 第十条",
        "summary": f"{title}：预计可退 ¥{refund:.2f}。" + "；".join(lines[:-1]),
    }


# ------------------------- 保修 / 安装 -------------------------
WARRANTY_RULES: dict[str, dict[str, str]] = {
    "engine_core": {
        "label": "发动机/变速箱核心件",
        "period": "2年或6万公里",
        "scope": "材料或制造缺陷导致的失效",
        "note": "正时皮带等橡胶件除外",
    },
    "electric": {
        "label": "电器件（发电机/启动马达/压缩机等）",
        "period": "1年或3万公里",
        "scope": "材料或制造缺陷导致的失效",
        "note": "人为进水、改装不在保",
    },
    "chassis": {
        "label": "底盘件（减震器/摆臂/方向机等）",
        "period": "1年或3万公里",
        "scope": "材料或制造缺陷导致的失效",
        "note": "事故碰撞不在保",
    },
    "sensor": {
        "label": "传感器/执行器",
        "period": "1年或2万公里",
        "scope": "材料或制造缺陷导致的失效",
        "note": "安装不当不在保",
    },
    "wear": {
        "label": "滤清器/刹车片/雨刮等易损件",
        "period": "无保修（发货前验货）",
        "scope": "收到即损可在 24 小时内联系补发",
        "note": "正常磨损不在保",
    },
    "fluid": {
        "label": "油液类（机油/刹车油等）",
        "period": "无保修",
        "scope": "—",
        "note": "开封后不支持退换",
    },
}

_CATEGORY_GROUP = {
    "点火系统": "wear",
    "过滤系统": "wear",
    "制动摩擦片": "wear",
    "清洁系统": "wear",
    "照明系统": "wear",
    "轮胎附件": "wear",
    "车内空气过滤": "wear",
    "进气系统": "wear",
    "燃油系统": "wear",
    "润滑油": "fluid",
    "液压油": "fluid",
    "养护用品": "fluid",
    "蓄电池": "electric",
    "充电系统": "electric",
    "起动系统": "electric",
    "空调系统": "electric",
    "冷却系统": "electric",
    "车身锁闭系统": "electric",
    "外部附件": "electric",
    "排放控制系统": "sensor",
    "驻车制动执行器": "sensor",
    "减震系统": "chassis",
    "悬架控制臂": "chassis",
    "转向系统": "chassis",
    "横向稳定系统": "chassis",
    "转向执行器": "chassis",
    "制动助力装置": "chassis",
    "制动盘": "chassis",
    "配气机构": "engine_core",
}


def _warranty_text(category: str) -> str:
    group = _CATEGORY_GROUP.get(category, "")
    if not group:
        return "见保修政策"
    if group == "wear":
        return "易损件 · 无保修"
    if group == "fluid":
        return "油液 · 开封不退"
    return f"{WARRANTY_RULES[group]['period']}保修"


def check_warranty(product: str = "", category: str = "") -> dict:
    item = get_product(product) if product else None
    cat = category or (item or {}).get("category", "")
    group = _CATEGORY_GROUP.get(cat, "electric")
    rule = dict(WARRANTY_RULES[group])
    explicit = (item or {}).get("warranty", "")
    if explicit:
        rule.update(
            {
                "label": "商品说明书标注",
                "period": explicit,
                "scope": "材料或制造缺陷导致的失效（以商品页标注为准）",
                "note": "易损件是否保修以商品详情页标注为准",
            }
        )
    name = (item or {}).get("name", product or cat or "该商品")
    details = [
        f"商品：{name}",
        f"保修档位：{rule['label']}",
        f"保修期限：{rule['period']}",
        f"保修范围：{rule['scope']}",
        f"特别说明：{rule['note']}",
    ]
    return {
        "product": (item or {}).get("id", ""),
        "name": name,
        "group": group,
        **rule,
        "details": details,
        "summary": f"{name}：{rule['period']}，{rule['scope']}（{rule['note']}）。",
    }


INSTALL_RULES = {
    "easy": {
        "label": "简单（车主可自行更换）",
        "tools": "基本套筒扳手、螺丝刀、尖嘴钳",
        "note": "操作前等车辆冷却，注意不要碰伤周边塑料件。",
    },
    "medium": {
        "label": "中等（建议有经验的车友操作）",
        "tools": "扭力扳手、千斤顶、安全支架、专用拆卸工具",
        "note": "涉及举升车辆时必须使用安全支架，不要只靠千斤顶。",
    },
    "hard": {
        "label": "复杂（强烈建议专业修理厂安装）",
        "tools": "压簧器、真空泵、诊断仪等专用设备",
        "note": "涉及制动、转向、悬挂、正时系统，装错可能影响行车安全，本店不建议自行安装。",
    },
}
_INSTALL_GROUP = {
    "点火系统": "easy",
    "过滤系统": "easy",
    "清洁系统": "easy",
    "照明系统": "easy",
    "轮胎附件": "easy",
    "车内空气过滤": "easy",
    "进气系统": "easy",
    "燃油系统": "medium",
    "制动摩擦片": "medium",
    "蓄电池": "medium",
    "排放控制系统": "medium",
    "横向稳定系统": "medium",
    "转向系统": "medium",
    "润滑油": "medium",
    "液压油": "medium",
    "养护用品": "medium",
    "配气机构": "hard",
    "减震系统": "hard",
    "转向执行器": "hard",
    "空调系统": "hard",
    "充电系统": "hard",
    "起动系统": "hard",
    "制动盘": "hard",
    "悬架控制臂": "hard",
    "制动助力装置": "hard",
    "车身锁闭系统": "medium",
    "外部附件": "medium",
    "驻车制动执行器": "hard",
}


def install_guide(product: str = "", category: str = "") -> dict:
    item = get_product(product) if product else None
    cat = category or (item or {}).get("category", "")
    group = _INSTALL_GROUP.get(cat, "medium")
    rule = INSTALL_RULES[group]
    name = (item or {}).get("name", product or cat or "该配件")
    details = [
        f"商品：{name}",
        f"安装难度：{rule['label']}",
        f"建议工具：{rule['tools']}",
        f"安全提示：{rule['note']}",
    ]
    if group == "medium" and cat in {"转向系统"}:
        details.append("更换后必须做四轮定位。")
    return {"name": name, "level": group, **rule, "details": details, "summary": "\n".join(details)}


_CYCLE_CACHE: list[dict] | None = None


def _maintenance_rows() -> list[dict]:
    global _CYCLE_CACHE
    if _CYCLE_CACHE is not None:
        return _CYCLE_CACHE
    path = MD_DIR / "faq.md"
    rows: list[dict] = []
    if path.exists():
        text = path.read_text(encoding="utf-8")
        marker = "附录：常用配件更换周期参考表"
        block = text.split(marker, 1)[1] if marker in text else ""
        for line in block.split("\n"):
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) < 3 or cells[0] in {"配件名称", "字段"}:
                continue
            if set(cells[0]) <= {"-", " "}:
                continue
            rows.append({"name": cells[0], "cycle": cells[1], "note": cells[2]})
    _CYCLE_CACHE = rows
    return rows


def maintenance_cycle(part: str = "") -> dict:
    rows = _maintenance_rows()
    if not rows:
        return {"rows": [], "table": "", "summary": "未找到更换周期数据"}
    matched = rows
    if part:
        scored = []
        for row in rows:
            score = keyword_overlap(part, row["name"])
            if row["name"] in part or part in row["name"]:
                score += 0.6
            if score > 0:
                scored.append((score, row))
        if scored:
            best = max(score for score, _ in scored)
            matched = [row for score, row in scored if score >= best * 0.9]
        else:
            matched = []

    if not matched:
        return {
            "rows": [],
            "table": "| 配件名称 | 建议更换周期 | 备注 |\n| --- | --- | --- |\n"
            + "\n".join(f"| {r['name']} | {r['cycle']} | {r['note']} |" for r in rows),
            "summary": "没有单独匹配到该配件，下面是全店常用配件更换周期表。",
        }

    table = "| 配件名称 | 建议更换周期 | 备注 |\n| --- | --- | --- |\n" + "\n".join(
        f"| {r['name']} | {r['cycle']} | {r['note']} |" for r in matched
    )
    summary = "；".join(f"{r['name']}建议 {r['cycle']} 更换" for r in matched)
    return {"rows": matched, "table": table, "summary": summary}


def human_handoff(reason: str = "") -> dict:
    return {
        "summary": (
            f"已为你准备转人工：客服微信 {SHOP['wechat']}，电话 {SHOP['phone']}，"
            f"服务时间 {SHOP['service_time']}。"
        ),
        "contact": {
            "phone": SHOP["phone"],
            "wechat": SHOP["wechat"],
            "email": SHOP["email"],
            "hours": SHOP["service_time"],
        },
        "reason": reason,
    }


# ------------------------- Function Call Schema -------------------------
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "按关键词、车型、分类、品牌、价格区间查询店铺商品，返回商品卡片与价格。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "商品关键词，如 刹车片、机油、火花塞"},
                    "category": {"type": "string", "description": "系统分类，如 制动系统、电气系统"},
                    "brand": {"type": "string", "description": "品牌，如 博世、NGK"},
                    "fitment": {"type": "string", "description": "车型或年款，如 宝马3系、大众迈腾B8"},
                    "max_price": {"type": "number", "description": "价格上限（元）"},
                    "min_price": {"type": "number", "description": "价格下限（元）"},
                    "limit": {"type": "integer", "description": "返回条数，默认 5"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_refund",
            "description": "按退货政策计算退款金额，区分七天无理由与质量问题，支持满减分摊和运费扣减。",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_price": {"type": "number", "description": "退货商品实付金额（元）"},
                    "order_total": {"type": "number", "description": "订单商品总额（优惠前）"},
                    "discount": {"type": "number", "description": "订单满减优惠金额"},
                    "shipping_fee": {"type": "number", "description": "原订单实际发货运费"},
                    "buyer_paid_shipping": {"type": "boolean", "description": "买家下单时是否支付了运费"},
                    "return_shipping": {"type": "number", "description": "买家寄回运费"},
                    "reason": {
                        "type": "string",
                        "enum": ["no_reason", "quality", "wrong_item"],
                        "description": "退货原因：七天无理由/质量问题/发错货",
                    },
                },
                "required": ["item_price", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_warranty",
            "description": "查询某商品或某类配件的保修期限、保修范围与除外责任。",
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string", "description": "商品名称或编号"},
                    "category": {"type": "string", "description": "商品品类"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "install_guide",
            "description": "查询配件安装难度、所需工具和安全提示。",
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string", "description": "商品名称或编号"},
                    "category": {"type": "string", "description": "商品品类"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "maintenance_cycle",
            "description": "查询配件建议更换周期（公里数/时间），如机油、刹车片、火花塞、电瓶等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "part": {"type": "string", "description": "配件名称，如 机油滤清器、刹车片"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "human_handoff",
            "description": "当用户要求转人工、投诉，或问题超出知识库范围时调用，返回人工客服联系方式。",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string", "description": "转人工原因"}},
            },
        },
    },
]


def execute_tool(name: str, args: dict) -> dict:
    try:
        if name == "search_products":
            return search_products(**args)
        if name == "calculate_refund":
            return calculate_refund(**args)
        if name == "check_warranty":
            return check_warranty(**args)
        if name == "install_guide":
            return install_guide(**args)
        if name == "maintenance_cycle":
            return maintenance_cycle(**args)
        if name == "human_handoff":
            return human_handoff(**args)
    except TypeError as exc:
        return {"error": f"参数不完整：{exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"工具执行失败：{exc}"}
    return {"error": f"未知工具：{name}"}
