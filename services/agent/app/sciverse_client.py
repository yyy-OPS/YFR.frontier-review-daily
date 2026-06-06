"""Sciverse API client for literature search and evidence snippets."""
from __future__ import annotations

from typing import Any

import httpx

from .config import settings


class SciverseClient:
    """Thin async wrapper around Sciverse REST endpoints."""

    def __init__(self, client: httpx.AsyncClient, token: str | None = None) -> None:
        self._c = client
        self._token = (token if token is not None else settings.sciverse_api_token).strip()

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    async def aclose(self) -> None:
        await self._c.aclose()

    def _headers(self) -> dict[str, str]:
        if not self._token:
            return {}
        return {"Authorization": f"Bearer {self._token}"}

    async def health(self) -> bool:
        if not self.enabled:
            return False
        try:
            r = await self._c.get(
                "/meta-catalog",
                params={"include_sample_values": "false"},
                headers=self._headers(),
                timeout=10.0,
            )
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def meta_search(
        self,
        query: str,
        page_size: int,
        *,
        page: int = 1,
        filters: list[dict[str, Any]] | None = None,
        fields: list[str] | None = None,
        freshness_boost: str | bool | None = None,
    ) -> tuple[int, dict | None]:
        payload: dict[str, Any] = {
            "query": query,
            "page": page,
            "page_size": page_size,
            "fields": fields or _DEFAULT_META_FIELDS,
        }
        if filters:
            payload["filters"] = filters
        if freshness_boost is not None:
            payload["freshness_boost"] = "STRONG" if freshness_boost is True else "NONE" if freshness_boost is False else freshness_boost
        return await self._post("/meta-search", payload)

    async def agentic_search(
        self,
        query: str,
        top_k: int,
        *,
        sub_queries: int | None = None,
    ) -> tuple[int, dict | None]:
        payload: dict[str, Any] = {"query": query, "top_k": top_k}
        if sub_queries is not None:
            payload["sub_queries"] = sub_queries
        return await self._post("/agentic-search", payload)

    async def content(
        self,
        doc_id: str,
        *,
        offset: int = 0,
        limit: int = 700,
    ) -> tuple[int, dict | None]:
        try:
            r = await self._c.get(
                "/content",
                params={"doc_id": doc_id, "offset": offset, "limit": limit},
                headers=self._headers(),
                timeout=settings.request_timeout,
            )
        except httpx.HTTPError as exc:
            return 503, {"code": "SCIVERSE_UNAVAILABLE", "message": str(exc)}
        return r.status_code, _safe_json(r)

    async def _post(self, path: str, payload: dict[str, Any]) -> tuple[int, dict | None]:
        if not self.enabled:
            return 503, {"code": "SCIVERSE_NOT_CONFIGURED", "message": "Sciverse API token 未配置"}
        try:
            r = await self._c.post(
                path,
                json=payload,
                headers=self._headers(),
                timeout=settings.request_timeout,
            )
        except httpx.HTTPError as exc:
            return 503, {"code": "SCIVERSE_UNAVAILABLE", "message": str(exc)}
        return r.status_code, _safe_json(r)


def _safe_json(r: httpx.Response) -> dict | None:
    try:
        return r.json()
    except Exception:
        return None


_DEFAULT_META_FIELDS = [
    "title",
    "doi",
    "author",
    "publication_published_year",
    "publication_published_date",
    "publication_venue_name_unified",
    "citation_count",
    "doc_id",
    "unique_id",
    "abstract",
    "access_oa_url",
]
