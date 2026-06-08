"""Small OpenAI-compatible LLM client used by Frontier Review."""
from __future__ import annotations

import json
import asyncio
from typing import AsyncIterator, Protocol
from urllib.parse import urlparse

import httpx

from .config import settings


class LLMError(Exception):
    pass


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
                async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
                    async with client.stream("POST", f"{self.base_url}/chat/completions", json=body, headers=headers) as res:
                        if res.status_code != 200:
                            detail = (await res.aread()).decode("utf-8", errors="ignore")[:400]
                            error = LLMError(f"LLM API 错误 {res.status_code}: {detail}")
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
                last_error = LLMError(f"LLM 服务不可达: {exc}")
                if attempt < self.max_retries:
                    await _sleep_before_retry(attempt)
                    continue
                raise last_error from exc
        if last_error:
            raise last_error

    async def complete(self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 2048, json_mode: bool = False) -> str:
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
                async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
                    res = await client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
                if res.status_code != 200:
                    error = LLMError(f"LLM API 错误 {res.status_code}: {res.text[:400]}")
                    if _should_retry_llm_error(res.status_code, res.text) and attempt < self.max_retries:
                        last_error = error
                        await _sleep_before_retry(attempt)
                        continue
                    raise error
                data = res.json()
                return str(data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
            except httpx.HTTPError as exc:
                last_error = LLMError(f"LLM 服务不可达: {exc}")
                if attempt < self.max_retries:
                    await _sleep_before_retry(attempt)
                    continue
                raise last_error from exc
            except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                raise LLMError(f"LLM 返回格式异常: {exc}") from exc
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
