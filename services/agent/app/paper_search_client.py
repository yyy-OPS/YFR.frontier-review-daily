"""Public scholarly search client compatible with the paper-search MCP sources."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import httpx

from .config import settings

log = logging.getLogger("agent.paper_search")

_SEMANTIC_SCHOLAR_LOCK = asyncio.Lock()
_SEMANTIC_SCHOLAR_LAST_REQUEST = 0.0


def _clean_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _first(value: Any) -> str:
    if isinstance(value, list):
        return _clean_text(value[0]) if value else ""
    return _clean_text(value)


def _doi(value: Any) -> str:
    text = _clean_text(value)
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", text, flags=re.IGNORECASE)


def _abstract_from_openalex(index: Any) -> str:
    if not isinstance(index, dict):
        return ""
    words: list[tuple[int, str]] = []
    for word, positions in index.items():
        if not isinstance(positions, list):
            continue
        for pos in positions:
            try:
                words.append((int(pos), str(word)))
            except (TypeError, ValueError):
                continue
    return " ".join(word for _, word in sorted(words))


def _year_from_date(value: Any) -> int | None:
    text = _clean_text(value)
    match = re.search(r"\b(19|20)\d{2}\b", text)
    return int(match.group(0)) if match else None


async def _semantic_scholar_wait_turn() -> None:
    """Keep Semantic Scholar requests below the approved 1 request/second key limit."""
    global _SEMANTIC_SCHOLAR_LAST_REQUEST
    interval = max(1.0, float(settings.semantic_scholar_min_interval_seconds or 1.15))
    async with _SEMANTIC_SCHOLAR_LOCK:
        elapsed = time.monotonic() - _SEMANTIC_SCHOLAR_LAST_REQUEST
        if elapsed < interval:
            await asyncio.sleep(interval - elapsed)
        _SEMANTIC_SCHOLAR_LAST_REQUEST = time.monotonic()


class PaperSearchClient:
    """Search deployable public scholarly APIs aligned with paper-search MCP sources."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=settings.request_timeout)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def search_source(self, source: str, query: str, *, limit: int, since_year: int) -> list[dict[str, Any]]:
        source = source.strip().lower()
        if source == "semantic":
            return await self._search_semantic(query, limit=limit, since_year=since_year)
        if source == "openalex":
            return await self._search_openalex(query, limit=limit, since_year=since_year)
        if source == "crossref":
            return await self._search_crossref(query, limit=limit, since_year=since_year)
        if source == "europepmc":
            return await self._search_europepmc(query, limit=limit, since_year=since_year)
        if source == "hal":
            return await self._search_hal(query, limit=limit, since_year=since_year)
        if source == "base":
            return await self._search_base(query, limit=limit, since_year=since_year)
        if source == "core":
            return await self._search_core(query, limit=limit, since_year=since_year)
        if source == "unpaywall":
            return await self._search_unpaywall(query, limit=limit, since_year=since_year)
        return []

    async def _search_semantic(self, query: str, *, limit: int, since_year: int) -> list[dict[str, Any]]:
        headers: dict[str, str] = {}
        if settings.semantic_scholar_api_key:
            headers["x-api-key"] = settings.semantic_scholar_api_key
        params = {
            "query": query,
            "limit": min(max(limit, 1), 100),
            "year": f"{since_year}-",
            "fields": ",".join(
                [
                    "title",
                    "abstract",
                    "authors",
                    "year",
                    "publicationDate",
                    "venue",
                    "citationCount",
                    "influentialCitationCount",
                    "externalIds",
                    "url",
                    "openAccessPdf",
                ]
            ),
        }
        await _semantic_scholar_wait_turn()
        r = await self._client.get("https://api.semanticscholar.org/graph/v1/paper/search", params=params, headers=headers)
        if r.status_code == 429:
            retry_after = r.headers.get("Retry-After")
            try:
                delay = max(float(retry_after or 0), float(settings.semantic_scholar_min_interval_seconds or 1.15))
            except ValueError:
                delay = float(settings.semantic_scholar_min_interval_seconds or 1.15)
            log.warning("Semantic Scholar rate limited; retrying once after %.2fs", delay)
            await asyncio.sleep(delay)
            await _semantic_scholar_wait_turn()
            r = await self._client.get("https://api.semanticscholar.org/graph/v1/paper/search", params=params, headers=headers)
        if r.status_code >= 400:
            return []
        return [self._semantic_item(item) for item in (r.json().get("data") or []) if item.get("title")]

    async def _search_openalex(self, query: str, *, limit: int, since_year: int) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "search": query,
            "filter": f"from_publication_date:{since_year}-01-01,type:article",
            "per-page": min(max(limit, 1), 200),
            "sort": "relevance_score:desc",
        }
        if settings.openalex_mailto:
            params["mailto"] = settings.openalex_mailto
        cited_params = {**params, "sort": "cited_by_count:desc"}
        results: list[dict[str, Any]] = []
        for query_params in (params, cited_params):
            r = await self._client.get("https://api.openalex.org/works", params=query_params)
            if r.status_code < 400:
                results.extend(r.json().get("results") or [])
        return [self._openalex_item(item) for item in _dedupe_raw(results, "id") if item.get("display_name")]

    async def _search_crossref(self, query: str, *, limit: int, since_year: int) -> list[dict[str, Any]]:
        headers = {"User-Agent": f"FrontierReview/1.0 (mailto:{settings.openalex_mailto})"} if settings.openalex_mailto else {}
        params = {
            "query": query,
            "rows": min(max(limit, 1), 1000),
            "filter": f"from-pub-date:{since_year}-01-01,type:journal-article",
            "sort": "relevance",
            "order": "desc",
        }
        cited_params = {**params, "sort": "is-referenced-by-count"}
        results: list[dict[str, Any]] = []
        for query_params in (params, cited_params):
            r = await self._client.get("https://api.crossref.org/works", params=query_params, headers=headers)
            if r.status_code < 400:
                results.extend(r.json().get("message", {}).get("items") or [])
        return [self._crossref_item(item) for item in _dedupe_raw(results, "DOI") if item.get("title")]

    async def _search_europepmc(self, query: str, *, limit: int, since_year: int) -> list[dict[str, Any]]:
        params = {
            "query": f"({query}) AND FIRST_PDATE:[{since_year}-01-01 TO 2030-12-31]",
            "format": "json",
            "pageSize": min(max(limit, 1), 100),
            "sort": "CITED desc",
        }
        r = await self._client.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search", params=params)
        if r.status_code >= 400:
            return []
        return [self._europepmc_item(item) for item in (r.json().get("resultList", {}).get("result") or []) if item.get("title")]

    async def _search_hal(self, query: str, *, limit: int, since_year: int) -> list[dict[str, Any]]:
        params = {
            "q": f"({query}) AND producedDateY_i:[{since_year} TO *]",
            "rows": min(max(limit, 1), 100),
            "sort": "score desc",
            "fl": "docid,title_s,authFullName_s,abstract_s,doiId_s,producedDateY_i,producedDate_s,journalTitle_s,uri_s,fileMain_s,fileAnnexes_s",
            "wt": "json",
        }
        r = await self._client.get("https://api.archives-ouvertes.fr/search/", params=params)
        if r.status_code >= 400:
            return []
        return [self._hal_item(item) for item in (r.json().get("response", {}).get("docs") or []) if item.get("title_s")]

    async def _search_core(self, query: str, *, limit: int, since_year: int) -> list[dict[str, Any]]:
        if not settings.core_api_key:
            return []
        headers = {"Authorization": f"Bearer {settings.core_api_key}"}
        payload = {
            "q": f"{query} yearPublished>={since_year}",
            "limit": min(max(limit, 1), 100),
        }
        r = await self._client.post("https://api.core.ac.uk/v3/search/works", json=payload, headers=headers)
        if r.status_code >= 400:
            return []
        return [self._core_item(item) for item in (r.json().get("results") or []) if item.get("title")]

    async def _search_base(self, query: str, *, limit: int, since_year: int) -> list[dict[str, Any]]:
        params = {
            "func": "PerformSearch",
            "query": f"{query} year:{since_year}-2030",
            "format": "json",
            "hits": min(max(limit, 1), 100),
        }
        r = await self._client.get("https://api.base-search.net/cgi-bin/BaseHttpSearchInterface.fcgi", params=params)
        if r.status_code >= 400:
            return []
        docs = r.json().get("response", {}).get("docs") or r.json().get("docs") or []
        return [self._base_item(item) for item in docs if item.get("dctitle")]

    async def _search_unpaywall(self, query: str, *, limit: int, since_year: int) -> list[dict[str, Any]]:
        if not settings.unpaywall_email:
            return []
        params = {
            "query": query,
            "is_oa": "true",
            "email": settings.unpaywall_email,
        }
        try:
            r = await self._client.get("https://api.unpaywall.org/v2/search", params=params)
        except httpx.HTTPError:
            return []
        if r.status_code >= 400:
            return []
        data = r.json()
        results = data.get("results", data if isinstance(data, list) else [])
        items: list[dict[str, Any]] = []
        for result in results[: min(max(limit, 1), 100)]:
            item = result.get("response") if isinstance(result, dict) else None
            if isinstance(item, dict) and item.get("title"):
                normalized = self._unpaywall_item(item)
                year = normalized.get("publication_published_year")
                if not year or int(year) >= since_year:
                    items.append(normalized)
        return items

    async def lookup_unpaywall(self, doi: str) -> dict[str, Any] | None:
        if not settings.unpaywall_email or not doi:
            return None
        try:
            r = await self._client.get(
                f"https://api.unpaywall.org/v2/{doi}",
                params={"email": settings.unpaywall_email},
                timeout=10.0,
            )
        except httpx.HTTPError:
            return None
        if r.status_code >= 400:
            return None
        data = r.json()
        return data if isinstance(data, dict) else None

    @staticmethod
    def _semantic_item(item: dict[str, Any]) -> dict[str, Any]:
        external = item.get("externalIds") or {}
        pdf = item.get("openAccessPdf") or {}
        return {
            "title": _clean_text(item.get("title")),
            "doi": _doi(external.get("DOI")),
            "author": [{"name": _clean_text(author.get("name"))} for author in item.get("authors") or []],
            "publication_published_year": item.get("year"),
            "publication_published_date": item.get("publicationDate"),
            "publication_venue_name_unified": _clean_text(item.get("venue")),
            "citation_count": item.get("citationCount") or 0,
            "influential_citation_count": item.get("influentialCitationCount") or 0,
            "doc_id": "",
            "unique_id": item.get("paperId"),
            "abstract": _clean_text(item.get("abstract")),
            "access_oa_url": pdf.get("url") or item.get("url"),
            "pdf_url": pdf.get("url"),
            "pdf_source": "semantic" if pdf.get("url") else "",
            "source": "semantic",
        }

    @staticmethod
    def _openalex_item(item: dict[str, Any]) -> dict[str, Any]:
        source = ((item.get("primary_location") or {}).get("source") or {}).get("display_name")
        primary_location = item.get("primary_location") or {}
        locations = item.get("locations") or []
        pdf_url = _clean_text(primary_location.get("pdf_url"))
        if not pdf_url and isinstance(locations, list):
            pdf_url = next((_clean_text(location.get("pdf_url")) for location in locations if isinstance(location, dict) and _clean_text(location.get("pdf_url"))), "")
        oa_url = _clean_text((item.get("open_access") or {}).get("oa_url")) or _clean_text(primary_location.get("landing_page_url"))
        authors = []
        for authorship in item.get("authorships") or []:
            author = authorship.get("author") or {}
            if author.get("display_name"):
                authors.append({"name": author.get("display_name")})
        return {
            "title": _clean_text(item.get("display_name")),
            "doi": _doi(item.get("doi")),
            "author": authors,
            "publication_published_year": item.get("publication_year"),
            "publication_published_date": item.get("publication_date"),
            "publication_venue_name_unified": _clean_text(source),
            "citation_count": item.get("cited_by_count") or 0,
            "influential_citation_count": 0,
            "doc_id": "",
            "unique_id": item.get("id"),
            "abstract": _clean_text(_abstract_from_openalex(item.get("abstract_inverted_index"))),
            "access_oa_url": pdf_url or oa_url or item.get("doi") or item.get("id"),
            "pdf_url": pdf_url,
            "pdf_source": "openalex" if pdf_url else "",
            "source": "openalex",
        }

    @staticmethod
    def _crossref_item(item: dict[str, Any]) -> dict[str, Any]:
        date_parts = ((item.get("published-print") or item.get("published-online") or item.get("issued") or {}).get("date-parts") or [])
        year = date_parts[0][0] if date_parts and date_parts[0] else _year_from_date(item.get("created"))
        authors = []
        for author in item.get("author") or []:
            literal = author.get("name") or " ".join(part for part in [author.get("given"), author.get("family")] if part)
            if literal:
                authors.append({"name": literal})
        links = item.get("link") or []
        pdf_url = ""
        if isinstance(links, list):
            pdf_url = next(
                (
                    _clean_text(link.get("URL"))
                    for link in links
                    if isinstance(link, dict)
                    and "pdf" in _clean_text(link.get("content-type") or link.get("content_type") or link.get("intended-application")).lower()
                    and _clean_text(link.get("URL"))
                ),
                "",
            )
        return {
            "title": _first(item.get("title")),
            "doi": _doi(item.get("DOI")),
            "author": authors,
            "publication_published_year": year,
            "publication_published_date": "",
            "publication_venue_name_unified": _first(item.get("container-title")),
            "citation_count": item.get("is-referenced-by-count") or 0,
            "influential_citation_count": 0,
            "doc_id": "",
            "unique_id": item.get("DOI") or item.get("URL"),
            "abstract": _clean_text(item.get("abstract")),
            "access_oa_url": pdf_url or item.get("URL"),
            "pdf_url": pdf_url,
            "pdf_source": "crossref" if pdf_url else "",
            "source": "crossref",
        }

    @staticmethod
    def _europepmc_item(item: dict[str, Any]) -> dict[str, Any]:
        author_string = _clean_text(item.get("authorString"))
        authors = [{"name": name.strip()} for name in author_string.split(",") if name.strip()]
        full_text_urls = item.get("fullTextUrlList", {}).get("fullTextUrl", [])
        full_text_url = ""
        if isinstance(full_text_urls, list) and full_text_urls:
            pdf_url = next((_clean_text(link.get("url")) for link in full_text_urls if _clean_text(link.get("documentStyle")).lower() == "pdf"), "")
            full_text_url = pdf_url or _clean_text(full_text_urls[0].get("url"))
        return {
            "title": _clean_text(item.get("title")),
            "doi": _doi(item.get("doi")),
            "author": authors,
            "publication_published_year": _year_from_date(item.get("firstPublicationDate") or item.get("pubYear")),
            "publication_published_date": item.get("firstPublicationDate"),
            "publication_venue_name_unified": _clean_text(item.get("journalTitle")),
            "citation_count": item.get("citedByCount") or 0,
            "influential_citation_count": 0,
            "doc_id": "",
            "unique_id": item.get("id"),
            "abstract": _clean_text(item.get("abstractText")),
            "access_oa_url": full_text_url or item.get("doi"),
            "pdf_url": full_text_url if ".pdf" in full_text_url.lower() else "",
            "pdf_source": "europepmc" if full_text_url and ".pdf" in full_text_url.lower() else "",
            "source": "europepmc",
        }

    @staticmethod
    def _hal_item(item: dict[str, Any]) -> dict[str, Any]:
        pdf_url = _first(item.get("fileMain_s"))
        if not pdf_url:
            pdf_url = _first(item.get("fileAnnexes_s"))
        return {
            "title": _first(item.get("title_s")),
            "doi": _doi(item.get("doiId_s")),
            "author": [{"name": name} for name in (item.get("authFullName_s") or []) if name],
            "publication_published_year": item.get("producedDateY_i"),
            "publication_published_date": item.get("producedDate_s"),
            "publication_venue_name_unified": _clean_text(item.get("journalTitle_s")),
            "citation_count": 0,
            "influential_citation_count": 0,
            "doc_id": "",
            "unique_id": item.get("docid"),
            "abstract": _first(item.get("abstract_s")),
            "access_oa_url": pdf_url or item.get("uri_s"),
            "pdf_url": pdf_url,
            "pdf_source": "hal" if pdf_url else "",
            "source": "hal",
        }

    @staticmethod
    def _core_item(item: dict[str, Any]) -> dict[str, Any]:
        authors = item.get("authors") or []
        return {
            "title": _clean_text(item.get("title")),
            "doi": _doi(item.get("doi")),
            "author": [{"name": _clean_text(author.get("name") if isinstance(author, dict) else author)} for author in authors],
            "publication_published_year": item.get("yearPublished") or _year_from_date(item.get("publishedDate")),
            "publication_published_date": item.get("publishedDate"),
            "publication_venue_name_unified": _clean_text(item.get("publisher") or item.get("journals")),
            "citation_count": item.get("citationCount") or 0,
            "influential_citation_count": 0,
            "doc_id": "",
            "unique_id": item.get("id") or item.get("doi"),
            "abstract": _clean_text(item.get("abstract")),
            "access_oa_url": item.get("downloadUrl") or item.get("sourceFulltextUrls") or item.get("doi"),
            "pdf_url": item.get("downloadUrl"),
            "pdf_source": "core" if item.get("downloadUrl") else "",
            "source": "core",
        }

    @staticmethod
    def _base_item(item: dict[str, Any]) -> dict[str, Any]:
        doi = _first(item.get("dcdoi") or item.get("doi"))
        urls = item.get("dclink") or item.get("dcidentifier") or []
        url = _first(urls)
        return {
            "title": _first(item.get("dctitle")),
            "doi": _doi(doi),
            "author": [{"name": name} for name in (item.get("dccreator") or []) if name],
            "publication_published_year": _year_from_date(item.get("dcyear") or item.get("dcdate")),
            "publication_published_date": _first(item.get("dcdate")),
            "publication_venue_name_unified": _first(item.get("dcsource") or item.get("dcpublisher")),
            "citation_count": 0,
            "influential_citation_count": 0,
            "doc_id": "",
            "unique_id": item.get("id") or doi or url,
            "abstract": _first(item.get("dcdescription")),
            "access_oa_url": url or doi,
            "source": "base",
        }

    @staticmethod
    def _unpaywall_item(item: dict[str, Any]) -> dict[str, Any]:
        best = item.get("best_oa_location") or {}
        z_authors = item.get("z_authors") or []
        year = item.get("year") or _year_from_date(item.get("published_date"))
        return {
            "title": _clean_text(item.get("title")),
            "doi": _doi(item.get("doi")),
            "author": [{"name": _clean_text(author.get("family") or author.get("given") or author.get("name"))} for author in z_authors],
            "publication_published_year": year,
            "publication_published_date": item.get("published_date"),
            "publication_venue_name_unified": _clean_text(item.get("journal_name")),
            "citation_count": 0,
            "influential_citation_count": 0,
            "doc_id": "",
            "unique_id": item.get("doi"),
            "abstract": "",
            "access_oa_url": best.get("url_for_pdf") or best.get("url") or item.get("doi_url"),
            "pdf_url": best.get("url_for_pdf"),
            "pdf_source": "unpaywall" if best.get("url_for_pdf") else "",
            "pdf_license": best.get("license"),
            "source": "unpaywall",
        }


def _dedupe_raw(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        value = _clean_text(item.get(key)).lower()
        title = _clean_text(item.get("title") or item.get("display_name")).lower()
        dedupe_key = value or re.sub(r"\W+", "", title)
        if not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        out.append(item)
    return out
