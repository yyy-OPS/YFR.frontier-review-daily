"""Small OpenAI-compatible LLM client used by Frontier Review."""
from __future__ import annotations

import json
import asyncio
import socket
from typing import AsyncIterator, Protocol
from urllib.parse import urlparse

import httpx

from .config import settings


class LLMError(Exception):
    pass


def _llm_http_error(status_code: int, detail: str) -> LLMError:
    compact = " ".join((detail or "").split())[:400]
    lowered = compact.lower()
    if status_code in {401, 403} and any(marker in lowered for marker in ("insufficient_balance", "insufficient account balance", "quota", "billing", "余额不足")):
        return LLMError(f"LLM 账户余额不足或额度不可用：请充值或在后台更换可用 API Key 后重试。上游返回 {status_code}: {compact}")
    if status_code in {401, 403} and any(marker in lowered for marker in ("invalid api key", "authentication", "unauthorized", "forbidden", "无效", "认证")):
        return LLMError(f"LLM API Key 无效或上游认证失败：请检查 Base URL、API Key 和模型权限。上游返回 {status_code}: {compact}")
    if status_code == 429:
        return LLMError(f"LLM 请求被限流或额度达到上限：请稍后重试或更换可用模型。上游返回 {status_code}: {compact}")
    return LLMError(f"LLM API error {status_code}: {compact}")


def _llm_network_error(exc: httpx.HTTPError) -> LLMError:
    if isinstance(exc, httpx.TimeoutException):
        timeout = getattr(settings, "llm_timeout_seconds", 900)
        return LLMError(f"LLM 请求超时：后端在 {timeout} 秒内没有完整收到上游响应。长综述建议启用流式聚合或继续增大 LLM_TIMEOUT_SECONDS。")
    if isinstance(exc, httpx.ConnectError):
        cause = exc.__cause__
        if isinstance(cause, socket.gaierror):
            return LLMError(f"LLM DNS resolution failed: {cause}")
        return LLMError(f"LLM connection failed: {exc}")
    if isinstance(exc, httpx.RemoteProtocolError):
        return LLMError(f"LLM upstream connection interrupted: {exc}")
    return LLMError(f"LLM service unreachable: {exc}")


class LLMClient(Protocol):
    def stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]: ...

    async def complete(self, messages: list[dict], **kwargs) -> str: ...


class FakeStreamClient:
    async def stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]:
        yield "未配置 LLM，无法生成真实综述。请在管理员后台配置 OpenAI 兼容模型。"

    async def complete(self, messages: list[dict], **kwargs) -> str:
        parts = [tok async for tok in self.stream(messages, **kwargs)]
        return "".join(parts).strip()


class OpenAICompatibleClient:
    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self.api_key = api_key
        self.base_url = _normalize_openai_base_url(base_url)
        self.model = model
        self.max_retries = int(getattr(settings, "llm_max_retries", 3))
        self.timeout = float(getattr(settings, "llm_timeout_seconds", 900.0))
        self.connect_timeout = float(getattr(settings, "llm_connect_timeout_seconds", 20.0))
        self.complete_use_stream = bool(getattr(settings, "llm_complete_use_stream", True))

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self.timeout, connect=self.connect_timeout)

    async def stream(self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 2048, json_mode: bool = False) -> AsyncIterator[str]:
        body: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last_error: LLMError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout()) as client:
                    async with client.stream("POST", f"{self.base_url}/chat/completions", json=body, headers=headers) as res:
                        if res.status_code != 200:
                            detail = (await res.aread()).decode("utf-8", errors="ignore")[:400]
                            error = _llm_http_error(res.status_code, detail)
                            if _should_retry_llm_error(res.status_code, detail) and attempt < self.max_retries:
                                last_error = error
                                await _sleep_before_retry(attempt)
                                continue
                            raise error
                        async for line in res.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                delta = json.loads(data)["choices"][0]["delta"].get("content")
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue
                            if delta:
                                yield delta
                        return
            except httpx.HTTPError as exc:
                last_error = _llm_network_error(exc)
                if attempt < self.max_retries:
                    await _sleep_before_retry(attempt)
                    continue
                raise last_error from exc
        if last_error:
            raise last_error

    async def complete(self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 2048, json_mode: bool = False) -> str:
        if self.complete_use_stream and not json_mode:
            parts: list[str] = []
            async for token in self.stream(messages, temperature=temperature, max_tokens=max_tokens, json_mode=False):
                parts.append(token)
            return "".join(parts).strip()
        body: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last_error: LLMError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout()) as client:
                    res = await client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
                if res.status_code != 200:
                    error = _llm_http_error(res.status_code, res.text)
                    if _should_retry_llm_error(res.status_code, res.text) and attempt < self.max_retries:
                        last_error = error
                        await _sleep_before_retry(attempt)
                        continue
                    raise error
                data = res.json()
                return str(data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
            except httpx.HTTPError as exc:
                last_error = _llm_network_error(exc)
                if attempt < self.max_retries:
                    await _sleep_before_retry(attempt)
                    continue
                raise last_error from exc
            except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                raise LLMError(f"LLM response parse error: {exc}") from exc
        if last_error:
            raise last_error
        return ""


def get_llm_client(api_key: str | None = None, base_url: str | None = None, model: str | None = None) -> LLMClient:
    key = (api_key or settings.openai_api_key or settings.deepseek_api_key).strip()
    if not key:
        return FakeStreamClient()
    url = (base_url or settings.openai_base_url or settings.deepseek_base_url).strip()
    mdl = (model or settings.openai_model or settings.deepseek_model).strip()
    return OpenAICompatibleClient(key, url, mdl)


def _normalize_openai_base_url(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc and parsed.path in ("", "/"):
        return f"{url}/v1"
    return url


def _should_retry_llm_error(status_code: int, detail: str) -> bool:
    lowered = (detail or "").lower()
    if status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
        return True
    transient_markers = (
        "upstream authentication failed",
        "bad gateway",
        "temporarily unavailable",
        "timeout",
        "rate limit",
        "too many requests",
        "当前 api 不支持所选模型",
    )
    return status_code == 404 and any(marker in lowered for marker in transient_markers)


async def _sleep_before_retry(attempt: int) -> None:
    await asyncio.sleep(min(8.0, 0.8 * (2 ** attempt)))
