"""Small OpenAI-compatible LLM client used by Frontier Review."""
from __future__ import annotations

import json
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
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
                async with client.stream("POST", f"{self.base_url}/chat/completions", json=body, headers=headers) as res:
                    if res.status_code != 200:
                        detail = (await res.aread()).decode("utf-8", errors="ignore")[:400]
                        raise LLMError(f"LLM API 错误 {res.status_code}: {detail}")
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
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM 服务不可达: {exc}") from exc

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
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
                res = await client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
            if res.status_code != 200:
                raise LLMError(f"LLM API 错误 {res.status_code}: {res.text[:400]}")
            data = res.json()
            return str(data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM 服务不可达: {exc}") from exc
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"LLM 返回格式异常: {exc}") from exc


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
