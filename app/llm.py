"""OpenAI 兼容的大模型调用封装。

凭据（base_url / api_key）由前端弹窗提供，随每次请求传入，本模块不读取任何
环境变量、不落盘、不写日志。未提供凭据时调用方应回退到本地检索模式。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT = 45.0


@dataclass(frozen=True)
class LLMCredentials:
    """一次请求所使用的模型凭据。"""

    base_url: str
    api_key: str
    model: str = DEFAULT_MODEL
    temperature: float = 0.2
    timeout: float = DEFAULT_TIMEOUT

    @property
    def complete(self) -> bool:
        return bool(self.base_url and self.api_key)

    def masked(self) -> dict[str, Any]:
        """用于日志/接口回显的脱敏视图，绝不包含完整 key。"""
        key = self.api_key
        tail = key[-4:] if len(key) >= 8 else ""
        return {
            "base_url": self.base_url,
            "model": self.model,
            "api_key": f"****{tail}" if key else "",
        }


def normalize_credentials(
    base_url: str | None,
    api_key: str | None,
    model: str | None = None,
    temperature: float | None = None,
) -> LLMCredentials | None:
    """把前端传来的原始值规整为凭据对象；缺失时返回 None。"""
    url = (base_url or "").strip().rstrip("/")
    key = (api_key or "").strip()
    if not url or not key:
        return None
    if not url.startswith(("http://", "https://")):
        raise ValueError("base_url 必须以 http:// 或 https:// 开头")
    name = (model or "").strip() or DEFAULT_MODEL
    temp = 0.2 if temperature is None else float(temperature)
    return LLMCredentials(base_url=url, api_key=key, model=name, temperature=temp)


# 客户端按 (base_url, api_key) 缓存，避免每轮对话都重新建连接池。
# key 只作为内存中的缓存键，不写入磁盘。
_CLIENTS: dict[tuple[str, str], Any] = {}
_MAX_CLIENTS = 32


def _get_client(cred: LLMCredentials):
    from openai import OpenAI

    cache_key = (cred.base_url, cred.api_key)
    client = _CLIENTS.get(cache_key)
    if client is None:
        if len(_CLIENTS) >= _MAX_CLIENTS:
            _CLIENTS.clear()
        client = OpenAI(api_key=cred.api_key, base_url=cred.base_url, timeout=cred.timeout)
        _CLIENTS[cache_key] = client
    return client


def chat(
    cred: LLMCredentials,
    messages: list[dict[str, Any]],
    tools: list[dict] | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    """返回 assistant message（dict），异常时抛出。"""
    client = _get_client(cred)
    kwargs: dict[str, Any] = {
        "model": cred.model,
        "messages": messages,
        "temperature": cred.temperature if temperature is None else temperature,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    response = client.chat.completions.create(**kwargs)
    message = response.choices[0].message
    return {
        "role": "assistant",
        "content": message.content or "",
        "tool_calls": [
            {
                "id": call.id,
                "name": call.function.name,
                "arguments": call.function.arguments,
            }
            for call in (message.tool_calls or [])
        ],
    }


def test_connection(cred: LLMCredentials) -> dict[str, Any]:
    """用最小代价验证凭据是否可用，供前端「测试连接」按钮调用。"""
    try:
        result = chat(
            cred,
            [{"role": "user", "content": "ping"}],
            temperature=0.0,
        )
        return {"ok": True, "model": cred.model, "reply": (result["content"] or "")[:80]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "model": cred.model, "error": _humanize_error(exc)}


def _humanize_error(exc: Exception) -> str:
    """把 SDK 异常翻译成用户能看懂的提示，且不回显 key。"""
    text = str(exc)
    low = text.lower()
    if "401" in text or "invalid_api_key" in low or "unauthorized" in low:
        return "API Key 无效或已失效，请检查后重试"
    if "404" in text or "not found" in low:
        return "接口地址或模型名不正确，请检查 base_url 与 model"
    if "403" in text or "permission" in low:
        return "该 Key 无权访问此模型，请确认账号权限"
    if "429" in text or "rate limit" in low or "quota" in low:
        return "调用频率或余额不足，请稍后重试"
    if "timeout" in low or "timed out" in low:
        return "连接超时，请检查 base_url 是否可访问"
    if "connection" in low or "network" in low or "ssl" in low:
        return "无法连接到该地址，请检查 base_url 与网络"
    return f"调用失败：{text[:160]}"
