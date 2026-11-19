"""端到端冒烟测试：python -m pytest tests -q"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["MYSQL_ENABLED"] = "0"  # 单元测试不依赖本机 MySQL

import pytest
from fastapi.testclient import TestClient

from app.agent.tools import calculate_refund, check_warranty
from app.api import app
from app.llm import LLMCredentials, normalize_credentials


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health_and_index(client):
    data = client.get("/api/health").json()
    assert data["status"] == "ok"
    assert data["vector_store"]["vectors"] > 80


def test_meta_products(client):
    meta = client.get("/api/meta").json()
    assert meta["products_count"] == 30
    names = [item["name"] for item in meta["categories"]]
    assert "制动系统" in names
    assert all("共" not in name for name in names)


def test_product_detail(client):
    item = client.get("/api/products/YB-BP-006").json()
    assert item["price"] == 298.0
    assert "宝马3系" in item["fitment"]


def test_chat_product(client):
    response = client.post("/api/chat", json={"message": "宝马3系 前刹车片多少钱？"})
    assert response.status_code == 200
    data = response.json()
    assert "陶瓷刹车片" in data["answer"]
    assert "298" in data["answer"]
    assert len(data["cards"]) == 1


def test_chat_refund_calculation(client):
    response = client.post(
        "/api/chat",
        json={"message": "刹车片200元，订单满减50，订单总额500，退货退多少？", "session_id": "refund-test"},
    )
    data = response.json()
    assert "180" in data["answer"]


def test_refund_rules():
    assert calculate_refund(item_price=200, order_total=500, discount=50)["refund"] == 180.0
    assert calculate_refund(item_price=200, shipping_fee=15)["refund"] == 185.0
    assert calculate_refund(item_price=200, shipping_fee=15, reason="quality")["refund"] == 200.0


def test_warranty_rule():
    assert "无保修" in check_warranty("刹车片")["period"]
    assert "1年或3万公里" in check_warranty("发电机")["period"]
    assert "1年或3万公里" in check_warranty("火花塞")["period"]


def test_chat_policy_and_multi_turn(client):
    first = client.post(
        "/api/chat", json={"message": "刹车片保修多久？", "session_id": "multi-turn"}
    ).json()
    assert "无保修" in first["answer"]

    second = client.post(
        "/api/chat", json={"message": "这个能自己安装吗？", "session_id": "multi-turn"}
    ).json()
    assert "陶瓷刹车片" in second["answer"]
    assert "安装" in second["answer"]


def test_chat_shipping_policy(client):
    data = client.post("/api/chat", json={"message": "七天无理由退货运费谁承担？"}).json()
    assert "买家承担" in data["answer"]


def test_chat_handoff(client):
    data = client.post("/api/chat", json={"message": "我要投诉，转人工"}).json()
    assert "400-888-8888" in data["answer"]


def test_unknown_question_has_fallback(client):
    data = client.post("/api/chat", json={"message": "你们支持以旧换新吗？"}).json()
    assert data["answer"]
    assert "人工" in data["answer"] or "客服" in data["answer"]


# ------------------------- 模型凭据相关 -------------------------
def test_llm_defaults_has_no_secret(client):
    data = client.get("/api/llm/defaults").json()
    assert data["base_url"].startswith("http")
    assert data["model"]
    # 默认值接口绝不能回显密钥
    assert "api_key" not in data


def test_credentials_normalization():
    cred = normalize_credentials("https://api.deepseek.com/v1/", "sk-test-12345678", "deepseek-chat")
    assert cred is not None
    assert cred.base_url == "https://api.deepseek.com/v1"  # 去掉末尾斜杠
    assert cred.complete is True


def test_credentials_missing_returns_none():
    assert normalize_credentials("", "sk-xxx") is None
    assert normalize_credentials("https://api.x.com", "") is None
    assert normalize_credentials(None, None) is None


def test_credentials_reject_bad_url():
    with pytest.raises(ValueError):
        normalize_credentials("api.deepseek.com", "sk-xxx")


def test_credentials_masked_never_exposes_key():
    cred = LLMCredentials(base_url="https://api.x.com", api_key="fake-key-for-masking-test")
    view = cred.masked()
    assert "masking-test" not in str(view)
    assert view["api_key"].startswith("****")


def test_chat_without_cred_falls_back_to_local(client):
    data = client.post("/api/chat", json={"message": "七天无理由退货运费谁承担？"}).json()
    assert data["mode"] == "local"
    assert data["answer"]


def test_chat_with_bad_key_does_not_break(client):
    """填了错的 key 也不能 500，要降级为本地回答并标记 local-fallback。"""
    data = client.post(
        "/api/chat",
        json={
            "message": "刹车片保修多久？",
            "base_url": "http://127.0.0.1:9/v1",
            "api_key": "sk-invalid-for-test",
        },
    ).json()
    assert data["answer"]
    assert data["mode"] in {"local-fallback", "local"}


def test_llm_test_endpoint_rejects_empty(client):
    data = client.post("/api/llm/test", json={"base_url": "", "api_key": ""}).json()
    assert data["ok"] is False
    assert data["error"]

