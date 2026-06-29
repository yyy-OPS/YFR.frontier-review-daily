"""Standalone daily Sciverse literature review workflow."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import struct
import time
import zlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .config import settings
from .errors import ApiError
from .llm import _normalize_openai_base_url, get_llm_client
from .paper_search_client import PaperSearchClient
from .sciverse_client import SciverseClient

router = APIRouter(prefix="/daily-review", tags=["daily-review"])
log = logging.getLogger("agent.daily_review")

Freshness = Literal["NONE", "MILD", "STRONG"]
LiteratureProvider = Literal["sciverse", "paper_search", "hybrid"]
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
PAPER_SEARCH_SOURCE_ALLOWLIST = {"semantic", "openalex", "crossref", "europepmc", "hal", "base", "core", "unpaywall"}
ALLOWED_IMAGE_CONTENT_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
TRANSLATION_RATE_BUCKETS: dict[str, list[float]] = {}
ADMIN_LOGIN_RATE_BUCKETS: dict[str, list[float]] = {}
PDF_RESOLVE_RATE_BUCKETS: dict[str, list[float]] = {}
LITERATURE_SEARCH_RATE_BUCKETS: dict[str, list[float]] = {}
TRUSTED_OA_PDF_HOST_SUFFIXES = (
    "arxiv.org",
    "biorxiv.org",
    "medrxiv.org",
    "pmc.ncbi.nlm.nih.gov",
    "europepmc.org",
    "core.ac.uk",
    "hal.science",
    "archives-ouvertes.fr",
    "plos.org",
    "frontiersin.org",
    "mdpi.com",
)


def validate_runtime_security() -> None:
    """Fail closed for deploy images while keeping explicit local development possible."""
    if settings.allow_insecure_defaults or not settings.require_secure_config:
        return
    password = os.environ.get("DAILY_REVIEW_ADMIN_PASSWORD", "")
    secret = os.environ.get("DAILY_REVIEW_ADMIN_SECRET", "")
    insecure_passwords = {"", "admin123", "please-change-admin-password"}
    insecure_secrets = {"", "daily-review-local-secret", "please-change-this-long-random-secret"}
    if password in insecure_passwords:
        raise RuntimeError("生产部署必须配置非默认 DAILY_REVIEW_ADMIN_PASSWORD")
    if secret in insecure_secrets or len(secret) < 24:
        raise RuntimeError("生产部署必须配置长度至少 24 位的 DAILY_REVIEW_ADMIN_SECRET")
    if any(origin.strip() == "*" for origin in settings.cors_origins):
        raise RuntimeError("生产部署禁止 CORS_ORIGINS 使用 *")


def _paper_search_sources_from_settings() -> list[str]:
    return [
        source
        for source in (item.strip().lower() for item in settings.paper_search_sources.split(","))
        if source in PAPER_SEARCH_SOURCE_ALLOWLIST
    ] or ["semantic", "openalex", "crossref", "europepmc", "hal", "base", "core", "unpaywall"]


def _normalize_paper_search_sources(sources: list[str] | None) -> list[str]:
    return [
        source
        for source in (str(item).strip().lower() for item in (sources or []))
        if source in PAPER_SEARCH_SOURCE_ALLOWLIST
    ] or _paper_search_sources_from_settings()


class LlmAdminConfig(BaseModel):
    baseUrl: str = Field(default="")
    apiKey: str = Field(default="")
    model: str = Field(default="")
    temperature: float = Field(default=0.25, ge=0, le=2)
    maxTokens: int = Field(default=12000, ge=1000, le=60000)


class ImageAdminConfig(BaseModel):
    enabled: bool = False
    baseUrl: str = Field(default="")
    apiKey: str = Field(default="")
    model: str = Field(default="")
    size: str = Field(default="1024x1024")


class WechatAdminConfig(BaseModel):
    enabled: bool = False
    appId: str = Field(default_factory=lambda: settings.wechat_app_id)
    appSecret: str = Field(default_factory=lambda: settings.wechat_app_secret)
    author: str = Field(default="研域前沿综述")
    sourceUrlBase: str = Field(default="")
    autoDraft: bool = False
    coverImageUrl: str = Field(default="")
    digestPrefix: str = Field(default="研域前沿综述")


class LiteratureSearchCdkConfig(BaseModel):
    id: str = Field(default="")
    name: str = Field(default="")
    code: str = Field(default="")
    enabled: bool = True
    maxUses: int = Field(default=50, ge=1, le=100000)
    usedCount: int = Field(default=0, ge=0)
    expiresAt: str | None = None
    paperCountMax: int = Field(default=100, ge=5, le=200)
    literatureProvider: LiteratureProvider | None = None
    paperSearchSources: list[str] = Field(default_factory=list)
    note: str = Field(default="")


class ReviewTopicConfig(BaseModel):
    id: str = Field(default="")
    slug: str = Field(default="")
    name: str = Field(default="")
    topic: str = Field(default="")
    enabled: bool = True
    scheduleEnabled: bool = True
    scheduleTime: str = "08:30"
    paperCount: int = Field(default=80, ge=20, le=500)
    sinceYear: int = Field(default_factory=lambda: datetime.now().year - 10, ge=1900, le=2100)
    freshnessBoost: Freshness = "STRONG"
    includeFullText: bool = True
    includeWeb: bool = True
    privateOnly: bool = False
    subtopicPool: list[str] = Field(default_factory=list)
    minHighNoveltyCount: int | None = Field(default=None, ge=1, le=500)
    maxRepeatRatio: float = Field(default=0.75, ge=0, le=1)
    allowTopicDeepDive: bool = True
    allowNoSignificantUpdate: bool = True


class DailyReviewConfig(BaseModel):
    topic: str = "近十年人工智能在科研中的前沿进展"
    scheduleEnabled: bool = True
    scheduleTime: str = "08:30"
    paperCount: int = Field(default=80, ge=20, le=500)
    sinceYear: int = Field(default_factory=lambda: datetime.now().year - 10, ge=1900, le=2100)
    freshnessBoost: Freshness = "STRONG"
    includeFullText: bool = True
    includeWeb: bool = True
    sciverseApiToken: str = Field(default="")
    literatureProvider: LiteratureProvider = Field(default=settings.daily_review_literature_provider if settings.daily_review_literature_provider in {"sciverse", "paper_search", "hybrid"} else "sciverse")
    paperSearchSources: list[str] = Field(default_factory=lambda: _paper_search_sources_from_settings())
    llm: LlmAdminConfig = Field(default_factory=LlmAdminConfig)
    translation: LlmAdminConfig = Field(default_factory=LlmAdminConfig)
    image: ImageAdminConfig = Field(default_factory=ImageAdminConfig)
    wechat: WechatAdminConfig = Field(default_factory=WechatAdminConfig)
    exclusiveAccessKey: str = Field(default="")
    literatureSearchCdk: str = Field(default="")
    literatureSearchCdks: list[LiteratureSearchCdkConfig] = Field(default_factory=list)
    activeTopicId: str = "general-ai-research"
    topics: list[ReviewTopicConfig] = Field(default_factory=list)


class DailyReviewConfigView(DailyReviewConfig):
    sciverseTokenConfigured: bool = False
    llmKeyConfigured: bool = False
    translationKeyConfigured: bool = False
    imageKeyConfigured: bool = False
    wechatSecretConfigured: bool = False
    exclusiveAccessKeyConfigured: bool = False
    literatureSearchCdkConfigured: bool = False


class ConnectionTestResult(BaseModel):
    ok: bool
    service: Literal["llm", "sciverse", "paper_search", "image", "wechat"]
    message: str
    detail: str | None = None


class DailyReviewRunRequest(BaseModel):
    topicId: str | None = None
    topicSlug: str | None = None
    topic: str | None = None
    paperCount: int | None = Field(default=None, ge=20, le=500)
    sinceYear: int | None = Field(default=None, ge=1900, le=2100)
    freshnessBoost: Freshness | None = None
    includeFullText: bool | None = None
    includeWeb: bool | None = None


class LiteratureOnlySearchRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=300)
    paperCount: int = Field(default=50, ge=5, le=200)
    sinceYear: int | None = Field(default=None, ge=1900, le=2100)
    literatureProvider: LiteratureProvider | None = None
    paperSearchSources: list[str] | None = None
    cdk: str | None = None
    llm: LlmAdminConfig | None = None


class LiteratureCdkStatusRequest(BaseModel):
    cdk: str = Field(min_length=1, max_length=300)


class LiteratureCdkStatusResult(BaseModel):
    ok: bool
    cdk: dict[str, Any] | None = None
    message: str


class LiteratureOnlySearchResult(BaseModel):
    ok: bool
    searchId: str | None = None
    sharePath: str | None = None
    createdAt: str | None = None
    topic: str
    requested: int
    returned: int
    sinceYear: int
    literatureProvider: LiteratureProvider
    paperSearchSources: list[str]
    llmSearchQueries: list[str]
    searchExpression: str
    cdkId: str | None = None
    cdkName: str | None = None
    cdk: dict[str, Any] | None = None
    papers: list[dict[str, Any]]


class LiteratureSearchProgressItem(BaseModel):
    searchId: str
    status: Literal["queued", "running", "success", "error"]
    stage: str
    message: str
    mode: Literal["determinate", "indeterminate"] = "determinate"
    detail: str | None = None
    percent: int = Field(default=0, ge=0, le=100)
    current: int = 0
    total: int | None = None
    startedAt: str | None = None
    updatedAt: str | None = None
    completedAt: str | None = None
    error: str | None = None
    sharePath: str | None = None


class LiteratureSearchAccepted(BaseModel):
    accepted: bool
    searchId: str
    sharePath: str
    progress: LiteratureSearchProgressItem


class LiteratureSearchProgressResult(BaseModel):
    progress: LiteratureSearchProgressItem


class LiteratureSearchStoredResult(BaseModel):
    result: LiteratureOnlySearchResult | None = None
    progress: LiteratureSearchProgressItem | None = None


class LiteratureSearchSummary(BaseModel):
    searchId: str
    topic: str
    sharePath: str
    cdkId: str | None = None
    cdkName: str | None = None
    requested: int = 0
    returned: int = 0
    sinceYear: int | None = None
    literatureProvider: str | None = None
    status: str = "unknown"
    createdAt: str | None = None
    updatedAt: str | None = None


class LiteratureSearchListResult(BaseModel):
    items: list[LiteratureSearchSummary]


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminLoginResult(BaseModel):
    token: str
    username: str
    expiresAt: str


class AdminPasswordChangeRequest(BaseModel):
    oldPassword: str
    newPassword: str = Field(min_length=6, max_length=128)


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    targetLanguage: str = "中文"
    context: str | None = None


class TranslateResult(BaseModel):
    translatedText: str
    model: str


class PdfResolveRequest(BaseModel):
    runId: str | None = None
    paperId: str | None = None
    doi: str | None = None
    title: str | None = None
    url: str | None = None


class PdfResolveResult(BaseModel):
    ok: bool
    url: str | None = None
    remoteUrl: str | None = None
    code: str | None = None
    source: str | None = None
    isOpenAccess: bool = False
    oaStatus: str | None = None
    license: str | None = None
    cached: bool = False
    bytes: int | None = None
    message: str
    detail: str | None = None


class WechatArticleResult(BaseModel):
    runId: str
    title: str
    digest: str
    contentHtml: str
    contentText: str
    coverUrl: str | None = None
    sourceUrl: str
    draftMediaId: str | None = None
    articleUrl: str | None = None
    status: str = "generated"
    message: str | None = None


class WechatDraftRequest(BaseModel):
    runId: str
    title: str | None = None
    digest: str | None = None
    contentHtml: str | None = None
    contentText: str | None = None
    coverUrl: str | None = None


class SmokeRequest(BaseModel):
    topicId: str | None = None
    topicSlug: str | None = None
    topic: str | None = None
    paperCount: int = Field(default=200, ge=20, le=500)
    sinceYear: int | None = Field(default=None, ge=1900, le=2100)
    freshnessBoost: Freshness | None = None


class SmokeResult(BaseModel):
    ok: bool
    topic: str
    requested: int
    returned: int
    sinceYear: int
    sciverseTotal: int | None = None
    searchExpression: str
    withAbstractCount: int = 0
    withSnippetCount: int = 0
    strongDomainCount: int = 0
    yearCounts: list[tuple[str, int]]
    venueTop: list[tuple[str, int]]
    sampleTitles: list[str]


class DailyReviewProgressItem(BaseModel):
    topicId: str
    topicSlug: str
    topicName: str
    status: Literal["idle", "running", "success", "error"]
    stage: str
    message: str
    mode: Literal["determinate", "indeterminate"] = "determinate"
    detail: str | None = None
    percent: int = Field(default=0, ge=0, le=100)
    current: int = 0
    total: int | None = None
    startedAt: str | None = None
    updatedAt: str | None = None
    completedAt: str | None = None
    runId: str | None = None
    error: str | None = None
    latestRunId: str | None = None
    latestRunAt: str | None = None
    latestPaperCount: int | None = None
    draftId: str | None = None
    draftStage: str | None = None
    draftCanResume: bool = False


class DailyReviewProgressResult(BaseModel):
    items: list[DailyReviewProgressItem]


class DailyReviewRunAccepted(BaseModel):
    accepted: bool
    topicId: str
    progress: DailyReviewProgressItem


class DailyReviewDraftSummary(BaseModel):
    draftId: str
    topicId: str
    topicSlug: str | None = None
    topicName: str | None = None
    topic: str
    stage: str
    status: str
    canResume: bool = False
    createdAt: str
    updatedAt: str
    paperCount: int = 0
    fullTextFetched: int = 0
    error: str | None = None


class DailyReviewDraftsResult(BaseModel):
    items: list[DailyReviewDraftSummary]


class DailyReviewResumeRequest(BaseModel):
    draftId: str


class DailyReviewRunSummary(BaseModel):
    runId: str
    topicId: str | None = None
    topicSlug: str | None = None
    topicName: str | None = None
    topic: str
    subtitle: str | None = None
    dailyMode: str | None = None
    newEvidenceCount: int | None = None
    reusedEvidenceCount: int | None = None
    createdAt: str
    paperCount: int
    sinceYear: int | None = None
    fullTextFetched: int = 0
    imageStatus: str | None = None


class DailyReviewHistoryResult(BaseModel):
    items: list[DailyReviewRunSummary]


class DailyReviewTopicsResult(BaseModel):
    items: list[ReviewTopicConfig]


_RUN_PROGRESS: dict[str, dict[str, Any]] = {}
_RUN_TASKS: dict[str, asyncio.Task] = {}
_LITERATURE_SEARCH_PROGRESS: dict[str, dict[str, Any]] = {}
_LITERATURE_SEARCH_TASKS: dict[str, asyncio.Task] = {}


def _config_path() -> Path:
    return _data_path("daily_review_config.json")


def _config_backup_path() -> Path:
    return _config_path().with_name(f"{_config_path().name}.bak")


def _runs_path() -> Path:
    return _data_path("daily_review_runs.jsonl")


def _runs_dir() -> Path:
    root = Path(settings.frontier_review_data_dir) / "runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _literature_search_dir() -> Path:
    root = Path(settings.frontier_review_data_dir) / "literature_search"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _literature_search_path(search_id: str) -> Path:
    safe_id = _safe_draft_part(search_id)
    if not safe_id:
        safe_id = "search"
    return _literature_search_dir() / f"{safe_id}.json"


def _literature_search_index_path() -> Path:
    return _literature_search_dir() / "index.jsonl"


def _runs_index_path() -> Path:
    return _data_path("daily_review_runs_index.jsonl")


def _paper_index_path() -> Path:
    return _data_path("paper_index.jsonl")


def _topic_daily_state_path() -> Path:
    return _data_path("topic_daily_state.json")


def _daily_delta_runs_path() -> Path:
    return _data_path("daily_delta_runs.jsonl")


def _drafts_dir() -> Path:
    root = Path(settings.frontier_review_data_dir) / "drafts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_draft_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "", value)
    if not safe:
        raise ApiError(400, "INVALID_DRAFT_ID", "暂存 ID 无效")
    return safe


def _draft_path(draft_id: str, topic_id: str | None = None) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "", draft_id)
    if not safe_id:
        raise ApiError(400, "INVALID_DRAFT_ID", "暂存 ID 无效")
    if topic_id:
        topic_dir = _drafts_dir() / _safe_draft_part(topic_id)
        topic_dir.mkdir(parents=True, exist_ok=True)
        return topic_dir / f"{safe_id}.json"
    for path in _drafts_dir().glob(f"*/{safe_id}.json"):
        return path
    return _drafts_dir() / f"{safe_id}.json"


def _admin_auth_path() -> Path:
    return _data_path("daily_review_admin.json")


def _data_path(filename: str) -> Path:
    root = Path(settings.frontier_review_data_dir)
    root.mkdir(parents=True, exist_ok=True)
    target = root / filename
    legacy = Path(settings.corpora_dir) / filename
    if not target.exists() and legacy.exists():
        try:
            shutil.copy2(legacy, target)
        except OSError:
            log.warning("failed to migrate legacy daily review file: %s", legacy)
    return target


def _image_assets_dir() -> Path:
    root = Path(settings.frontier_review_data_dir) / "assets" / "images"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _pdf_assets_dir() -> Path:
    root = Path(settings.frontier_review_data_dir) / "assets" / "pdfs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _image_asset_url(filename: str) -> str:
    return f"/daily-review/assets/images/{filename}"


def _pdf_asset_url(filename: str) -> str:
    return f"/api/daily-review/assets/pdfs/{filename}"


def _wechat_token_path() -> Path:
    return _data_path("wechat_token.json")


def _image_suffix(content_type: str | None = None, url: str | None = None, default: str = ".png") -> str:
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if content_type in ALLOWED_IMAGE_CONTENT_TYPES:
        return ALLOWED_IMAGE_CONTENT_TYPES[content_type]
    suffix = Path((url or "").split("?", 1)[0]).suffix.lower()
    return suffix if suffix in {".png", ".jpg", ".jpeg", ".webp"} else default


def _save_image_asset(data: bytes, suffix: str) -> str:
    if suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".png"
    digest = hashlib.sha256(data).hexdigest()[:16]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    filename = f"{stamp}-{digest}{suffix}"
    (_image_assets_dir() / filename).write_bytes(data)
    return _image_asset_url(filename)


def _is_public_ip(ip: str) -> bool:
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return parsed.is_global


def _assert_public_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("remote image url must be http/https")
    hostname = parsed.hostname.strip().lower()
    if hostname in {"localhost"} or hostname.endswith(".localhost"):
        raise RuntimeError("remote image url points to a local host")
    try:
        infos = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RuntimeError(f"remote image host cannot be resolved: {hostname}") from exc
    ips = {info[4][0] for info in infos}
    if not ips or not all(_is_public_ip(ip) for ip in ips):
        raise RuntimeError("remote image url resolved to a non-public address")


def _is_trusted_oa_pdf_host(hostname: str) -> bool:
    host = hostname.strip().lower()
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in TRUSTED_OA_PDF_HOST_SUFFIXES)


def _assert_public_pdf_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("pdf url must be http/https")
    hostname = parsed.hostname.strip().lower()
    if hostname in {"localhost"} or hostname.endswith(".localhost"):
        raise RuntimeError("pdf url points to a local host")
    try:
        infos = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RuntimeError(f"pdf host cannot be resolved: {hostname}") from exc
    ips = {info[4][0] for info in infos}
    if not ips:
        raise RuntimeError("pdf url host has no resolved address")
    if any(_is_public_ip(ip) for ip in ips) or _is_trusted_oa_pdf_host(hostname):
        return
    raise RuntimeError("pdf url resolved to a non-public address")


async def _download_remote_image(client: httpx.AsyncClient, url: str, headers: dict[str, str] | None) -> tuple[str, bytes]:
    current = url
    for _ in range(5):
        _assert_public_http_url(current)
        async with client.stream("GET", current, headers=headers, follow_redirects=False) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise RuntimeError("image redirect without location")
                current = urljoin(current, location)
                continue
            _assert_public_http_url(str(response.url))
            if response.status_code >= 400:
                raise RuntimeError(f"image fetch failed {response.status_code}")
            content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            if content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
                raise RuntimeError(f"unsupported image content type: {content_type or 'unknown'}")
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > settings.max_cached_image_bytes:
                raise RuntimeError("image is too large")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > settings.max_cached_image_bytes:
                    raise RuntimeError("image is too large")
            if not data:
                raise RuntimeError("image response is empty")
            return content_type, bytes(data)
    raise RuntimeError("image redirect chain is too long")


async def _cache_remote_image_url(url: str, api_key: str | None = None) -> str:
    headers = {"Authorization": f"Bearer {api_key.strip()}"} if api_key and api_key.strip() else None
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
        content_type, data = await _download_remote_image(client, url, headers)
    return _save_image_asset(data, ALLOWED_IMAGE_CONTENT_TYPES[content_type])


def _replace_run(updated: dict[str, Any]) -> None:
    run_id = str(updated.get("runId") or "")
    if not run_id:
        return
    for item in _iter_run_index():
        if str(item.get("runId") or "") != run_id:
            continue
        rel = str(item.get("path") or "")
        if not rel:
            continue
        path = Path(settings.frontier_review_data_dir) / rel
        if path.exists() and path.is_file():
            path.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
            break
    path = _runs_path()
    if not path.exists():
        return
    next_lines: list[str] = []
    changed = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            current = json.loads(line)
        except json.JSONDecodeError:
            next_lines.append(line)
            continue
        if isinstance(current, dict) and str(current.get("runId") or "") == run_id:
            next_lines.append(json.dumps(updated, ensure_ascii=False))
            changed = True
        else:
            next_lines.append(line)
    if changed:
        path.write_text("\n".join(next_lines) + "\n", encoding="utf-8")


async def _ensure_run_image_local(run: dict[str, Any] | None, config: DailyReviewConfig | None = None) -> dict[str, Any] | None:
    if not run:
        return run
    image = run.get("image")
    if not isinstance(image, dict):
        return run
    url = str(image.get("url") or "")
    if not url.startswith(("http://", "https://")):
        return run
    try:
        local_url = await _cache_remote_image_url(url, config.image.apiKey if config else None)
    except Exception as exc:  # noqa: BLE001
        log.warning("failed to cache existing remote image: %s", exc)
        return run
    out = dict(run)
    out["image"] = {**image, "remoteUrl": url, "url": local_url}
    _replace_run(out)
    return out


def _slugify(value: str, fallback: str = "topic") -> str:
    value = html.unescape(value or "").strip().lower()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    if not value:
        value = fallback
    if re.search(r"[\u4e00-\u9fff]", value):
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]
        return f"{fallback}-{digest}"
    return value[:80]


def _default_topic_config(config: DailyReviewConfig | None = None) -> ReviewTopicConfig:
    topic = (config.topic if config else "") or "近十年人工智能在科研中的前沿进展"
    return ReviewTopicConfig(
        id="general-ai-research",
        slug="general-ai-research",
        name="科研前沿日报",
        topic=topic,
        enabled=True,
        scheduleEnabled=config.scheduleEnabled if config else True,
        scheduleTime=(config.scheduleTime if config else "08:30") or "08:30",
        paperCount=config.paperCount if config else 80,
        sinceYear=config.sinceYear if config else datetime.now().year - 10,
        freshnessBoost=config.freshnessBoost if config else "STRONG",
        includeFullText=config.includeFullText if config else True,
        includeWeb=config.includeWeb if config else True,
        subtopicPool=[],
        maxRepeatRatio=0.75,
        allowTopicDeepDive=True,
        allowNoSignificantUpdate=True,
    )


def _ensure_topics(config: DailyReviewConfig) -> DailyReviewConfig:
    topics = config.topics or [_default_topic_config(config)]
    normalized: list[ReviewTopicConfig] = []
    seen: set[str] = set()
    for index, topic in enumerate(topics):
        topic.topic = _repair_mojibake(topic.topic or config.topic or "近十年人工智能在科研中的前沿进展")
        if _looks_mojibake(topic.topic):
            topic.topic = "近十年人工智能在科研中的前沿进展"
        topic.name = _repair_mojibake(topic.name or topic.topic[:36] or f"主题 {index + 1}")
        if _looks_mojibake(topic.name):
            topic.name = f"主题 {index + 1}"
        topic.subtopicPool = [
            _repair_mojibake(str(item)).strip()
            for item in (topic.subtopicPool or [])
            if str(item).strip()
        ][:30]
        topic.id = (topic.id or _slugify(topic.name, f"topic-{index + 1}")).strip()
        topic.slug = (topic.slug or _slugify(topic.name, topic.id)).strip()
        original = topic.slug
        suffix = 2
        while topic.slug in seen:
            topic.slug = f"{original}-{suffix}"
            suffix += 1
        seen.add(topic.slug)
        normalized.append(topic)
    config.topics = normalized
    if not any(t.id == config.activeTopicId for t in normalized):
        config.activeTopicId = normalized[0].id
    active = next((t for t in normalized if t.id == config.activeTopicId), normalized[0])
    config.topic = active.topic
    config.scheduleEnabled = active.scheduleEnabled
    config.scheduleTime = active.scheduleTime
    config.paperCount = active.paperCount
    config.sinceYear = active.sinceYear
    config.freshnessBoost = active.freshnessBoost
    config.includeFullText = active.includeFullText
    config.includeWeb = active.includeWeb
    config.wechat.author = _repair_mojibake(config.wechat.author or "").strip()
    if not config.wechat.author or _looks_mojibake(config.wechat.author) or config.wechat.author.count("?") >= 3:
        config.wechat.author = "研域前沿综述"
    config.wechat.digestPrefix = _repair_mojibake(config.wechat.digestPrefix or "").strip()
    if not config.wechat.digestPrefix or _looks_mojibake(config.wechat.digestPrefix) or config.wechat.digestPrefix.count("?") >= 3:
        config.wechat.digestPrefix = "研域前沿综述"
    config.wechat.sourceUrlBase = (config.wechat.sourceUrlBase or "").strip()
    if config.literatureSearchCdk and not config.literatureSearchCdks:
        config.literatureSearchCdks = [
            LiteratureSearchCdkConfig(
                id="default",
                name="默认文献检索 CDK",
                code=config.literatureSearchCdk,
                maxUses=1000,
                paperCountMax=100,
            )
        ]
    normalized_cdks: list[LiteratureSearchCdkConfig] = []
    seen_cdk_ids: set[str] = set()
    for index, cdk in enumerate(config.literatureSearchCdks or []):
        cdk.code = _usable_secret_override(cdk.code)
        if not cdk.code:
            continue
        cdk.id = (cdk.id or _hash_text(cdk.code, 12) or f"cdk-{index + 1}").strip()
        original_cdk_id = cdk.id
        suffix = 2
        while cdk.id in seen_cdk_ids:
            cdk.id = f"{original_cdk_id}-{suffix}"
            suffix += 1
        seen_cdk_ids.add(cdk.id)
        cdk.name = _clean_text(cdk.name) or f"文献检索 CDK {index + 1}"
        cdk.paperSearchSources = _normalize_paper_search_sources(cdk.paperSearchSources) if cdk.paperSearchSources else []
        if cdk.literatureProvider not in {"sciverse", "paper_search", "hybrid", None}:
            cdk.literatureProvider = None
        normalized_cdks.append(cdk)
    config.literatureSearchCdks = normalized_cdks
    return config


def _read_config_file(path: Path) -> DailyReviewConfig | None:
    if path.exists():
        try:
            return _ensure_topics(DailyReviewConfig(**json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            log.warning("failed to read daily review config: %s", path, exc_info=True)
    return None


def _load_config() -> DailyReviewConfig:
    path = _config_path()
    config = _read_config_file(path)
    if config is not None:
        return config
    backup = _config_backup_path()
    config = _read_config_file(backup)
    if config is not None:
        log.warning("daily review config recovered from backup: %s", backup)
        _save_config(config)
        return config
    return _ensure_topics(DailyReviewConfig(
        sciverseApiToken=settings.sciverse_api_token,
        llm=LlmAdminConfig(
            baseUrl=settings.openai_base_url,
            apiKey=settings.openai_api_key,
            model=settings.openai_model,
        ),
    ))


def _save_config(config: DailyReviewConfig) -> None:
    config = _ensure_topics(config)
    path = _config_path()
    backup = _config_backup_path()
    current = _read_config_file(path)
    if current is not None:
        try:
            shutil.copy2(path, backup)
        except OSError:
            log.warning("failed to write daily review config backup: %s", backup, exc_info=True)
    payload = json.dumps(config.model_dump(), ensure_ascii=False, indent=2)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            log.warning("failed to clean daily review config temp file: %s", tmp, exc_info=True)


def _password_hash(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


def _admin_secret() -> bytes:
    raw = os.environ.get("DAILY_REVIEW_ADMIN_SECRET") or settings.openai_api_key or "daily-review-local-secret"
    return raw.encode("utf-8")


def _load_admin_auth() -> dict[str, str]:
    path = _admin_auth_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("username") and data.get("salt") and data.get("passwordHash"):
                return data
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    username = os.environ.get("DAILY_REVIEW_ADMIN_USERNAME", "admin")
    password = os.environ.get("DAILY_REVIEW_ADMIN_PASSWORD", "admin123")
    salt = hashlib.sha256(f"{username}:{_admin_secret().decode('utf-8', errors='ignore')}".encode("utf-8")).hexdigest()[:24]
    return {"username": username, "salt": salt, "passwordHash": _password_hash(password, salt)}


def _save_admin_auth(username: str, password: str) -> None:
    salt = hashlib.sha256(os.urandom(32)).hexdigest()[:24]
    _admin_auth_path().write_text(
        json.dumps({"username": username, "salt": salt, "passwordHash": _password_hash(password, salt)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _verify_admin_password(username: str, password: str) -> bool:
    auth = _load_admin_auth()
    return hmac.compare_digest(username, auth["username"]) and hmac.compare_digest(_password_hash(password, auth["salt"]), auth["passwordHash"])


def _sign_admin_payload(payload: str) -> str:
    return hmac.new(_admin_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _make_admin_token(username: str) -> tuple[str, datetime]:
    expires = datetime.now(timezone.utc) + timedelta(hours=12)
    payload = base64.urlsafe_b64encode(
        json.dumps({"u": username, "exp": int(expires.timestamp())}, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"{payload}.{_sign_admin_payload(payload)}", expires


def _decode_admin_token(token: str) -> dict[str, Any] | None:
    try:
        payload, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(_sign_admin_payload(payload), sig):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode((payload + "=" * (-len(payload) % 4)).encode("ascii")).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(data.get("exp") or 0) < int(datetime.now(timezone.utc).timestamp()):
        return None
    return data


def _require_admin(request: Request) -> None:
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token or _decode_admin_token(token) is None:
        raise ApiError(401, "ADMIN_AUTH_REQUIRED", "请先使用管理员账号登录")


def _has_admin_access(request: Request) -> bool:
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return bool(token and _decode_admin_token(token) is not None)


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


def _check_translation_rate_limit(request: Request) -> None:
    _check_rate_limit(TRANSLATION_RATE_BUCKETS, _client_key(request), max(1, settings.translation_rate_limit_per_minute), "TRANSLATION_RATE_LIMITED", "翻译请求过于频繁，请稍后再试")


def _check_pdf_resolve_rate_limit(request: Request) -> None:
    _check_rate_limit(PDF_RESOLVE_RATE_BUCKETS, _client_key(request), 60, "PDF_RESOLVE_RATE_LIMITED", "开放 PDF 查询过于频繁，请稍后再试")


def _check_literature_search_rate_limit(request: Request) -> None:
    _check_rate_limit(LITERATURE_SEARCH_RATE_BUCKETS, _client_key(request), 12, "LITERATURE_SEARCH_RATE_LIMITED", "文献检索请求过于频繁，请稍后再试")


def _check_admin_login_rate_limit(request: Request) -> None:
    _check_rate_limit(ADMIN_LOGIN_RATE_BUCKETS, _client_key(request), max(1, settings.admin_login_rate_limit_per_minute), "ADMIN_LOGIN_RATE_LIMITED", "登录尝试过于频繁，请稍后再试")


def _check_rate_limit(bucket_map: dict[str, list[float]], key: str, limit: int, code: str, message: str) -> None:
    now = time.monotonic()
    bucket = [stamp for stamp in bucket_map.get(key, []) if now - stamp < 60]
    if len(bucket) >= limit:
        bucket_map[key] = bucket
        raise ApiError(429, code, message)
    bucket.append(now)
    bucket_map[key] = bucket


def _mask(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if len(value) <= 10:
        return "*" * len(value)
    return f"{value[:6]}...{value[-4:]}"


def _view_config(config: DailyReviewConfig) -> DailyReviewConfigView:
    config = _ensure_topics(config)
    data = config.model_dump()
    data["sciverseTokenConfigured"] = bool(config.sciverseApiToken.strip())
    data["llmKeyConfigured"] = bool(config.llm.apiKey.strip())
    data["translationKeyConfigured"] = bool(config.translation.apiKey.strip())
    data["imageKeyConfigured"] = bool(config.image.apiKey.strip())
    data["wechatSecretConfigured"] = bool(config.wechat.appSecret.strip())
    data["exclusiveAccessKeyConfigured"] = bool(config.exclusiveAccessKey.strip())
    data["literatureSearchCdkConfigured"] = bool(config.literatureSearchCdks or config.literatureSearchCdk.strip())
    data["sciverseApiToken"] = _mask(config.sciverseApiToken)
    data["llm"]["apiKey"] = _mask(config.llm.apiKey)
    data["translation"]["apiKey"] = _mask(config.translation.apiKey)
    data["image"]["apiKey"] = _mask(config.image.apiKey)
    data["wechat"]["appSecret"] = _mask(config.wechat.appSecret)
    data["exclusiveAccessKey"] = _mask(config.exclusiveAccessKey)
    data["literatureSearchCdk"] = _mask(config.literatureSearchCdk)
    return DailyReviewConfigView(**data)


def _merge_secret(new_value: str, old_value: str) -> str:
    value = (new_value or "").strip()
    if not value:
        return old_value
    if value == "__CLEAR_SECRET__":
        return ""
    if old_value and ("*" in value or "..." in value) and value.endswith(old_value[-4:]):
        return old_value
    return value


def _with_resolved_secrets(body: DailyReviewConfig) -> DailyReviewConfig:
    old = _load_config()
    old_dump = old.model_dump()
    old_topic_signatures = {
        json.dumps(topic, ensure_ascii=False, sort_keys=True)
        for topic in old_dump.get("topics", [])
        if isinstance(topic, dict)
    }
    body_dump = body.model_dump()
    body_topic_signatures = {
        json.dumps(topic, ensure_ascii=False, sort_keys=True)
        for topic in body_dump.get("topics", [])
        if isinstance(topic, dict)
    }
    if body_topic_signatures and body_topic_signatures == old_topic_signatures:
        # Pydantic mutates the nested list on assignment in some TestClient/Pydantic
        # paths. Rebuild from the raw request body so topic edits are never lost.
        raw_topics = body_dump.get("topics") or []
        body.topics = [ReviewTopicConfig(**topic) for topic in raw_topics if isinstance(topic, dict)]
    body.sciverseApiToken = _merge_secret(body.sciverseApiToken, old.sciverseApiToken)
    body.llm.apiKey = _merge_secret(body.llm.apiKey, old.llm.apiKey)
    body.translation.apiKey = _merge_secret(body.translation.apiKey, old.translation.apiKey)
    body.image.apiKey = _merge_secret(body.image.apiKey, old.image.apiKey)
    body.wechat.appSecret = _merge_secret(body.wechat.appSecret, old.wechat.appSecret)
    body.exclusiveAccessKey = _merge_secret(body.exclusiveAccessKey, old.exclusiveAccessKey)
    body.literatureSearchCdk = _merge_secret(body.literatureSearchCdk, old.literatureSearchCdk)
    old_cdks = {cdk.id: cdk for cdk in old.literatureSearchCdks}
    for cdk in body.literatureSearchCdks:
        old_cdk = old_cdks.get(cdk.id)
        if old_cdk:
            cdk.code = _merge_secret(cdk.code, old_cdk.code)
    return _ensure_topics(body)


def _topic_for_request(config: DailyReviewConfig, topic_id: str | None = None, topic_slug: str | None = None) -> ReviewTopicConfig:
    config = _ensure_topics(config)
    if topic_id:
        found = next((t for t in config.topics if t.id == topic_id), None)
        if found:
            return found
    if topic_slug:
        found = next((t for t in config.topics if t.slug == topic_slug), None)
        if found:
            return found
    found = next((t for t in config.topics if t.id == config.activeTopicId), None)
    return found or config.topics[0]


def _public_topic_keys(config: DailyReviewConfig) -> set[str]:
    config = _ensure_topics(config)
    keys: set[str] = set()
    for topic in config.topics:
        if topic.enabled and not topic.privateOnly:
            keys.update({topic.id, topic.slug, topic.name})
    return {key for key in keys if key}


def _is_public_topic_filter(config: DailyReviewConfig, topic: str | None) -> bool:
    if not topic:
        return True
    return topic.strip() in _public_topic_keys(config)


def _is_public_run(config: DailyReviewConfig, run: dict[str, Any]) -> bool:
    keys = _public_topic_keys(config)
    return any(str(run.get(field) or "") in keys for field in ("topicId", "topicSlug", "topicName"))


def _exclusive_access_key(request: Request) -> str:
    return (request.headers.get("X-Exclusive-Review-Key") or request.query_params.get("accessKey") or "").strip()


def _require_exclusive_access(config: DailyReviewConfig, request: Request) -> None:
    expected = (config.exclusiveAccessKey or "").strip()
    provided = _exclusive_access_key(request)
    if not expected or not provided or not hmac.compare_digest(provided, expected):
        raise ApiError(401, "EXCLUSIVE_ACCESS_REQUIRED", "专属综述访问密钥无效")


def _parse_beijing_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=BEIJING_TZ)
    return parsed.astimezone(BEIJING_TZ)


def _find_valid_literature_cdk(config: DailyReviewConfig, code: str) -> LiteratureSearchCdkConfig | None:
    provided = _usable_secret_override(code)
    if not provided:
        return None
    for cdk in _ensure_topics(config).literatureSearchCdks:
        if not cdk.enabled or not cdk.code:
            continue
        if not hmac.compare_digest(provided, cdk.code):
            continue
        expires_at = _parse_beijing_datetime(cdk.expiresAt)
        if expires_at and datetime.now(BEIJING_TZ) > expires_at:
            return None
        if cdk.usedCount >= cdk.maxUses:
            return None
        return cdk
    return None


def _literature_cdk_public_info(cdk: LiteratureSearchCdkConfig | None) -> dict[str, Any] | None:
    if not cdk:
        return None
    remaining = max(0, cdk.maxUses - cdk.usedCount)
    return {
        "id": cdk.id,
        "name": cdk.name,
        "enabled": cdk.enabled,
        "maxUses": cdk.maxUses,
        "usedCount": cdk.usedCount,
        "remainingUses": remaining,
        "expiresAt": cdk.expiresAt,
        "paperCountMax": cdk.paperCountMax,
        "literatureProvider": cdk.literatureProvider,
        "paperSearchSources": cdk.paperSearchSources,
        "note": cdk.note,
    }


def _consume_literature_cdk(config: DailyReviewConfig, cdk_id: str | None) -> None:
    if not cdk_id:
        return
    for cdk in config.literatureSearchCdks:
        if cdk.id == cdk_id:
            cdk.usedCount += 1
            _save_config(config)
            return


def _is_exclusive_topic(topic: ReviewTopicConfig) -> bool:
    return bool(topic.enabled and topic.privateOnly)


def _exclusive_topic_keys(config: DailyReviewConfig) -> set[str]:
    config = _ensure_topics(config)
    keys: set[str] = set()
    for topic in config.topics:
        if _is_exclusive_topic(topic):
            keys.update({topic.id, topic.slug, topic.name})
    return {key for key in keys if key}


def _is_exclusive_topic_filter(config: DailyReviewConfig, topic: str | None) -> bool:
    if not topic:
        return True
    return topic.strip() in _exclusive_topic_keys(config)


def _is_exclusive_run(config: DailyReviewConfig, run: dict[str, Any]) -> bool:
    keys = _exclusive_topic_keys(config)
    return any(str(run.get(field) or "") in keys for field in ("topicId", "topicSlug", "topicName"))


def _has_exclusive_access(config: DailyReviewConfig, request: Request) -> bool:
    expected = (config.exclusiveAccessKey or "").strip()
    provided = _exclusive_access_key(request)
    return bool(expected and provided and hmac.compare_digest(provided, expected))


def _can_access_run(config: DailyReviewConfig, run: dict[str, Any], request: Request) -> bool:
    if _is_public_run(config, run):
        return True
    if _is_exclusive_run(config, run):
        return _has_admin_access(request) or _has_exclusive_access(config, request)
    return _has_admin_access(request)


def _runs_for_asset(filename: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for run in _iter_runs():
        image = run.get("image") if isinstance(run.get("image"), dict) else {}
        image_url = str(image.get("url") or image.get("remoteUrl") or "")
        if image_url.endswith(f"/{filename}"):
            matches.append(run)
            continue
        for paper in run.get("papers") or []:
            if not isinstance(paper, dict):
                continue
            pdf_urls = [
                str(paper.get("pdfUrl") or ""),
                str(paper.get("pdfRemoteUrl") or ""),
            ]
            if any(url.endswith(f"/{filename}") for url in pdf_urls):
                matches.append(run)
                break
    return matches


def _require_asset_access(filename: str, request: Request) -> None:
    config = _load_config()
    runs = _runs_for_asset(filename)
    if not runs:
        return
    if any(_is_public_run(config, run) for run in runs):
        return
    if any(_is_exclusive_run(config, run) for run in runs):
        if _has_admin_access(request) or _has_exclusive_access(config, request):
            return
        raise ApiError(401, "EXCLUSIVE_ACCESS_REQUIRED", "专属综述资源需要访问密钥")
    if not _has_admin_access(request):
        raise ApiError(401, "ADMIN_AUTH_REQUIRED", "该资源需要管理员权限")


def _run_config_for_topic(config: DailyReviewConfig, topic: ReviewTopicConfig) -> DailyReviewConfig:
    data = config.model_dump()
    data.update(
        {
            "topic": topic.topic,
            "scheduleEnabled": topic.scheduleEnabled,
            "scheduleTime": topic.scheduleTime,
            "paperCount": topic.paperCount,
            "sinceYear": topic.sinceYear,
            "freshnessBoost": topic.freshnessBoost,
            "includeFullText": topic.includeFullText,
            "includeWeb": topic.includeWeb,
            "activeTopicId": topic.id,
        }
    )
    return _ensure_topics(DailyReviewConfig(**data))


def _sciverse_from_config(request: Request, config: DailyReviewConfig) -> SciverseClient:
    token = _usable_secret_override(request.headers.get("X-Sciverse-Key")) or config.sciverseApiToken or settings.sciverse_api_token
    return SciverseClient(request.app.state.sciverse_client._c, token=token)


def _sciverse_from_app(app: Any, config: DailyReviewConfig) -> SciverseClient:
    return SciverseClient(app.state.sciverse_client._c, token=config.sciverseApiToken or settings.sciverse_api_token)


def _sciverse_for_literature_search(request: Request | None, app: Any, config: DailyReviewConfig) -> SciverseClient:
    if request is not None:
        return _sciverse_from_config(request, config)
    return _sciverse_from_app(app, config)


def _llm_from_config(request: Request, config: DailyReviewConfig):
    return get_llm_client(
        request.headers.get("X-LLM-Key") or config.llm.apiKey,
        base_url=request.headers.get("X-LLM-Base-URL") or config.llm.baseUrl,
        model=request.headers.get("X-LLM-Model") or config.llm.model,
    )


def _llm_from_admin_config(config: DailyReviewConfig):
    return get_llm_client(config.llm.apiKey, base_url=config.llm.baseUrl, model=config.llm.model)


def _llm_from_literature_search_request(body: LiteratureOnlySearchRequest, config: DailyReviewConfig):
    llm = body.llm
    if llm and _usable_secret_override(llm.apiKey) and llm.baseUrl.strip() and llm.model.strip():
        return get_llm_client(llm.apiKey, base_url=llm.baseUrl, model=llm.model)
    cdk = _find_valid_literature_cdk(config, body.cdk or "")
    if cdk:
        return _llm_from_admin_config(config)
    raise ApiError(400, "LITERATURE_SEARCH_LLM_REQUIRED", "请输入可用 CDK，或填写自己的 OpenAI 兼容 LLM 配置")


def _translation_llm_from_config(config: DailyReviewConfig):
    return get_llm_client(
        config.translation.apiKey or config.llm.apiKey,
        base_url=config.translation.baseUrl or config.llm.baseUrl,
        model=config.translation.model or config.llm.model,
    )


def _usable_secret_override(value: str | None) -> str:
    secret = (value or "").strip()
    if not secret or "*" in secret or "..." in secret:
        return ""
    return secret


def _clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _looks_mojibake(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if text.count("?") >= max(3, len(text) // 5):
        return True
    return sum(1 for ch in text if ch in "ÃÂâåæçèä") >= 2


def _repair_mojibake(value: str) -> str:
    text = value.strip()
    if not _looks_mojibake(text):
        return text
    for encoding in ("latin1", "cp1252"):
        try:
            repaired = text.encode(encoding).decode("utf-8")
        except UnicodeError:
            continue
        if repaired and not _looks_mojibake(repaired):
            return repaired
    return text


def _authors(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else ([] if value is None else [value])
    names: list[str] = []
    for item in raw:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("display_name") or item.get("literal") or "")
        else:
            name = str(item)
        name = _clean_text(name)
        if name:
            names.append(name)
    return names


def _paper_id(paper: dict[str, Any]) -> str:
    for key in ("doi", "doc_id", "unique_id", "title"):
        value = str(paper.get(key) or "").strip().lower()
        if value:
            return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return hashlib.sha256(json.dumps(paper, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _canonical_doi(value: Any) -> str:
    doi = str(value or "").strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = doi.strip().strip(".")
    return doi.lower()


def _canonical_title(value: Any) -> str:
    title = _clean_text(value).lower()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", title)


def _canonical_url(value: Any, doi: str | None = None) -> str | None:
    url = str(value or "").strip()
    if doi and not url:
        return f"https://doi.org/{doi}"
    if url and not re.match(r"^https?://", url, re.IGNORECASE):
        if re.match(r"^10\.\d{4,9}/", url, re.IGNORECASE):
            return f"https://doi.org/{url}"
        return None
    return url or None


def _looks_like_pdf_url(url: str | None) -> bool:
    value = (url or "").strip().lower()
    if not value:
        return False
    parsed = urlparse(value)
    path = parsed.path.lower()
    query = parsed.query.lower()
    return ".pdf" in path or "/pdf/" in path or path.endswith("/pdf") or "pdf" in query


def _normalize_paper(raw: dict[str, Any]) -> dict[str, Any]:
    doi = _canonical_doi(raw.get("doi"))
    url = _canonical_url(raw.get("access_oa_url"), doi)
    source = str(raw.get("source") or "sciverse").strip().lower()
    pdf_url = _canonical_url(raw.get("pdf_url") or raw.get("pdfUrl"))
    if not pdf_url and url and _looks_like_pdf_url(url):
        pdf_url = url
    pdf_candidates = []
    if pdf_url:
        pdf_candidates.append({
            "url": pdf_url,
            "source": raw.get("pdf_source") or raw.get("pdfSource") or source,
            "license": raw.get("pdf_license") or raw.get("pdfLicense"),
        })
    return {
        "id": _paper_id(raw),
        "title": _clean_text(raw.get("title")),
        "authors": _authors(raw.get("author")),
        "year": raw.get("publication_published_year"),
        "publishedDate": raw.get("publication_published_date"),
        "venue": _clean_text(raw.get("publication_venue_name_unified")),
        "doi": doi or None,
        "url": url,
        "abstract": _clean_text(raw.get("abstract")),
        "citationCount": raw.get("citation_count"),
        "fwci": raw.get("fwci"),
        "influentialCitationCount": raw.get("influential_citation_count"),
        "docId": raw.get("doc_id"),
        "uniqueId": raw.get("unique_id"),
        "source": source,
        "sources": [source],
        "isLinkVerified": bool(raw.get("is_link_verified")),
        "pdfUrl": pdf_url,
        "pdfSource": raw.get("pdf_source") or raw.get("pdfSource") or (source if pdf_url else None),
        "pdfLicense": raw.get("pdf_license") or raw.get("pdfLicense"),
        "pdfCandidates": pdf_candidates,
        "openAccessPdf": bool(pdf_url or raw.get("openAccessPdf")),
        "evidenceSource": "abstract",
    }


def _merge_paper(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    sources = set(existing.get("sources") or [existing.get("source") or "unknown"])
    sources.update(incoming.get("sources") or [incoming.get("source") or "unknown"])
    merged["sources"] = sorted(str(source) for source in sources if source)
    merged["source"] = "+".join(merged["sources"])

    for key in ("title", "venue", "publishedDate", "doi", "url", "docId", "uniqueId", "pdfUrl", "pdfSource", "pdfLicense"):
        if not merged.get(key) and incoming.get(key):
            merged[key] = incoming[key]
    candidates: list[dict[str, Any]] = []
    seen_pdf_urls: set[str] = set()
    for candidate in (existing.get("pdfCandidates") or []) + (incoming.get("pdfCandidates") or []):
        if not isinstance(candidate, dict):
            continue
        candidate_url = _canonical_url(candidate.get("url"))
        if not candidate_url or candidate_url in seen_pdf_urls:
            continue
        seen_pdf_urls.add(candidate_url)
        candidates.append({
            "url": candidate_url,
            "source": _clean_text(candidate.get("source")) or "unknown",
            "license": _clean_text(candidate.get("license")) or None,
        })
    if not candidates and merged.get("pdfUrl"):
        candidates.append({
            "url": merged.get("pdfUrl"),
            "source": _clean_text(merged.get("pdfSource")) or "unknown",
            "license": _clean_text(merged.get("pdfLicense")) or None,
        })
    merged["pdfCandidates"] = candidates
    if len(_clean_text(incoming.get("abstract"))) > len(_clean_text(merged.get("abstract"))):
        merged["abstract"] = incoming.get("abstract")
    if len(_clean_text(incoming.get("snippet"))) > len(_clean_text(merged.get("snippet"))):
        merged["snippet"] = incoming.get("snippet")
    if len(incoming.get("authors") or []) > len(merged.get("authors") or []):
        merged["authors"] = incoming.get("authors")
    try:
        merged["citationCount"] = max(int(float(merged.get("citationCount") or 0)), int(float(incoming.get("citationCount") or 0)))
    except (TypeError, ValueError):
        merged["citationCount"] = merged.get("citationCount") or incoming.get("citationCount")
    try:
        merged["influentialCitationCount"] = max(
            int(float(merged.get("influentialCitationCount") or 0)),
            int(float(incoming.get("influentialCitationCount") or 0)),
        )
    except (TypeError, ValueError):
        merged["influentialCitationCount"] = merged.get("influentialCitationCount") or incoming.get("influentialCitationCount")
    merged["year"] = merged.get("year") or incoming.get("year")
    merged["fwci"] = merged.get("fwci") or incoming.get("fwci")
    merged["isLinkVerified"] = bool(merged.get("isLinkVerified") or incoming.get("isLinkVerified"))
    merged["openAccessPdf"] = bool(merged.get("openAccessPdf") or incoming.get("openAccessPdf") or merged.get("pdfUrl"))
    return merged


def _dedupe(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    secondary: dict[str, str] = {}
    out: list[dict[str, Any]] = []
    for paper in papers:
        title_key = _canonical_title(paper.get("title"))
        year = str(paper.get("year") or "")
        first_author = _canonical_title((paper.get("authors") or [""])[0])
        keys = [
            _canonical_doi(paper.get("doi")),
            str(paper.get("uniqueId") or "").strip().lower(),
            str(paper.get("docId") or "").strip().lower(),
            f"{title_key}:{year}" if title_key and year else "",
            f"{title_key}:{first_author}" if title_key and first_author else title_key,
        ]
        keys = [key for key in keys if key]
        primary = next((secondary[key] for key in keys if key in secondary), keys[0] if keys else "")
        if not primary:
            continue
        if primary in by_key:
            by_key[primary] = _merge_paper(by_key[primary], paper)
        else:
            by_key[primary] = paper
            out.append(paper)
        for key in keys:
            secondary[key] = primary
    return [by_key[key] for key in by_key]


BAD_TITLE_RE = re.compile(
    r"\b(erratum|corrigendum|correction|retraction|retracted|withdrawn|expression of concern|editorial|letter to|commentary|book review|conference abstract|profile|profiles)\b|"
    r"\b(selected papers|conference proceedings|proceedings of|special issue|volume \d+)\b|"
    r"^3d model of\b|^dataset\b|^supplementary\b",
    re.IGNORECASE,
)

BAD_VENUE_RE = re.compile(r"\b(zenodo|data repository|figshare|nih 3d|dryad|osf|protocols\.io)\b", re.IGNORECASE)

CIVIL_TOPIC_RE = re.compile(r"a^")

CIVIL_NEGATIVE_RE = re.compile(r"a^")


def _paper_text(paper: dict[str, Any]) -> str:
    return " ".join(
        str(paper.get(key) or "")
        for key in ("title", "venue", "abstract", "snippet")
    ).lower()


def _has_content_evidence(paper: dict[str, Any]) -> bool:
    return len(_clean_text(paper.get("abstract"))) >= 80 or len(_clean_text(paper.get("snippet"))) >= 80


def _topic_specific_groups(topic: str | None) -> list[tuple[tuple[str, ...], int]]:
    return []


def _topic_specific_hit_count(paper: dict[str, Any], topic: str | None = None) -> int:
    text = _paper_text(paper)
    return sum(1 for terms, _weight in _topic_specific_groups(topic) if any(term in text for term in terms))


_DYNAMIC_TERM_STOPWORDS = {
    "and", "or", "the", "for", "with", "from", "into", "using", "based", "past", "decade",
    "recent", "advances", "advance", "application", "applications", "review", "survey",
    "research", "study", "analysis", "method", "methods", "system", "systems",
}

_DYNAMIC_ANCHOR_STOPWORDS = _DYNAMIC_TERM_STOPWORDS | {
    "data", "driven", "digital", "twin", "structural", "structure", "structures", "health",
    "monitoring", "monitor", "sensor", "sensors", "smart", "engineering", "infrastructure",
    "prediction", "predictive", "assessment", "framework", "model", "models", "damage",
    "early", "warning", "life", "real", "time", "adaptive", "technology", "technologies",
    "performance", "reliability", "optimization", "diagnosis", "detection", "condition",
    "machine", "learning", "management", "remaining", "useful", "counting", "curve",
}


def _dynamic_topic_terms(topic: str | None, extra_queries: list[str] | None = None) -> list[str]:
    texts = [topic or "", *(extra_queries or [])]
    terms: set[str] = set()
    for value in texts:
        for quoted in re.findall(r'"([^"]{3,80})"', value):
            phrase = re.sub(r"\s+", " ", quoted.lower().replace("-", " ")).strip()
            if any(ch.isalpha() for ch in phrase) and not set(phrase.split()) <= _DYNAMIC_TERM_STOPWORDS:
                terms.add(phrase)
        lowered = re.sub(r"\b(and|or|not)\b", " ", value.lower())
        tokens = [
            token for token in re.findall(r"[a-z][a-z0-9]*", lowered.replace("-", " "))
            if len(token) >= 3 and token not in _DYNAMIC_TERM_STOPWORDS
        ]
        for token in re.findall(r"\b[A-Z]{2,8}\b", value):
            terms.add(token.lower())
        for size in (3, 2):
            for index in range(0, max(0, len(tokens) - size + 1)):
                phrase_tokens = tokens[index:index + size]
                if not phrase_tokens or set(phrase_tokens) <= _DYNAMIC_TERM_STOPWORDS:
                    continue
                terms.add(" ".join(phrase_tokens))
    return sorted(terms, key=lambda item: (-len(item.split()), -len(item), item))[:40]


def _dynamic_topic_hit_count(paper: dict[str, Any], dynamic_terms: list[str] | None = None) -> int:
    text = _paper_text(paper)
    return sum(1 for term in dynamic_terms or [] if term and term in text)


def _dynamic_topic_anchor_terms(dynamic_terms: list[str] | None = None) -> list[str]:
    anchors: set[str] = set()
    for term in dynamic_terms or []:
        for token in re.findall(r"[a-z][a-z0-9]*", term.lower()):
            if len(token) >= 4 and token not in _DYNAMIC_ANCHOR_STOPWORDS:
                anchors.add(token)
    return sorted(anchors, key=lambda item: (-len(item), item))[:24]


def _dynamic_topic_anchor_hit_count(paper: dict[str, Any], anchor_terms: list[str] | None = None) -> int:
    text = _paper_text(paper)
    return sum(1 for term in anchor_terms or [] if term and re.search(rf"\b{re.escape(term)}\b", text))


def _dynamic_topic_min_hits(dynamic_terms: list[str] | None = None) -> int:
    return 2 if len(dynamic_terms or []) >= 4 else 1


def _domain_relevance_score(paper: dict[str, Any], topic: str | None = None) -> int:
    if not topic:
        return 0
    text = _paper_text(paper)
    topic_text = topic.lower()
    score = 8 if topic_text and topic_text in text else 0
    latin_terms = re.findall(r"[a-z][a-z0-9-]{2,}", topic_text)
    chinese_terms: set[str] = set()
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", topic):
        chinese_terms.add(chunk.lower())
        for size in (2, 3, 4):
            if len(chunk) >= size:
                chinese_terms.update(chunk[i:i + size].lower() for i in range(0, len(chunk) - size + 1))
    for term in set(latin_terms).union(chinese_terms):
        if term and term in text:
            score += 2
    if CIVIL_NEGATIVE_RE.search(text):
        score -= 10
    return min(score, 20)


def _paper_quality_score(paper: dict[str, Any]) -> int:
    title = str(paper.get("title") or "")
    venue = str(paper.get("venue") or "")
    abstract = str(paper.get("abstract") or "").strip()
    if not title or BAD_TITLE_RE.search(title) or BAD_VENUE_RE.search(venue):
        return -10
    score = 0
    if len(abstract) >= 120:
        score += 5
    elif len(abstract) >= 40:
        score += 2
    else:
        score -= 4
    if len(_clean_text(paper.get("snippet"))) >= 80:
        score += 3
    if paper.get("doi"):
        score += 1
    if paper.get("docId"):
        score += 2
    if paper.get("isLinkVerified"):
        score += 2
    if len(paper.get("sources") or []) > 1:
        score += 1
    try:
        citations = int(float(paper.get("citationCount") or 0))
    except (TypeError, ValueError):
        citations = 0
    if citations >= 20:
        score += 3
    elif citations >= 3:
        score += 2
    elif citations > 0:
        score += 1
    if paper.get("year"):
        score += 1
    return score


def _clamp_score(value: int | float) -> int:
    return max(0, min(100, int(round(value))))


def _score_label(score: int) -> str:
    if score >= 80:
        return "high"
    if score >= 55:
        return "medium"
    return "low"


def _citation_count(paper: dict[str, Any]) -> int:
    try:
        return int(float(paper.get("citationCount") or 0))
    except (TypeError, ValueError):
        return 0


def _evidence_score(paper: dict[str, Any], texts: dict[str, str] | None = None) -> int:
    score = 0
    has_fulltext = bool((texts or {}).get(str(paper.get("id") or ""))) or paper.get("evidenceSource") == "fulltext"
    abstract_len = len(_clean_text(paper.get("abstract")))
    snippet_len = len(_clean_text(paper.get("snippet")))
    if has_fulltext:
        score += 38
    if abstract_len >= 120:
        score += 24
    elif abstract_len >= 40:
        score += 12
    if snippet_len >= 120:
        score += 12
    elif snippet_len >= 40:
        score += 6
    if paper.get("doi"):
        score += 8
    if paper.get("docId"):
        score += 8
    if paper.get("isLinkVerified"):
        score += 5
    if len(paper.get("sources") or []) >= 2:
        score += 5
    return _clamp_score(score)


def _quality_score_100(paper: dict[str, Any]) -> int:
    title = str(paper.get("title") or "")
    venue = str(paper.get("venue") or "")
    if not title or BAD_TITLE_RE.search(title) or BAD_VENUE_RE.search(venue):
        return 0
    score = 15
    if len(_clean_text(paper.get("abstract"))) >= 120:
        score += 14
    elif len(_clean_text(paper.get("abstract"))) >= 40:
        score += 7
    if paper.get("doi"):
        score += 10
    if paper.get("docId"):
        score += 8
    if len(paper.get("sources") or []) >= 2:
        score += 8
    if paper.get("isLinkVerified"):
        score += 5
    citations = _citation_count(paper)
    if citations >= 100:
        score += 26
    elif citations >= 50:
        score += 22
    elif citations >= 20:
        score += 16
    elif citations >= 3:
        score += 10
    elif citations > 0:
        score += 5
    try:
        year = int(float(paper.get("year") or 0))
    except (TypeError, ValueError):
        year = 0
    current_year = datetime.now(timezone.utc).year
    if year >= current_year - 2:
        score += 14
    elif year >= current_year - 5:
        score += 10
    elif year:
        score += 5
    return _clamp_score(score)


def _relevance_score_100(
    paper: dict[str, Any],
    topic: str | None,
    dynamic_terms: list[str] | None = None,
) -> int:
    domain_score = max(0, _domain_relevance_score(paper, topic))
    topic_hits = _topic_specific_hit_count(paper, topic)
    dynamic_hits = _dynamic_topic_hit_count(paper, dynamic_terms)
    anchor_hits = _dynamic_topic_anchor_hit_count(paper, _dynamic_topic_anchor_terms(dynamic_terms))
    score = min(35, domain_score * 4)
    score += min(25, topic_hits * 12)
    score += min(25, dynamic_hits * 6)
    score += min(15, anchor_hits * 8)
    if _has_content_evidence(paper):
        score += 5
    return _clamp_score(score)


def _annotate_evidence_scores(
    papers: list[dict[str, Any]],
    topic: str | None,
    dynamic_terms: list[str] | None,
    texts: dict[str, str] | None = None,
) -> None:
    for paper in papers:
        relevance = _relevance_score_100(paper, topic, dynamic_terms)
        quality = _quality_score_100(paper)
        evidence = _evidence_score(paper, texts)
        paper["relevanceScore"] = relevance
        paper["qualityScore"] = quality
        paper["evidenceScore"] = evidence
        paper["evidenceScores"] = {
            "relevance": {"score": relevance, "label": _score_label(relevance)},
            "quality": {"score": quality, "label": _score_label(quality)},
            "evidence": {"score": evidence, "label": _score_label(evidence)},
        }
        paper["scoreTags"] = [
            f"relevance:{_score_label(relevance)}",
            f"quality:{_score_label(quality)}",
            f"evidence:{_score_label(evidence)}",
        ]


def _ensure_run_evidence_scores(run: dict[str, Any] | None) -> dict[str, Any] | None:
    if not run:
        return run
    papers = run.get("papers")
    if not isinstance(papers, list) or not papers:
        return run
    if all(
        isinstance(paper, dict)
        and paper.get("relevanceScore") is not None
        and paper.get("qualityScore") is not None
        and paper.get("evidenceScore") is not None
        for paper in papers
    ):
        return run
    query = run.get("query") if isinstance(run.get("query"), dict) else {}
    topic = str(run.get("topicName") or query.get("topic") or "")
    extra_queries = query.get("llmSearchQueries")
    if not isinstance(extra_queries, list):
        extra_queries = []
    _annotate_evidence_scores(
        [paper for paper in papers if isinstance(paper, dict)],
        topic,
        _dynamic_topic_terms(topic, [str(item) for item in extra_queries]),
    )
    return run


def _hash_text(value: Any, length: int = 16) -> str:
    text = _clean_text(value).lower()
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def _paper_index_identity(paper: dict[str, Any]) -> str:
    doi = _canonical_doi(paper.get("doi"))
    if doi:
        return f"doi:{doi}"
    unique_id = str(paper.get("uniqueId") or paper.get("docId") or "").strip().lower()
    if unique_id:
        return f"uid:{unique_id}"
    title_hash = _hash_text(paper.get("title"))
    return f"title:{title_hash or _paper_id(paper)}"


def _paper_index_key(topic_id: str, paper: dict[str, Any]) -> str:
    return f"{topic_id}:{_paper_index_identity(paper)}"


def _load_paper_index() -> dict[str, dict[str, Any]]:
    path = _paper_index_path()
    if not path.exists():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("key"):
            rows[str(item["key"])] = item
    return rows


def _write_paper_index(index: dict[str, dict[str, Any]]) -> None:
    path = _paper_index_path()
    rows = [index[key] for key in sorted(index)]
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def _save_draft(draft: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    draft["updatedAt"] = now
    draft.setdefault("createdAt", now)
    draft.setdefault("status", "running")
    draft.setdefault("canResume", False)
    path = _draft_path(str(draft["draftId"]), str(draft.get("topicId") or "unknown-topic"))
    path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    return draft


def _load_draft(draft_id: str) -> dict[str, Any]:
    path = _draft_path(draft_id)
    if not path.exists():
        raise ApiError(404, "DRAFT_NOT_FOUND", "未找到暂存任务")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiError(500, "DRAFT_READ_FAILED", str(exc)) from exc
    if not isinstance(data, dict):
        raise ApiError(500, "DRAFT_INVALID", "暂存任务格式错误")
    return data


def _draft_summary(draft: dict[str, Any]) -> DailyReviewDraftSummary:
    papers = draft.get("papers") if isinstance(draft.get("papers"), list) else []
    texts = draft.get("texts") if isinstance(draft.get("texts"), dict) else {}
    return DailyReviewDraftSummary(
        draftId=str(draft.get("draftId") or ""),
        topicId=str(draft.get("topicId") or ""),
        topicSlug=draft.get("topicSlug"),
        topicName=draft.get("topicName"),
        topic=str(draft.get("topic") or ""),
        stage=str(draft.get("stage") or "unknown"),
        status=str(draft.get("status") or "unknown"),
        canResume=bool(draft.get("canResume")),
        createdAt=str(draft.get("createdAt") or ""),
        updatedAt=str(draft.get("updatedAt") or ""),
        paperCount=len(papers),
        fullTextFetched=len(texts),
        error=draft.get("error"),
    )


def _list_drafts(config: DailyReviewConfig | None = None) -> list[DailyReviewDraftSummary]:
    config = _ensure_topics(config or _load_config())
    allowed = {topic.id for topic in config.topics}
    items: list[DailyReviewDraftSummary] = []
    paths = [*_drafts_dir().glob("*.json"), *_drafts_dir().glob("*/*.json")]
    for path in sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            draft = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(draft, dict) and str(draft.get("topicId") or "") in allowed:
            items.append(_draft_summary(draft))
    return items


def _delete_draft(draft_id: str) -> None:
    path = _draft_path(draft_id)
    if path.exists():
        path.unlink()


def _recent_topic_runs(topic: ReviewTopicConfig, limit: int = 7) -> list[dict[str, Any]]:
    aliases = {topic.id, topic.slug, topic.name}
    runs = [
        run for run in reversed(_iter_runs())
        if aliases & {str(run.get("topicId") or ""), str(run.get("topicSlug") or ""), str(run.get("topicName") or "")}
    ]
    return runs[:limit]


def _recent_paper_keys(topic: ReviewTopicConfig, limit: int = 7) -> set[str]:
    keys: set[str] = set()
    for run in _recent_topic_runs(topic, limit=limit):
        for paper in run.get("papers") or []:
            if isinstance(paper, dict):
                keys.add(_paper_index_key(topic.id, paper))
    return keys


def _novelty_score(paper: dict[str, Any], index_entry: dict[str, Any] | None, recently_used: bool) -> int:
    score = 0
    if index_entry is None:
        score += 35
    else:
        score += 10
    if not recently_used:
        score += 18
    try:
        year = int(float(paper.get("year") or 0))
    except (TypeError, ValueError):
        year = 0
    current_year = datetime.now(timezone.utc).year
    if year >= current_year:
        score += 18
    elif year >= current_year - 1:
        score += 15
    elif year >= current_year - 3:
        score += 10
    elif year:
        score += 5
    score += min(12, int((paper.get("qualityScore") or _quality_score_100(paper)) * 0.12))
    score += min(10, int((paper.get("evidenceScore") or _evidence_score(paper)) * 0.10))
    if paper.get("doi"):
        score += 4
    if len(paper.get("sources") or []) >= 2:
        score += 3
    used_count = int((index_entry or {}).get("usedCount") or 0)
    if used_count >= 5:
        score -= 15
    elif used_count >= 2:
        score -= 8
    return _clamp_score(score)


def _daily_mode_from_delta(delta: dict[str, Any], target_count: int, topic: ReviewTopicConfig | None = None) -> str:
    high_new = int(delta.get("highNoveltyCount") or 0)
    new_count = int(delta.get("newEvidenceCount") or 0)
    repeat_ratio = float(delta.get("repeatRatio") or 0)
    default_floor = min(10, max(3, int(target_count * 0.3)))
    strong_floor = int(topic.minHighNoveltyCount) if topic and topic.minHighNoveltyCount else default_floor
    max_repeat = float(topic.maxRepeatRatio) if topic else 0.75
    allow_deep_dive = bool(topic.allowTopicDeepDive) if topic else True
    allow_short = bool(topic.allowNoSignificantUpdate) if topic else True
    if high_new >= strong_floor and repeat_ratio <= max_repeat:
        return "fresh_daily"
    if high_new >= 3 or new_count >= max(5, int(target_count * 0.15)):
        return "delta_brief"
    if allow_deep_dive and int(delta.get("paperCount") or 0) >= 20:
        return "topic_deep_dive"
    return "no_significant_update" if allow_short else "delta_brief"


def _annotate_daily_delta(papers: list[dict[str, Any]], topic: ReviewTopicConfig, target_count: int) -> dict[str, Any]:
    index = _load_paper_index()
    recent_keys = _recent_paper_keys(topic)
    new_count = 0
    reused_count = 0
    high_novelty = 0
    novelty_total = 0
    for paper in papers:
        key = _paper_index_key(topic.id, paper)
        entry = index.get(key)
        seen_before = entry is not None
        recently_used = key in recent_keys
        score = _novelty_score(paper, entry, recently_used)
        paper["paperIndexKey"] = key
        paper["noveltyScore"] = score
        paper["seenBefore"] = seen_before
        paper["recentlyUsed"] = recently_used
        paper["usedCount"] = int((entry or {}).get("usedCount") or 0)
        paper.setdefault("evidenceScores", {})
        paper["evidenceScores"]["novelty"] = {"score": score, "label": _score_label(score)}
        paper.setdefault("scoreTags", [])
        paper["scoreTags"].append(f"novelty:{_score_label(score)}")
        novelty_total += score
        if seen_before:
            reused_count += 1
        else:
            new_count += 1
        if score >= 70:
            high_novelty += 1
    paper_count = len(papers)
    delta = {
        "mode": "",
        "paperCount": paper_count,
        "newEvidenceCount": new_count,
        "reusedEvidenceCount": reused_count,
        "highNoveltyCount": high_novelty,
        "repeatRatio": round(reused_count / paper_count, 3) if paper_count else 0,
        "averageNoveltyScore": round(novelty_total / paper_count, 1) if paper_count else 0,
        "recentRunCount": len(_recent_topic_runs(topic)),
    }
    delta["thresholds"] = {
        "minHighNoveltyCount": topic.minHighNoveltyCount or min(10, max(3, int(target_count * 0.3))),
        "maxRepeatRatio": topic.maxRepeatRatio,
        "allowTopicDeepDive": topic.allowTopicDeepDive,
        "allowNoSignificantUpdate": topic.allowNoSignificantUpdate,
    }
    delta["mode"] = _daily_mode_from_delta(delta, target_count, topic)
    return delta


def _persist_daily_delta(topic: ReviewTopicConfig, papers: list[dict[str, Any]], run_id: str, delta: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    index = _load_paper_index()
    for paper in papers:
        key = str(paper.get("paperIndexKey") or _paper_index_key(topic.id, paper))
        old = index.get(key) or {}
        index[key] = {
            **old,
            "key": key,
            "topicId": topic.id,
            "topicSlug": topic.slug,
            "identity": _paper_index_identity(paper),
            "doi": _canonical_doi(paper.get("doi")),
            "titleHash": _hash_text(paper.get("title")),
            "abstractHash": _hash_text(paper.get("abstract")),
            "source": paper.get("source"),
            "sources": paper.get("sources") or [],
            "firstSeenAt": old.get("firstSeenAt") or now,
            "lastSeenAt": now,
            "lastUsedAt": now,
            "lastRunId": run_id,
            "usedCount": int(old.get("usedCount") or 0) + 1,
            "relevanceScore": paper.get("relevanceScore"),
            "qualityScore": paper.get("qualityScore"),
            "evidenceScore": paper.get("evidenceScore"),
            "noveltyScore": paper.get("noveltyScore"),
        }
    _write_paper_index(index)

    state_path = _topic_daily_state_path()
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except json.JSONDecodeError:
        state = {}
    state[topic.id] = {
        "topicId": topic.id,
        "topicSlug": topic.slug,
        "lastRunId": run_id,
        "lastRunAt": now,
        "lastDelta": delta,
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    _daily_delta_runs_path().open("a", encoding="utf-8").write(
        json.dumps({"runId": run_id, "topicId": topic.id, "createdAt": now, **delta}, ensure_ascii=False) + "\n"
    )


def _mode_label(mode: str) -> str:
    return {
        "fresh_daily": "新增日报",
        "delta_brief": "差异简报",
        "topic_deep_dive": "专题深挖",
        "no_significant_update": "监测短报",
    }.get(mode, "滚动日报")


def _fallback_subtitle(topic: str, delta: dict[str, Any], papers: list[dict[str, Any]]) -> str:
    mode = str(delta.get("mode") or "")
    top_title = _clean_text((papers[0] or {}).get("title")) if papers else ""
    if mode == "fresh_daily":
        return f"今日新增：{topic}高质量证据更新"
    if mode == "delta_brief":
        return f"本期变化：{topic}证据增量观察"
    if mode == "topic_deep_dive":
        return f"专题观察：{top_title[:24] or topic}"
    return f"监测短报：{topic}暂无显著新增"


async def _generate_daily_subtitle(
    llm: Any,
    topic_config: ReviewTopicConfig,
    topic: str,
    papers: list[dict[str, Any]],
    delta: dict[str, Any],
    progress: ProgressCallback | None = None,
) -> str:
    if progress:
        progress("小标题生成", "正在生成本期动态小标题", 76, len(papers), len(papers), "indeterminate", "用于降低同主题日报重复感")
    samples = "\n".join(
        f"- {paper.get('title') or 'Untitled'} ({paper.get('year') or 'n.d.'}, novelty {paper.get('noveltyScore', 'n/a')})"
        for paper in papers[:12]
    )
    recent_titles: list[str] = []
    for run in _recent_topic_runs(topic_config, limit=5):
        run_delta = run.get("dailyDelta") if isinstance(run.get("dailyDelta"), dict) else {}
        recent_titles.append(f"- {run_delta.get('subtitle') or run.get('subtitle') or run.get('topic') or ''}")
    recent = "\n".join(recent_titles)
    subtopics = "\n".join(f"- {item}" for item in (topic_config.subtopicPool or [])[:12])
    messages = [
        {"role": "system", "content": "你是学术日报编辑，只输出一个克制、准确的中文小标题。"},
        {"role": "user", "content": f"""
主主题: {topic}
日报模式: {_mode_label(str(delta.get("mode") or ""))}
新证据: {delta.get("newEvidenceCount")}，复用证据: {delta.get("reusedEvidenceCount")}，高新颖度证据: {delta.get("highNoveltyCount")}。
管理员指定子议题池，优先从中选择但不得偏离候选文献:
{subtopics or "未配置"}
候选文献:
{samples}
最近小标题，避免重复:
{recent or "无"}

请生成一个 18-34 个汉字的中文小标题。必须紧扣主主题和候选文献；如果使用子议题池，只能选择候选文献确实支持的子议题。不要使用“颠覆、革命、突破”等夸大词；新增不足时要诚实体现滚动观察或专题深挖。只输出小标题本身。
"""},
    ]
    try:
        subtitle = _clean_text(await llm.complete(messages, temperature=0.2, max_tokens=200))
    except Exception:  # noqa: BLE001
        subtitle = ""
    subtitle = subtitle.strip(" \n\r\t\"'“”‘’#：:")
    return subtitle[:80] or _fallback_subtitle(topic, delta, papers)


def _rank_quality_papers(
    papers: list[dict[str, Any]],
    *,
    allow_weak: bool = False,
    topic: str | None = None,
    dynamic_terms: list[str] | None = None,
) -> list[dict[str, Any]]:
    scored = [(paper, _paper_quality_score(paper)) for paper in papers]
    threshold = 1 if allow_weak else 3
    topic_groups = _topic_specific_groups(topic)
    min_topic_hits = 2 if len(topic_groups) >= 3 else 1
    require_dynamic = bool(dynamic_terms) and not topic_groups
    min_dynamic_hits = _dynamic_topic_min_hits(dynamic_terms)
    dynamic_anchor_terms = _dynamic_topic_anchor_terms(dynamic_terms)
    require_anchor = bool(dynamic_anchor_terms)
    if topic and (CIVIL_TOPIC_RE.search(topic) or topic_groups or require_dynamic):
        kept = [
            paper for paper, score in scored
            if score >= threshold
            and (
                _domain_relevance_score(paper, topic) >= (5 if allow_weak else 7)
                or (require_dynamic and _dynamic_topic_hit_count(paper, dynamic_terms) >= min_dynamic_hits)
            )
            and (not topic_groups or _topic_specific_hit_count(paper, topic) >= min_topic_hits)
            and (not require_dynamic or _dynamic_topic_hit_count(paper, dynamic_terms) >= min_dynamic_hits)
            and (not require_anchor or _dynamic_topic_anchor_hit_count(paper, dynamic_anchor_terms) >= 1)
            and (_has_content_evidence(paper) or _domain_relevance_score(paper, topic) >= 12)
        ]
    else:
        kept = [paper for paper, score in scored if score >= threshold and (_has_content_evidence(paper) or allow_weak)]
    if not kept and allow_weak:
        kept = [
            paper for paper, score in scored
            if score > -10
            and (
                not topic
                or not (CIVIL_TOPIC_RE.search(topic) or topic_groups or require_dynamic)
                or _domain_relevance_score(paper, topic) >= 5
                or (require_dynamic and _dynamic_topic_hit_count(paper, dynamic_terms) >= min_dynamic_hits)
            )
            and (not topic_groups or _topic_specific_hit_count(paper, topic) >= min_topic_hits)
            and (not require_dynamic or _dynamic_topic_hit_count(paper, dynamic_terms) >= min_dynamic_hits)
            and (not require_anchor or _dynamic_topic_anchor_hit_count(paper, dynamic_anchor_terms) >= 1)
        ]
    return sorted(
        kept,
        key=lambda paper: (
            _relevance_score_100(paper, topic, dynamic_terms),
            _quality_score_100(paper),
            _evidence_score(paper),
            _dynamic_topic_anchor_hit_count(paper, dynamic_anchor_terms),
            _dynamic_topic_hit_count(paper, dynamic_terms),
            _domain_relevance_score(paper, topic),
            1 if _has_content_evidence(paper) else 0,
            _paper_quality_score(paper),
            int(float(paper.get("citationCount") or 0)) if str(paper.get("citationCount") or "0").replace(".", "", 1).isdigit() else 0,
            int(paper.get("year") or 0),
        ),
        reverse=True,
    )


async def _is_reachable_url(client: httpx.AsyncClient, url: str) -> tuple[bool, int | None]:
    try:
        response = await client.head(url, follow_redirects=True, timeout=8.0)
        if response.status_code in {405, 429} or response.status_code >= 500:
            response = await client.get(url, follow_redirects=True, timeout=10.0, headers={"Range": "bytes=0-2048"})
        return response.status_code < 400, response.status_code
    except httpx.HTTPError:
        return False, None


async def _probe_pdf_url(client: httpx.AsyncClient, url: str) -> tuple[bool, str | None, str | None]:
    try:
        _assert_public_pdf_url(url)
    except RuntimeError as exc:
        return False, None, str(exc)
    try:
        response = await client.head(url, follow_redirects=True, timeout=10.0)
        if response.status_code in {405, 403, 429} or response.status_code >= 500:
            response = await client.get(url, follow_redirects=True, timeout=12.0, headers={"Range": "bytes=0-2048"})
        try:
            _assert_public_pdf_url(str(response.url))
        except RuntimeError as exc:
            return False, None, str(exc)
        content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        final_url = str(response.url)
        if response.status_code >= 400:
            return False, final_url, f"HTTP {response.status_code}"
        if content_type == "application/pdf" or _looks_like_pdf_url(final_url):
            return True, final_url, None
        return False, final_url, f"content-type={content_type or 'unknown'}"
    except httpx.HTTPError as exc:
        return False, None, str(exc) or exc.__class__.__name__


def _pdf_cache_code(paper: dict[str, Any], remote_url: str) -> str:
    source = "|".join(
        [
            _canonical_doi(paper.get("doi")),
            _canonical_title(paper.get("title")),
            str(paper.get("uniqueId") or ""),
            str(paper.get("docId") or ""),
            remote_url,
        ]
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]


def _find_cached_pdf(paper: dict[str, Any], remote_url: str) -> tuple[str, str, int] | None:
    code = _pdf_cache_code(paper, remote_url)
    existing = sorted(_pdf_assets_dir().glob(f"{code}-*.pdf"))
    if not existing:
        return None
    path = existing[0]
    return _pdf_asset_url(path.name), code, path.stat().st_size


async def _download_open_pdf(client: httpx.AsyncClient, url: str) -> tuple[str, bytes]:
    current = url
    for _ in range(5):
        _assert_public_pdf_url(current)
        async with client.stream("GET", current, follow_redirects=False, headers={"Accept": "application/pdf,*/*"}) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise RuntimeError("pdf redirect without location")
                current = urljoin(current, location)
                continue
            _assert_public_pdf_url(str(response.url))
            if response.status_code >= 400:
                raise RuntimeError(f"pdf fetch failed {response.status_code}")
            content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > settings.max_cached_pdf_bytes:
                raise RuntimeError("pdf is too large")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > settings.max_cached_pdf_bytes:
                    raise RuntimeError("pdf is too large")
            if not data:
                raise RuntimeError("pdf response is empty")
            if not bytes(data[:5]).startswith(b"%PDF-"):
                raise RuntimeError(f"response is not a valid PDF: content-type={content_type or 'unknown'}")
            return str(response.url), bytes(data)
    raise RuntimeError("pdf redirect chain is too long")


async def _cache_open_pdf(paper: dict[str, Any], remote_url: str) -> tuple[str, str, int, bool, str]:
    code = _pdf_cache_code(paper, remote_url)
    assets_dir = _pdf_assets_dir()
    existing = sorted(assets_dir.glob(f"{code}-*.pdf"))
    if existing:
        path = existing[0]
        return _pdf_asset_url(path.name), code, path.stat().st_size, True, remote_url
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
        final_url, data = await _download_open_pdf(client, remote_url)
    digest = hashlib.sha256(data).hexdigest()[:16]
    filename = f"{code}-{digest}.pdf"
    path = assets_dir / filename
    path.write_bytes(data)
    return _pdf_asset_url(filename), code, len(data), False, final_url


def _paper_from_pdf_request(body: PdfResolveRequest, request: Request | None = None, config: DailyReviewConfig | None = None) -> dict[str, Any] | None:
    paper: dict[str, Any] | None = None
    if body.runId:
        run = _find_run(body.runId)
        if run:
            if request is not None:
                resolved_config = config or _load_config()
                if not _can_access_run(resolved_config, run, request):
                    raise ApiError(404, "RUN_NOT_FOUND", "未找到该期日报")
            candidates = run.get("papers") or []
            if body.paperId:
                paper = next((item for item in candidates if str(item.get("id") or "") == body.paperId), None)
            if paper is None and body.doi:
                doi = _canonical_doi(body.doi)
                paper = next((item for item in candidates if _canonical_doi(item.get("doi")) == doi), None)
            if paper is None and body.title:
                title = _canonical_title(body.title)
                paper = next((item for item in candidates if _canonical_title(item.get("title")) == title), None)
    if paper:
        return dict(paper)
    if body.doi or body.title or body.url:
        return {
            "doi": _canonical_doi(body.doi),
            "title": _clean_text(body.title),
            "url": _canonical_url(body.url, _canonical_doi(body.doi) or None),
        }
    return None


def _pdf_not_found_result(detail: str | None = None, oa_status: str | None = None) -> PdfResolveResult:
    return PdfResolveResult(
        ok=False,
        isOpenAccess=False,
        oaStatus=oa_status,
        message="未找到合法开放 PDF。可通过 DOI 或出版社页面查看访问选项。",
        detail=detail,
    )


async def _resolve_open_pdf_for_paper(paper: dict[str, Any]) -> PdfResolveResult:
    candidates: list[tuple[str, str, str | None, str | None]] = []

    def add_candidate(url: Any, source: str, license_value: Any = None, oa_status: Any = None) -> None:
        canonical = _canonical_url(url)
        if canonical and canonical not in {item[0] for item in candidates}:
            candidates.append((canonical, source, _clean_text(license_value) or None, _clean_text(oa_status) or None))

    for candidate in paper.get("pdfCandidates") or []:
        if isinstance(candidate, dict):
            add_candidate(
                candidate.get("url"),
                _clean_text(candidate.get("source")) or _clean_text(paper.get("pdfSource")) or "paper",
                candidate.get("license") or paper.get("pdfLicense"),
            )
    add_candidate(paper.get("pdfUrl"), _clean_text(paper.get("pdfSource")) or "paper", paper.get("pdfLicense"))
    url = _canonical_url(paper.get("url"))
    if url and (_looks_like_pdf_url(url) or paper.get("openAccessPdf")):
        add_candidate(url, _clean_text(paper.get("source")) or "paper")

    doi = _canonical_doi(paper.get("doi"))
    unpaywall_oa_status = _clean_text(paper.get("unpaywallOaStatus")) or None
    if doi and settings.unpaywall_email:
        async with httpx.AsyncClient(timeout=12.0) as client:
            searcher = PaperSearchClient(client)
            try:
                unpaywall = await searcher.lookup_unpaywall(doi)
            except Exception as exc:  # noqa: BLE001
                unpaywall = None
                unpaywall_oa_status = unpaywall_oa_status or f"lookup failed: {str(exc) or exc.__class__.__name__}"
        if unpaywall:
            unpaywall_oa_status = _clean_text(unpaywall.get("oa_status")) or unpaywall_oa_status
            if unpaywall.get("is_oa"):
                locations = [unpaywall.get("best_oa_location") or {}]
                locations.extend(unpaywall.get("oa_locations") or [])
                for location in locations:
                    if not isinstance(location, dict):
                        continue
                    add_candidate(
                        location.get("url_for_pdf"),
                        "unpaywall",
                        location.get("license"),
                        unpaywall_oa_status,
                    )

    if not candidates:
        return _pdf_not_found_result(oa_status=unpaywall_oa_status)

    async with httpx.AsyncClient(timeout=12.0) as client:
        last_detail: str | None = None
        for candidate_url, source, license_value, oa_status in candidates:
            cached_pdf = _find_cached_pdf(paper, candidate_url)
            if cached_pdf:
                local_url, code, size = cached_pdf
                return PdfResolveResult(
                    ok=True,
                    url=local_url,
                    remoteUrl=candidate_url,
                    code=code,
                    source=source,
                    isOpenAccess=True,
                    oaStatus=oa_status or unpaywall_oa_status,
                    license=license_value,
                    cached=True,
                    bytes=size,
                    message="已复用本地缓存的合法开放获取 PDF。",
                )
            ok, final_url, detail = await _probe_pdf_url(client, candidate_url)
            if ok and final_url:
                try:
                    local_url, code, size, was_cached, remote_url = await _cache_open_pdf(paper, final_url)
                except RuntimeError as exc:
                    last_detail = str(exc) or exc.__class__.__name__
                    continue
                return PdfResolveResult(
                    ok=True,
                    url=local_url,
                    remoteUrl=remote_url,
                    code=code,
                    source=source,
                    isOpenAccess=True,
                    oaStatus=oa_status or unpaywall_oa_status,
                    license=license_value,
                    cached=was_cached,
                    bytes=size,
                    message="已找到合法开放获取 PDF 链接。",
                )
            last_detail = detail or last_detail
    return _pdf_not_found_result(detail=last_detail, oa_status=unpaywall_oa_status)


async def _prefetch_open_pdfs(papers: list[dict[str, Any]], progress: ProgressCallback | None = None) -> list[dict[str, Any]]:
    if not papers:
        return papers
    semaphore = asyncio.Semaphore(3)
    done = 0
    found = 0

    async def prefetch_one(paper: dict[str, Any]) -> dict[str, Any]:
        nonlocal done, found
        async with semaphore:
            result = await _resolve_open_pdf_for_paper(paper)
        if result.ok and result.url:
            paper["pdfAvailable"] = True
            paper["pdfUrl"] = result.url
            paper["pdfRemoteUrl"] = result.remoteUrl
            paper["pdfCode"] = result.code
            paper["pdfSource"] = result.source
            paper["pdfLicense"] = result.license
            paper["pdfBytes"] = result.bytes
            paper["pdfCached"] = result.cached
            paper["openAccessPdf"] = True
        else:
            paper["pdfAvailable"] = False
            paper["pdfStatus"] = result.message
            paper["pdfDetail"] = result.detail
            paper["openAccessPdf"] = bool(paper.get("openAccessPdf"))
        done += 1
        found += 1 if paper.get("pdfAvailable") else 0
        if progress and (done % 5 == 0 or done == len(papers)):
            progress(
                "PDF 预缓存",
                f"已检查 {done}/{len(papers)} 篇高质量证据，成功缓存 {found} 篇开放 PDF",
                min(68, 58 + int(done / max(1, len(papers)) * 10)),
                done,
                len(papers),
                "determinate",
                "同一篇文献任一来源成功下载后立即停止尝试其他来源",
            )
        return paper

    results = await asyncio.gather(*(prefetch_one(paper) for paper in papers), return_exceptions=True)
    out: list[dict[str, Any]] = []
    for index, item in enumerate(results):
        if isinstance(item, Exception):
            paper = papers[index]
            paper["pdfAvailable"] = False
            paper["pdfStatus"] = "PDF 预缓存失败"
            paper["pdfDetail"] = str(item) or item.__class__.__name__
            out.append(paper)
        else:
            out.append(item)
    return out


async def _validate_paper_links(papers: list[dict[str, Any]], progress: ProgressCallback | None = None) -> list[dict[str, Any]]:
    if not settings.paper_search_validate_links:
        return papers
    semaphore = asyncio.Semaphore(12)

    async with httpx.AsyncClient(timeout=10.0) as client:
        paper_search = PaperSearchClient(client)

        async def check(paper: dict[str, Any]) -> dict[str, Any] | None:
            doi = _canonical_doi(paper.get("doi"))
            url = f"https://doi.org/{doi}" if doi else _canonical_url(paper.get("url"))
            if not url:
                return None
            if doi and settings.unpaywall_email:
                try:
                    unpaywall = await paper_search.lookup_unpaywall(doi)
                except Exception as exc:
                    log.warning("Unpaywall lookup failed: doi=%s error=%s", doi, str(exc) or exc.__class__.__name__)
                    unpaywall = None
                if unpaywall:
                    best = unpaywall.get("best_oa_location") or {}
                    oa_url = best.get("url_for_pdf") or best.get("url")
                    if oa_url:
                        url = str(oa_url)
                    if best.get("url_for_pdf"):
                        paper["pdfUrl"] = _canonical_url(best.get("url_for_pdf"))
                        paper["pdfSource"] = "unpaywall"
                        paper["pdfLicense"] = best.get("license")
                        paper["openAccessPdf"] = True
                    paper["unpaywallOaStatus"] = unpaywall.get("oa_status")
                    paper["unpaywallIsOa"] = unpaywall.get("is_oa")
                    sources = set(paper.get("sources") or [paper.get("source") or "unknown"])
                    sources.add("unpaywall")
                    paper["sources"] = sorted(str(source) for source in sources if source)
                    paper["source"] = "+".join(paper["sources"])
            if paper.get("isLinkVerified"):
                paper["url"] = url
                return paper
            async with semaphore:
                ok, status = await _is_reachable_url(client, url)
            if not ok:
                paper["isLinkVerified"] = False
                paper["linkStatus"] = status
                if doi:
                    paper["doi"] = doi
                    paper["url"] = url
                    return paper
                return None
            paper["doi"] = doi or paper.get("doi")
            paper["url"] = url
            paper["isLinkVerified"] = True
            paper["linkStatus"] = status
            return paper

        tasks = [check(paper) for paper in papers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    verified: list[dict[str, Any]] = []
    for index, result in enumerate(results, 1):
        if isinstance(result, Exception):
            log.warning("Paper link validation failed: %s", str(result) or result.__class__.__name__)
        elif result is not None:
            verified.append(result)
        if progress and (index % 20 == 0 or index == len(results)):
            progress(
                "链接校验",
                f"已校验 {index}/{len(results)} 条 DOI/URL，保留 {len(verified)} 条可访问文献",
                min(55, 46 + int(index / max(1, len(results)) * 9)),
                len(verified),
                len(results),
                "determinate",
                "真实请求 DOI/URL，过滤不可访问记录",
            )
    return verified


def _search_query(topic: str) -> str:
    return _repair_mojibake(_clean_text(topic)).strip()


def _invalid_search_query(query: str) -> bool:
    text = _clean_text(query).strip()
    lowered = text.lower()
    if not text:
        return True
    if text.count("?") >= max(3, len(text) // 4):
        return True
    blocked_markers = (
        "topic missing",
        "garbled",
        "please provide",
        "provide specific",
        "请提供",
        "具体研究主题",
        "主题缺失",
    )
    return any(marker in lowered for marker in blocked_markers)


def _search_candidates(topic: str, extra_queries: list[str] | None = None) -> list[str]:
    repaired_topic = _search_query(topic)
    candidates: list[str] = []
    candidates.extend(extra_queries or [])
    candidates.append(repaired_topic)
    out: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate = _repair_mojibake(_clean_text(candidate))
        if _invalid_search_query(candidate):
            continue
        key = candidate.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(candidate.strip())
    return out


ProgressCallback = Callable[[str, str, int, int, int | None, Literal["determinate", "indeterminate"], str | None], None]


async def _expand_search_queries_with_clean_prompt(
    llm: Any,
    topic: str,
    progress: ProgressCallback | None = None,
) -> list[str]:
    topic = _repair_mojibake(_clean_text(topic))
    if _invalid_search_query(topic):
        return []
    if progress:
        progress("检索扩展", "正在生成中英文高质量检索式", 3, 0, None, "indeterminate", "用于覆盖不同语言的高质量期刊文献")
    try:
        text = await llm.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "You generate concise academic literature search queries. "
                        "Return only a JSON array of strings. Do not ask questions."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Research topic: {topic}\n\n"
                        "Generate 4-8 academic search queries for scholarly databases. Requirements:\n"
                        "1. Stay tightly focused on the topic and avoid unrelated broad fields.\n"
                        "2. Prefer high-quality international journal terminology in English; keep key Chinese terms, abbreviations, material names, and method names when useful.\n"
                        "3. Keep each query short and suitable for Semantic Scholar, OpenAlex, Crossref, Europe PMC, HAL, BASE, CORE, and Sciverse.\n"
                        "4. Avoid one-word or overly broad queries such as AI, design, degradation, 3D.\n"
                        "5. Output only a JSON array of strings."
                    ),
                },
            ],
            temperature=0,
            max_tokens=1000,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM search query expansion failed: %s", exc)
        raise ApiError(502, "SEARCH_QUERY_EXPANSION_FAILED", f"LLM 生成检索式失败：{exc}") from exc
    queries = _parse_search_query_lines(text)
    if not queries:
        raise ApiError(502, "SEARCH_QUERY_EXPANSION_EMPTY", "LLM 未返回可用检索式，请检查模型能力或换用更稳定的模型。")
    return queries


def _parse_search_query_lines(text: str) -> list[str]:
    cleaned = text.strip()
    queries: list[str] = []
    try:
        parsed = json.loads(cleaned[cleaned.find("["): cleaned.rfind("]") + 1])
        if isinstance(parsed, list):
            queries = [str(item) for item in parsed]
    except (ValueError, json.JSONDecodeError):
        queries = []
    if not queries:
        queries = [
            re.sub(r"^[-*\d.\s]+", "", line).strip().strip("\"'")
            for line in cleaned.splitlines()
        ]
    out: list[str] = []
    seen: set[str] = set()
    for query in queries:
        query = _clean_text(query).strip().strip("\"'")
        if query.count('"') % 2 == 1:
            query = query.replace('"', "")
        key = query.lower()
        if 4 <= len(query) <= 220 and key not in seen and not _looks_mojibake(query) and not _invalid_search_query(query):
            seen.add(key)
            out.append(query)
    return out[:8]


async def _expand_search_queries_with_llm(
    llm: Any,
    topic: str,
    progress: ProgressCallback | None = None,
) -> list[str]:
    if progress:
        progress("检索扩展", "正在生成中英文高质量检索式", 3, 0, None, "indeterminate", "用于覆盖不同语言的高质量期刊文献")
    try:
        text = await llm.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "You generate concise academic literature search queries. "
                        "Return only a JSON array of strings."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"主题：{topic}\n"
                        "请生成 4-8 条用于多数据库检索的查询式。要求：\n"
                        "1. 不限制语言，中英文都可以，但必须优先覆盖国际高质量英文期刊常用术语；\n"
                        "2. 保留主题中的关键中文术语、英文缩写、材料/方法名；\n"
                        "3. 查询式要短，适合 Semantic Scholar、OpenAlex、CrossRef、Europe PMC、HAL、BASE；\n"
                        "4. 不要输出解释，只输出 JSON 数组。"
                    ),
                },
            ],
            temperature=0,
            max_tokens=1000,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM search query expansion failed: %s", exc)
        return []
    return _parse_search_query_lines(text)


async def _fetch_texts(
    sciverse: SciverseClient,
    papers: list[dict[str, Any]],
    progress: ProgressCallback | None = None,
) -> dict[str, str]:
    texts: dict[str, str] = {}
    candidates = papers[:40]
    for index, paper in enumerate(candidates, 1):
        if progress:
            progress("全文片段", f"正在读取全文片段 {index}/{len(candidates)}", 55 + int(index / max(1, len(candidates)) * 10), index, len(candidates), "determinate", "按已尝试读取的文献数计算")
        doc_id = paper.get("docId")
        if not doc_id:
            continue
        status, body = await sciverse.content(str(doc_id), offset=0, limit=3500)
        if status == 200 and body and body.get("text"):
            texts[paper["id"]] = _clean_text(body.get("text"))[:3500]
    return texts


def _title_key(value: str) -> str:
    return re.sub(r"\W+", "", value.lower())[:120]


async def _enrich_with_agentic_evidence(sciverse: SciverseClient, topic: str, papers: list[dict[str, Any]]) -> None:
    if not papers or not hasattr(sciverse, "agentic_search"):
        return
    status, body = await sciverse.agentic_search(topic, min(100, max(20, len(papers) // 3)), sub_queries=2)
    if status >= 400 or not body:
        return
    by_title = {_title_key(str(p.get("title") or "")): p for p in papers}
    for hit in body.get("hits", []) or []:
        title = _clean_text(hit.get("title"))
        paper = by_title.get(_title_key(title))
        if paper is None:
            continue
        chunk = _clean_text(hit.get("chunk"))
        abstract = _clean_text(hit.get("abstract"))
        if not paper.get("abstract") and (abstract or chunk):
            paper["abstract"] = abstract or chunk[:800]
        if chunk:
            paper["snippet"] = chunk[:1200]
        if hit.get("doc_id") and not paper.get("docId"):
            paper["docId"] = hit.get("doc_id")
        if hit.get("score") is not None:
            paper["score"] = hit.get("score")
        if hit.get("page_no") is not None:
            paper["pageNo"] = hit.get("page_no")


async def _enrich_with_sciverse_doc_ids(sciverse: SciverseClient, papers: list[dict[str, Any]]) -> None:
    doi_papers = [paper for paper in papers if paper.get("doi") and not paper.get("docId")]
    if not doi_papers:
        return
    semaphore = asyncio.Semaphore(8)
    fields = [
        "title", "doi", "doc_id", "unique_id", "abstract", "publication_published_year",
        "publication_venue_name_unified", "access_oa_url",
    ]

    async def lookup(paper: dict[str, Any]) -> None:
        doi = _canonical_doi(paper.get("doi"))
        if not doi:
            return
        async with semaphore:
            status, body = await sciverse.meta_search(
                "",
                3,
                filters=[{"field": "doi", "operator": "FILTER_OP_EQ", "value": doi}],
                fields=fields,
            )
        if status >= 400 or not body:
            return
        for hit in body.get("results", []) or []:
            if _canonical_doi(hit.get("doi")) != doi:
                continue
            if hit.get("doc_id") and not paper.get("docId"):
                paper["docId"] = hit.get("doc_id")
            if hit.get("unique_id") and not paper.get("uniqueId"):
                paper["uniqueId"] = hit.get("unique_id")
            abstract = _clean_text(hit.get("abstract"))
            if abstract and len(abstract) > len(_clean_text(paper.get("abstract"))):
                paper["abstract"] = abstract
            if hit.get("access_oa_url") and not paper.get("url"):
                paper["url"] = _canonical_url(hit.get("access_oa_url"), doi)
            sources = set(paper.get("sources") or [paper.get("source") or "unknown"])
            sources.add("sciverse")
            paper["sources"] = sorted(str(source) for source in sources if source)
            paper["source"] = "+".join(paper["sources"])
            return

    await asyncio.gather(*(lookup(paper) for paper in doi_papers[:120]))


def _evidence_pack(papers: list[dict[str, Any]], texts: dict[str, str], *, compact: bool = False, start: int = 1) -> str:
    rows: list[str] = []
    abstract_limit = 260 if compact else 900
    fulltext_limit = 500 if compact else 1400
    for i, paper in enumerate(papers, start):
        text = texts.get(paper["id"], "")
        snippet = str(paper.get("snippet") or "")
        source = "全文片段+摘要" if text else "摘要/元数据"
        rows.append(
            "\n".join(
                [
                    f"[{i}] {paper.get('title')}",
                    f"Evidence scores: relevance {paper.get('relevanceScore', 'n/a')}/100, quality {paper.get('qualityScore', 'n/a')}/100, evidence {paper.get('evidenceScore', 'n/a')}/100, novelty {paper.get('noveltyScore', 'n/a')}/100",
                    f"Daily delta: seenBefore={paper.get('seenBefore', 'n/a')}, recentlyUsed={paper.get('recentlyUsed', 'n/a')}, usedCount={paper.get('usedCount', 'n/a')}",
                    f"Evidence tags: {', '.join(paper.get('scoreTags') or []) or 'n/a'}",
                    f"证据来源: {source}",
                    f"作者: {', '.join(paper.get('authors') or [])[:180] or 'n/a'}",
                    f"年份/期刊: {paper.get('year') or 'n.d.'} / {paper.get('venue') or 'n/a'}",
                    f"DOI: {paper.get('doi') or 'n/a'}",
                    f"被引/FWCI: {paper.get('citationCount') or 0} / {paper.get('fwci') or 'n/a'}",
                    f"摘要: {str(paper.get('abstract') or '')[:abstract_limit]}",
                    f"检索片段: {snippet[:fulltext_limit]}",
                    f"全文片段: {text[:fulltext_limit]}",
                ]
            )
        )
    return "\n\n---\n\n".join(rows)


def _review_requirements(
    topic: str,
    papers: list[dict[str, Any]],
    texts: dict[str, str],
    include_web: bool,
    daily_delta: dict[str, Any] | None = None,
) -> str:
    web_note = (
        "若模型或上游网关具备联网能力，可补充近 12 个月产业动态、预印本趋势和公开资料；"
        "若不能联网，必须明确说明网络补充未启用，不得编造网页来源。"
        if include_web
        else "不要使用外部网页补充，只基于给定文献证据写作。"
    )
    delta = daily_delta or {}
    mode = str(delta.get("mode") or "")
    delta_note = (
        f"本期模式: {_mode_label(mode)}；动态小标题: {delta.get('subtitle') or '未生成'}；"
        f"新增证据 {delta.get('newEvidenceCount', 0)} 篇，复用/延续证据 {delta.get('reusedEvidenceCount', 0)} 篇，"
        f"高新颖度证据 {delta.get('highNoveltyCount', 0)} 篇，平均新颖度 {delta.get('averageNoveltyScore', 0)}。"
        "写作时必须优先使用 relevanceScore、qualityScore、evidenceScore、noveltyScore 均较高的证据；"
        "如新增不足，应明确写成滚动观察或专题深挖，不要假装每天都有大量全新突破。"
        if delta
        else "本期未启用 Daily Delta 元数据。"
    )
    structure_note = (
        "若本期模式为“监测短报”，可将正文压缩为监测摘要、无显著更新说明、后续跟踪清单和参考文献；"
        "其余模式请输出完整综述结构。"
    )
    return f"""
主题: {topic}
检索日期: {datetime.now(timezone.utc).date().isoformat()}
文献数量: {len(papers)}
全文证据覆盖: {len(texts)} / {len(papers)} 篇。没有全文片段的文献只能按题名、摘要和元数据判断；引用使用到全文片段时请标注“[全文证据]”。
联网补充策略: {web_note}
Daily Delta: {delta_note}

请输出一篇完整中文 Markdown 综述，必须明确、逻辑清晰，反映前沿技术和前沿领域研究进展。
写作要求:
- 所有判断都使用 [序号] 引用，序号必须来自给定文献。
- 不要堆砌摘要，要比较技术路线、指出共识、争议、瓶颈和未来方向。
- 必须在摘要或开头明确“本期相对往期的新增/延续/重复情况”，并说明结论强度。
- 优先引用新颖度高且相关性、质量、证据完整度均高的文献；低分文献只能作为背景，不得承担关键结论。
- 必须包含: # 标题、## 摘要、## 证据覆盖说明、## 1. 研究背景与问题定义、## 2. 前沿技术路线图、## 3. 关键证据表、## 4. 领域趋势、## 5. 争议与未解决问题、## 6. 未来 12-24 个月值得跟踪的方向、## 7. 结论、## 参考文献。
- “关键证据表”至少 8 条，包含文献、年份、方法、主要发现、局限。
{structure_note}
"""


def _review_prompt(topic: str, papers: list[dict[str, Any]], texts: dict[str, str], include_web: bool, daily_delta: dict[str, Any] | None = None) -> list[dict[str, str]]:
    system = "你是严谨的学术综述作者。必须基于证据写作，输出中文 Markdown。"
    user = f"{_review_requirements(topic, papers, texts, include_web, daily_delta)}\n\n文献证据:\n{_evidence_pack(papers, texts, compact=len(papers) > 120)}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _chunk_prompt(topic: str, chunk: list[dict[str, Any]], texts: dict[str, str], offset: int) -> list[dict[str, str]]:
    evidence = _evidence_pack(chunk, texts, compact=True, start=offset + 1)
    user = f"""
主题: {topic}
请对以下文献批次做中文证据归纳。保留全局引用序号，例如 [123]。
输出: 主要技术路线、代表性进展、证据强弱、局限、可用于总综述的引用组合。

文献证据:
{evidence}
"""
    return [{"role": "system", "content": "你是学术证据归纳助手，只基于给定证据写作。"}, {"role": "user", "content": user}]


def _final_from_summaries_prompt(
    topic: str,
    papers: list[dict[str, Any]],
    texts: dict[str, str],
    include_web: bool,
    summaries: list[str],
    daily_delta: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    top_evidence = _evidence_pack(papers[:120], texts, compact=True)
    user = f"""
{_review_requirements(topic, papers, texts, include_web, daily_delta)}

下面是分批证据归纳，请综合为一篇完整综述。引用序号必须保持原始全局编号。

分批归纳:
{chr(10).join(f"### 批次 {i + 1}{chr(10)}{summary}" for i, summary in enumerate(summaries))}

高权重文献证据节选:
{top_evidence}
"""
    return [{"role": "system", "content": "你是严谨的学术综述作者。必须综合全部批次证据，输出中文 Markdown。"}, {"role": "user", "content": user}]


def _references_section(papers: list[dict[str, Any]]) -> str:
    lines = ["## 参考文献"]
    for i, paper in enumerate(papers, 1):
        authors = ", ".join(paper.get("authors") or []) or "作者未知"
        title = paper.get("title") or "Untitled"
        year = paper.get("year") or "n.d."
        venue = paper.get("venue") or "来源未知"
        doi = paper.get("doi") or paper.get("url") or "无 DOI/URL"
        lines.append(f"[{i}] {authors}. {title}. {venue}, {year}. {doi}.")
    return "\n".join(lines)


def _key_evidence_section(papers: list[dict[str, Any]], texts: dict[str, str]) -> str:
    lines = [
        "## 3. 关键证据表",
        "| 文献 | 年份 | 证据来源 | 主要信息 | 局限 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for i, paper in enumerate(papers[:12], 1):
        source = "全文证据+摘要" if texts.get(paper["id"]) else "摘要/元数据"
        abstract = str(paper.get("abstract") or "暂无摘要")
        finding = abstract[:120].replace("|", " ")
        lines.append(
            f"| [{i}] {paper.get('title') or 'Untitled'} | {paper.get('year') or 'n.d.'} | {source} | {finding} | 需结合全文、数据与方法细节进一步验证 |"
        )
    return "\n".join(lines)


def _evidence_coverage_section(papers: list[dict[str, Any]], texts: dict[str, str]) -> str:
    abstract_count = len(papers) - len(texts)
    fulltext_note = (
        f"其中 {len(texts)} 篇包含全文片段，正文中涉及这些文献的论证可视为“全文证据”支撑；"
        if texts
        else "本期未获取到可用全文片段，综述主要依据题名、摘要与元数据，结论强度需按摘要级证据理解；"
    )
    return (
        "## 证据覆盖说明\n\n"
        f"本期共纳入 {len(papers)} 篇文献。{fulltext_note}"
        f"{abstract_count} 篇主要依据摘要/元数据。公开页面的“文献证据”页会逐篇标注证据来源，并支持点击引用跳转到对应文献。"
    )


def _ensure_review_sections(review: str, papers: list[dict[str, Any]], texts: dict[str, str]) -> str:
    out = review.strip()
    if "证据覆盖" not in out:
        parts = out.split("\n## ", 1)
        coverage = _evidence_coverage_section(papers, texts)
        out = f"{parts[0].rstrip()}\n\n{coverage}\n\n## {parts[1]}" if len(parts) == 2 else f"{coverage}\n\n{out}"
    if "关键证据" not in out:
        out = f"{out.rstrip()}\n\n{_key_evidence_section(papers, texts)}"
    if "参考文献" not in out:
        out = f"{out.rstrip()}\n\n{_references_section(papers)}"
    return out


def _daily_delta_section(delta: dict[str, Any] | None) -> str:
    if not delta:
        return ""
    return (
        "## 本期 Daily Delta\n\n"
        f"- 本期模式：{delta.get('modeLabel') or _mode_label(str(delta.get('mode') or ''))}。\n"
        f"- 动态小标题：{delta.get('subtitle') or '未生成'}。\n"
        f"- 新增证据：{delta.get('newEvidenceCount', 0)} 篇；复用/延续证据：{delta.get('reusedEvidenceCount', 0)} 篇；"
        f"高新颖度证据：{delta.get('highNoveltyCount', 0)} 篇。\n"
        f"- 平均新颖度：{delta.get('averageNoveltyScore', 0)}；重复比例：{delta.get('repeatRatio', 0)}。\n"
        "- 写作策略：优先使用相关性、质量、证据完整度和新颖度较高的文献；新增不足时转为差异简报、专题深挖或监测短报。"
    )


def _ensure_daily_delta_section(review: str, delta: dict[str, Any] | None) -> str:
    section = _daily_delta_section(delta)
    if not section or "Daily Delta" in review:
        return review
    parts = review.strip().split("\n## ", 1)
    return f"{parts[0].rstrip()}\n\n{section}\n\n## {parts[1]}" if len(parts) == 2 else f"{section}\n\n{review.strip()}"


def _one_figure_prompt(topic: str, review_markdown: str, papers: list[dict[str, Any]]) -> str:
    titles = "; ".join(str(p.get("title") or "") for p in papers[:12])
    return (
        f"为主题“{topic}”生成一张适合综述文章的中文信息图。"
        "画面呈现技术路线分层、关键进展、瓶颈、未来方向，风格专业清晰，适合学术报告。"
        f"代表文献: {titles}. 综述要点: {_review_brief_for_image(review_markdown)}"
    )


def _review_brief_for_image(review_markdown: str, limit: int = 2400) -> str:
    lines: list[str] = []
    for raw in review_markdown.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith(("-", "*")) or re.match(r"^\d+[.、]", line):
            lines.append(re.sub(r"\s+", " ", line))
        elif len(lines) < 8 and len(line) >= 24:
            lines.append(re.sub(r"\s+", " ", line))
        if len("\n".join(lines)) >= limit:
            break
    brief = "\n".join(lines).strip() or review_markdown.strip()
    return brief[:limit]


def _image_prompt_messages(topic: str, review_markdown: str, papers: list[dict[str, Any]]) -> list[dict[str, str]]:
    evidence = []
    for index, paper in enumerate(papers[:12], 1):
        score = paper.get("evidenceScore") or paper.get("qualityScore") or paper.get("relevanceScore")
        score_text = f"，证据分 {score}" if score is not None else ""
        evidence.append(f"[{index}] {paper.get('title') or 'Untitled'}（{paper.get('year') or 'n.d.'}{score_text}）")
    user = f"""
主题：{topic}

压缩综述线索：
{_review_brief_for_image(review_markdown, 1600)}

核心文献线索：
{chr(10).join(evidence)}

请生成一个用于图像生成模型的中文提示词，不要输出解释。要求：
1. 只输出图像生成提示词，不要输出解释，不要包含 Markdown 标题符号。
2. 提示词要中等详细、逻辑通顺，尽可能覆盖综述的主要信息，但不要复制长段正文。
3. 适合“一图看懂”学术综述信息图，中文标签清晰、结构化、专业克制。
4. 画面必须包含：研究背景、技术主线、关键进展、证据基础、应用场景、瓶颈挑战、未来方向。
5. 明确视觉布局，例如中心主题、分层模块、箭头关系、证据侧栏、趋势时间线或闭环流程。
6. 可保留 5-8 个代表性关键词或短语，不要堆砌文献标题，不要出现 DOI。
7. 控制在 1200-1800 字，信息密度高但可被图像模型理解。
"""
    return [
        {"role": "system", "content": "你是学术信息图提示词设计师，擅长把综述压缩成清晰可画的图像生成提示词。"},
        {"role": "user", "content": user},
    ]


async def _build_image_prompt(topic: str, review_markdown: str, papers: list[dict[str, Any]], llm: Any, progress: ProgressCallback | None = None) -> str:
    fallback = _one_figure_prompt(topic, review_markdown, papers)
    if progress:
        progress("生图提示词", "LLM 正在将综述压缩为一图看懂提示词", 94, len(papers), len(papers), "indeterminate", "只传递压缩线索，不直接把完整综述交给生图模型")
    try:
        prompt = await llm.complete(_image_prompt_messages(topic, review_markdown, papers), temperature=0.2, max_tokens=2200)
    except Exception as exc:  # noqa: BLE001
        log.warning("image prompt generation failed, using fallback prompt: %s", exc)
        return fallback
    prompt = re.sub(r"^```(?:text|markdown)?|```$", "", prompt.strip(), flags=re.IGNORECASE | re.MULTILINE).strip()
    if not prompt or "未配置 LLM" in prompt:
        return fallback
    if len(prompt) < 1000:
        prompt = f"{prompt}\n\n补充画面要求：{fallback}"
    return prompt[:3200]


async def _collect_paper_search_papers(
    topic: str,
    count: int,
    since_year: int,
    sources: list[str] | None,
    progress: ProgressCallback | None = None,
    extra_queries: list[str] | None = None,
    validate_links: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    selected_sources = _normalize_paper_search_sources(sources)
    raw: list[dict[str, Any]] = []
    deduped: list[dict[str, Any]] = []
    used_queries: list[str] = []
    source_errors: dict[str, str] = {}
    dynamic_terms = _dynamic_topic_terms(topic, extra_queries)
    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        searcher = PaperSearchClient(client)
        for search_expression in _search_candidates(topic, extra_queries):
            used_queries.append(search_expression)
            per_source_limit = min(100, max(30, count * 2))
            for source in selected_sources:
                try:
                    batch = await searcher.search_source(source, search_expression, limit=per_source_limit, since_year=since_year)
                except Exception as exc:
                    source_errors[source] = str(exc) or exc.__class__.__name__
                    log.warning("Paper Search source failed: source=%s query=%s error=%s", source, search_expression, source_errors[source])
                    batch = []
                raw.extend(batch)
                normalized = _dedupe([_normalize_paper(p) for p in raw if p.get("title")])
                deduped = _rank_quality_papers(normalized, allow_weak=len(normalized) < count, topic=topic, dynamic_terms=dynamic_terms)
                if progress:
                    progress(
                        "文献检索",
                        f"{source} 已返回 {len(batch)} 条，累计筛选 {len(deduped)}/{count} 篇高质量证据",
                        min(45, 8 + int(len(deduped) / max(1, count) * 37)),
                        len(deduped),
                        count,
                        "determinate",
                        "Paper Search 多源检索按高质量证据数量计数",
                    )
                if len(deduped) >= count:
                    break
            if len(deduped) >= count:
                break
    verified = await _validate_paper_links(deduped, progress=progress) if validate_links else deduped
    ranked = _rank_quality_papers(_dedupe(verified), allow_weak=True, topic=topic, dynamic_terms=dynamic_terms)
    meta = {
        "total_count": len(ranked),
        "raw_count": len(raw),
        "sources": selected_sources,
        "verified_count": len(verified),
        "source_errors": source_errors,
    }
    return ranked[:count], meta, f"paper_search[{','.join(selected_sources)}]: " + " | fallback: ".join(used_queries)


async def _collect_papers(
    sciverse: SciverseClient,
    topic: str,
    count: int,
    since_year: int,
    freshness: Freshness,
    fields: list[str],
    progress: ProgressCallback | None = None,
    provider: LiteratureProvider = "sciverse",
    paper_search_sources: list[str] | None = None,
    extra_queries: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict | None, str]:
    if provider == "paper_search":
        return await _collect_paper_search_papers(topic, count, since_year, paper_search_sources, progress, extra_queries)
    if provider == "hybrid":
        paper_papers, paper_meta, paper_expression = await _collect_paper_search_papers(
            topic,
            count,
            since_year,
            paper_search_sources,
            progress,
            extra_queries,
        )
        if not sciverse.enabled:
            return paper_papers[:count], paper_meta, paper_expression
        try:
            sciverse_papers, sciverse_meta, sciverse_expression = await _collect_papers(
                sciverse,
                topic,
                count,
                since_year,
                freshness,
                fields,
                progress,
                provider="sciverse",
                extra_queries=extra_queries,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Sciverse hybrid supplement failed: %s", str(exc) or exc.__class__.__name__)
            return paper_papers[:count], paper_meta, paper_expression
        merged = _rank_quality_papers(
            _dedupe([*paper_papers, *sciverse_papers]),
            allow_weak=True,
            topic=topic,
            dynamic_terms=_dynamic_topic_terms(topic, extra_queries),
        )[:count]
        meta = {"total_count": len(merged), "paper_search": paper_meta, "sciverse": sciverse_meta}
        return merged, meta, f"{paper_expression} | sciverse: {sciverse_expression}"

    raw: list[dict[str, Any]] = []
    meta: dict | None = None
    deduped: list[dict[str, Any]] = []
    used_queries: list[str] = []
    last_error: tuple[int, dict | None] | None = None
    for search_expression in _search_candidates(topic, extra_queries):
        used_queries.append(search_expression)
        page = 1
        stagnant_pages = 0
        while len(deduped) < count and page <= 12:
            remaining = count - len(deduped)
            page_size = min(200, max(100, remaining * 3))
            status, meta = await sciverse.meta_search(
                search_expression,
                page_size,
                page=page,
                filters=[{"field": "publication_published_year", "operator": "FILTER_OP_GTE", "value": since_year}],
                fields=fields,
                freshness_boost=freshness,
            )
            if status >= 400:
                last_error = (status, meta)
                break
            batch = (meta or {}).get("results", [])
            if not batch:
                break
            before = len(deduped)
            raw.extend(batch)
            normalized = _dedupe([_normalize_paper(p) for p in raw if p.get("title")])
            deduped = _rank_quality_papers(
                normalized,
                allow_weak=len(normalized) < count,
                topic=topic,
                dynamic_terms=_dynamic_topic_terms(topic, extra_queries),
            )
            if progress:
                progress(
                    "文献检索",
                    f"已检索 {len(raw)} 条候选，筛选出 {len(deduped)}/{count} 篇高质量证据",
                    min(45, 8 + int(len(deduped) / max(1, count) * 37)),
                    len(deduped),
                    count,
                    "determinate",
                    "按高质量证据筛选数量计算",
                )
            stagnant_pages = stagnant_pages + 1 if len(deduped) == before else 0
            if stagnant_pages >= 2:
                break
            page += 1
    if not deduped and last_error:
        status, body = last_error
        raise ApiError(status if status < 500 else 502, "SCIVERSE_SEARCH_FAILED", str(body or {}))
    return deduped[:count], meta, " | fallback: ".join(used_queries)


def _append_run(result: dict[str, Any]) -> None:
    run_id = str(result.get("runId") or "")
    topic_part = _safe_draft_part(str(result.get("topicId") or result.get("topicSlug") or "unknown-topic"))
    if not run_id:
        return
    topic_dir = _runs_dir() / topic_part
    topic_dir.mkdir(parents=True, exist_ok=True)
    run_path = topic_dir / f"{_safe_draft_part(run_id)}.json"
    run_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    index_item = _run_index_item(result, run_path)
    _runs_index_path().open("a", encoding="utf-8").write(json.dumps(index_item, ensure_ascii=False) + "\n")
    _runs_path().open("a", encoding="utf-8").write(json.dumps(result, ensure_ascii=False) + "\n")


def _run_index_item(run: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    query = run.get("query") if isinstance(run.get("query"), dict) else {}
    image = run.get("image") if isinstance(run.get("image"), dict) else {}
    delta = run.get("dailyDelta") if isinstance(run.get("dailyDelta"), dict) else {}
    item = {
        "runId": str(run.get("runId") or ""),
        "topicId": run.get("topicId"),
        "topicSlug": run.get("topicSlug"),
        "topicName": run.get("topicName"),
        "topic": run.get("topic"),
        "subtitle": run.get("subtitle") or delta.get("subtitle"),
        "dailyMode": delta.get("mode"),
        "newEvidenceCount": delta.get("newEvidenceCount"),
        "reusedEvidenceCount": delta.get("reusedEvidenceCount"),
        "createdAt": run.get("createdAt"),
        "paperCount": len(run.get("papers") or []),
        "sinceYear": query.get("sinceYear"),
        "fullTextFetched": int(run.get("fullTextFetched") or 0),
        "imageStatus": image.get("status"),
    }
    if path:
        try:
            item["path"] = str(path.relative_to(Path(settings.frontier_review_data_dir))).replace("\\", "/")
        except ValueError:
            item["path"] = str(path)
    return item


def _load_run_from_index_item(item: dict[str, Any]) -> dict[str, Any] | None:
    rel = str(item.get("path") or "")
    if not rel:
        return None
    path = Path(settings.frontier_review_data_dir) / rel
    if not path.exists() or not path.is_file():
        return None
    try:
        run = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return run if isinstance(run, dict) and run.get("runId") else None


def _iter_run_index() -> list[dict[str, Any]]:
    index_path = _runs_index_path()
    items: list[dict[str, Any]] = []
    if index_path.exists():
        try:
            lines = index_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("runId"):
                items.append(item)
    seen = {str(item.get("runId")) for item in items}
    for run in _iter_legacy_runs():
        run_id = str(run.get("runId") or "")
        if run_id and run_id not in seen:
            items.append(_run_index_item(run))
            seen.add(run_id)
    return items


def _matches_topic_filter(run: dict[str, Any], topic: str | None) -> bool:
    if not topic:
        return True
    aliases = {topic.strip()}
    try:
        config = _load_config()
        for item in config.topics:
            values = {item.id, item.slug, item.name}
            if aliases & values:
                aliases.update(values)
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to resolve topic aliases: %s", str(exc) or exc.__class__.__name__)
    run_values = {str(run.get("topicId") or ""), str(run.get("topicSlug") or ""), str(run.get("topicName") or "")}
    return bool(aliases & run_values)


def _latest_run(topic: str | None = None) -> dict[str, Any] | None:
    for item in reversed(_iter_run_index()):
        if isinstance(item, dict) and _matches_topic_filter(item, topic):
            run = _load_run_from_index_item(item) or _find_legacy_run(str(item.get("runId") or ""))
            if run is None:
                continue
            return _ensure_run_evidence_scores(run)
    return None


def _latest_run_from_runs(runs: list[dict[str, Any]], topic: str | None = None) -> dict[str, Any] | None:
    for run in reversed(runs):
        if isinstance(run, dict) and _matches_topic_filter(run, topic):
            return _ensure_run_evidence_scores(run)
    return None


def _iter_runs() -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for item in _iter_run_index():
        run = _load_run_from_index_item(item) or _find_legacy_run(str(item.get("runId") or ""))
        if run is not None:
            runs.append(run)
    return runs


def _iter_legacy_runs() -> list[dict[str, Any]]:
    path = _runs_path()
    if not path.exists():
        return []
    runs: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("runId"):
            runs.append(item)
    return runs


def _run_summary(run: dict[str, Any]) -> DailyReviewRunSummary:
    query = run.get("query") or {}
    image = run.get("image") or {}
    delta = run.get("dailyDelta") if isinstance(run.get("dailyDelta"), dict) else {}
    return DailyReviewRunSummary(
        runId=str(run.get("runId") or ""),
        topicId=run.get("topicId"),
        topicSlug=run.get("topicSlug"),
        topicName=run.get("topicName"),
        topic=str(run.get("topic") or ""),
        subtitle=run.get("subtitle") or delta.get("subtitle"),
        dailyMode=delta.get("mode"),
        newEvidenceCount=delta.get("newEvidenceCount"),
        reusedEvidenceCount=delta.get("reusedEvidenceCount"),
        createdAt=str(run.get("createdAt") or ""),
        paperCount=len(run.get("papers") or []),
        sinceYear=query.get("sinceYear"),
        fullTextFetched=int(run.get("fullTextFetched") or 0),
        imageStatus=image.get("status"),
    )


def _set_run_progress(
    topic: ReviewTopicConfig,
    *,
    status: Literal["running", "success", "error"],
    stage: str,
    message: str,
    percent: int,
    current: int = 0,
    total: int | None = None,
    mode: Literal["determinate", "indeterminate"] = "determinate",
    detail: str | None = None,
    run_id: str | None = None,
    error: str | None = None,
    draft_id: str | None = None,
    draft_stage: str | None = None,
    draft_can_resume: bool = False,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    old = _RUN_PROGRESS.get(topic.id) or {}
    started_at = old.get("startedAt") if status == "running" and old.get("status") == "running" else now
    _RUN_PROGRESS[topic.id] = {
        "topicId": topic.id,
        "topicSlug": topic.slug,
        "topicName": topic.name,
        "status": status,
        "stage": stage,
        "message": message,
        "mode": mode,
        "detail": detail,
        "percent": max(0, min(100, int(percent))),
        "current": max(0, int(current or 0)),
        "total": total,
        "startedAt": started_at,
        "updatedAt": now,
        "completedAt": now if status in {"success", "error"} else None,
        "runId": run_id,
        "error": error,
        "draftId": draft_id if draft_id is not None else (old.get("draftId") if status == "running" else None),
        "draftStage": draft_stage if draft_stage is not None else (old.get("draftStage") if status == "running" else None),
        "draftCanResume": bool(draft_can_resume),
    }


def _progress_items(config: DailyReviewConfig) -> list[DailyReviewProgressItem]:
    items: list[DailyReviewProgressItem] = []
    runs = _iter_runs()
    config = _ensure_topics(config)
    drafts_by_topic = {
        item.topicId: item
        for item in _list_drafts(config)
        if item.canResume
    }
    for topic in config.topics:
        latest = _latest_run_from_runs(runs, topic.slug)
        latest_count = len(latest.get("papers") or []) if latest else None
        base = {
            "topicId": topic.id,
            "topicSlug": topic.slug,
            "topicName": topic.name,
            "status": "idle",
            "stage": "等待",
            "message": "当前没有生成任务",
            "mode": "determinate",
            "detail": None,
            "percent": 0,
            "current": 0,
            "total": topic.paperCount,
            "latestRunId": latest.get("runId") if latest else None,
            "latestRunAt": latest.get("createdAt") if latest else None,
            "latestPaperCount": latest_count,
            "draftId": None,
            "draftStage": None,
            "draftCanResume": False,
        }
        draft = drafts_by_topic.get(topic.id)
        if draft:
            base["draftId"] = draft.draftId
            base["draftStage"] = draft.stage
            base["draftCanResume"] = True
        item = {**base, **(_RUN_PROGRESS.get(topic.id) or {})}
        item["latestRunId"] = base["latestRunId"]
        item["latestRunAt"] = base["latestRunAt"]
        item["latestPaperCount"] = base["latestPaperCount"]
        if not item.get("draftId"):
            item["draftId"] = base["draftId"]
            item["draftStage"] = base["draftStage"]
            item["draftCanResume"] = base["draftCanResume"]
        items.append(DailyReviewProgressItem(**item))
    return items


def _progress_item_for_topic(config: DailyReviewConfig, topic_id: str) -> DailyReviewProgressItem:
    for item in _progress_items(config):
        if item.topicId == topic_id:
            return item
    raise ApiError(404, "TOPIC_NOT_FOUND", "主题不存在")


def _new_literature_search_id(topic: str) -> str:
    seed = f"literature-search:{topic}:{datetime.now(timezone.utc).isoformat()}:{os.urandom(8).hex()}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _save_literature_search_payload(
    search_id: str,
    *,
    result: dict[str, Any] | None = None,
    progress: dict[str, Any] | None = None,
) -> None:
    path = _literature_search_path(search_id)
    payload: dict[str, Any] = {}
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(old, dict):
                payload = old
        except (OSError, json.JSONDecodeError):
            payload = {}
    if progress is not None:
        payload["progress"] = progress
    if result is not None:
        payload["result"] = result
    payload["updatedAt"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if result is not None:
        _upsert_literature_search_index(search_id, payload)


def _load_literature_search_payload(search_id: str) -> dict[str, Any] | None:
    path = _literature_search_path(search_id)
    if not path.exists() or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _literature_search_summary_from_payload(search_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    progress = payload.get("progress") if isinstance(payload.get("progress"), dict) else {}
    cdk = result.get("cdk") if isinstance(result.get("cdk"), dict) else {}
    return {
        "searchId": search_id,
        "topic": str(result.get("topic") or progress.get("message") or "未命名检索"),
        "sharePath": str(result.get("sharePath") or progress.get("sharePath") or f"/literature-search/{search_id}"),
        "cdkId": result.get("cdkId") or cdk.get("id"),
        "cdkName": result.get("cdkName") or cdk.get("name"),
        "requested": int(result.get("requested") or progress.get("total") or 0),
        "returned": int(result.get("returned") or progress.get("current") or 0),
        "sinceYear": result.get("sinceYear"),
        "literatureProvider": result.get("literatureProvider"),
        "status": str(progress.get("status") or ("success" if result else "unknown")),
        "createdAt": result.get("createdAt") or progress.get("startedAt"),
        "updatedAt": payload.get("updatedAt") or progress.get("updatedAt"),
    }


def _upsert_literature_search_index(search_id: str, payload: dict[str, Any]) -> None:
    summary = _literature_search_summary_from_payload(search_id, payload)
    path = _literature_search_index_path()
    rows: list[dict[str, Any]] = []
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                if isinstance(item, dict) and item.get("searchId") != search_id:
                    rows.append(item)
        except (OSError, json.JSONDecodeError):
            rows = []
    rows.append(summary)
    rows = sorted(rows, key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)[:5000]
    _write_literature_search_index(rows)


def _write_literature_search_index(rows: list[dict[str, Any]]) -> None:
    path = _literature_search_index_path()
    path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in rows) + ("\n" if rows else ""), encoding="utf-8")


def _iter_literature_search_summaries() -> list[dict[str, Any]]:
    path = _literature_search_index_path()
    rows: list[dict[str, Any]] = []
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                if isinstance(item, dict) and item.get("searchId"):
                    rows.append(item)
        except (OSError, json.JSONDecodeError):
            rows = []
    if rows:
        return rows
    for item_path in _literature_search_dir().glob("*.json"):
        if item_path.name == "index.jsonl":
            continue
        search_id = item_path.stem
        payload = _load_literature_search_payload(search_id)
        if payload:
            rows.append(_literature_search_summary_from_payload(search_id, payload))
    rows = sorted(rows, key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
    if rows:
        _write_literature_search_index(rows[:5000])
    return rows


def _set_literature_search_progress(
    search_id: str,
    *,
    status: Literal["queued", "running", "success", "error"],
    stage: str,
    message: str,
    percent: int,
    current: int = 0,
    total: int | None = None,
    mode: Literal["determinate", "indeterminate"] = "determinate",
    detail: str | None = None,
    error: str | None = None,
) -> LiteratureSearchProgressItem:
    now = datetime.now(timezone.utc).isoformat()
    old = _LITERATURE_SEARCH_PROGRESS.get(search_id) or {}
    started_at = old.get("startedAt") or now
    progress = {
        "searchId": search_id,
        "status": status,
        "stage": stage,
        "message": message,
        "mode": mode,
        "detail": detail,
        "percent": max(0, min(100, int(percent))),
        "current": max(0, int(current or 0)),
        "total": total,
        "startedAt": started_at,
        "updatedAt": now,
        "completedAt": now if status in {"success", "error"} else None,
        "error": error,
        "sharePath": f"/literature-search/{search_id}",
    }
    _LITERATURE_SEARCH_PROGRESS[search_id] = progress
    _save_literature_search_payload(search_id, progress=progress)
    return LiteratureSearchProgressItem(**progress)


def _find_run(run_id: str) -> dict[str, Any] | None:
    for item in reversed(_iter_run_index()):
        if str(item.get("runId") or "") != run_id:
            continue
        run = _load_run_from_index_item(item) or _find_legacy_run(run_id)
        return _ensure_run_evidence_scores(run) if run else None
    return _find_legacy_run(run_id)


def _find_legacy_run(run_id: str) -> dict[str, Any] | None:
    for run in reversed(_iter_legacy_runs()):
        if str(run.get("runId")) == run_id:
            return _ensure_run_evidence_scores(run)
    return None


async def _generate_image(config: DailyReviewConfig, prompt: str) -> dict[str, Any]:
    if not config.image.enabled or not config.image.apiKey.strip():
        url = _save_image_asset(_fallback_png(prompt), ".png")
        return {"status": "prompt-only", "prompt": prompt, "url": url}
    base_url = _normalize_openai_base_url(config.image.baseUrl or config.llm.baseUrl or settings.openai_base_url)
    payload = {"model": config.image.model or "gpt-image-1", "prompt": prompt, "size": config.image.size or "1024x1024"}
    headers = {"Authorization": f"Bearer {config.image.apiKey}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
            r = await client.post(f"{base_url}/images/generations", json=payload, headers=headers)
        if r.status_code >= 400:
            raise RuntimeError(f"image api returned {r.status_code}: {r.text[:300]}")
        first = (r.json().get("data") or [{}])[0]
        if first.get("url"):
            remote_url = str(first["url"])
            url = await _cache_remote_image_url(remote_url, config.image.apiKey)
            return {"status": "generated", "prompt": prompt, "url": url, "remoteUrl": remote_url}
        if first.get("b64_json"):
            image_bytes = base64.b64decode(first["b64_json"])
            if len(image_bytes) > settings.max_cached_image_bytes:
                raise RuntimeError("image is too large")
            url = _save_image_asset(image_bytes, ".png")
            return {"status": "generated", "prompt": prompt, "url": url}
    except Exception as exc:  # noqa: BLE001
        log.warning("image generation failed, fallback to png: %s", exc)
    url = _save_image_asset(_fallback_png(prompt), ".png")
    return {"status": "fallback", "prompt": prompt, "url": url}


def _image_error_message(status_code: int, detail: str) -> tuple[str, str]:
    lowered = detail.lower()
    if "cloudflare" in lowered or "challenge" in lowered:
        return "生图上游被 Cloudflare 拦截", "上游代理返回 Cloudflare challenge 页面，请更换生图 Base URL/代理或刷新上游服务会话。"
    return f"生图请求失败 {status_code}", detail[:500]


async def _test_image_generation(config: DailyReviewConfig) -> ConnectionTestResult:
    if not config.image.apiKey.strip():
        return ConnectionTestResult(ok=False, service="image", message="生图 API Key 未配置")
    base_url = _normalize_openai_base_url(config.image.baseUrl or settings.openai_base_url)
    payload = {
        "model": config.image.model or "gpt-image-1",
        "prompt": "A clean academic infographic icon showing literature review workflow, no text.",
        "size": config.image.size or "1024x1024",
    }
    headers = {"Authorization": f"Bearer {config.image.apiKey}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
            r = await client.post(f"{base_url}/images/generations", json=payload, headers=headers)
        if r.status_code >= 400:
            message, detail = _image_error_message(r.status_code, r.text[:1000])
            return ConnectionTestResult(ok=False, service="image", message=message, detail=detail)
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        return ConnectionTestResult(ok=False, service="image", message="生图请求失败", detail=str(exc))
    first = (data.get("data") or [{}])[0]
    ok = bool(first.get("url") or first.get("b64_json"))
    if first.get("url"):
        try:
            await _cache_remote_image_url(str(first["url"]), config.image.apiKey)
        except Exception as exc:  # noqa: BLE001
            return ConnectionTestResult(
                ok=False,
                service="image",
                message="生图接口返回图片地址，但服务器无法下载图片",
                detail=str(exc),
            )
    return ConnectionTestResult(
        ok=ok,
        service="image",
        message="生图连接成功" if ok else "生图接口返回成功但没有图片数据",
        detail=f"模型 {payload['model']} 已返回并完成服务器本地缓存验证" if ok else str(data)[:500],
    )


def _wechat_public_url(config: DailyReviewConfig, path: str) -> str:
    base = (config.wechat.sourceUrlBase or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        return ""
    return f"{base}{path if path.startswith('/') else f'/{path}'}"


def _wechat_public_asset_url(config: DailyReviewConfig, path: str) -> str:
    if path.startswith("/daily-review/assets/"):
        return _wechat_public_url(config, f"/api{path}")
    return _wechat_public_url(config, path)


def _wechat_run_url(config: DailyReviewConfig, run: dict[str, Any]) -> str:
    slug = str(run.get("topicSlug") or "").strip()
    if not slug:
        topic_id = str(run.get("topicId") or "").strip()
        for topic in config.topics:
            if topic.id == topic_id:
                slug = topic.slug
                break
    run_id = str(run.get("runId") or "").strip()
    if slug and run_id:
        return _wechat_public_url(config, f"/daily-review/{slug}/{run_id}")
    return _wechat_public_url(config, f"/daily-review/{slug}" if slug else "/daily-review")


def _wechat_require_config(config: DailyReviewConfig) -> WechatAdminConfig:
    wechat = config.wechat
    if not wechat.enabled:
        raise ApiError(400, "WECHAT_NOT_ENABLED", "公众号模块未启用")
    if not wechat.appId.strip() or not wechat.appSecret.strip():
        raise ApiError(400, "WECHAT_NOT_CONFIGURED", "公众号 AppID 或 AppSecret 未配置")
    return wechat


async def _wechat_token(config: DailyReviewConfig, *, force: bool = False) -> str:
    wechat = _wechat_require_config(config)
    path = _wechat_token_path()
    now = int(time.time())
    if not force and path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("appId") == wechat.appId and int(cached.get("expiresAt") or 0) > now + 120:
                token = str(cached.get("accessToken") or "")
                if token:
                    return token
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    payload = {"grant_type": "client_credential", "appid": wechat.appId.strip(), "secret": wechat.appSecret.strip(), "force_refresh": force}
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0)) as client:
        response = await client.post("https://api.weixin.qq.com/cgi-bin/stable_token", json=payload)
    data = response.json()
    token = str(data.get("access_token") or "")
    if response.status_code >= 400 or not token:
        raise ApiError(502, "WECHAT_TOKEN_FAILED", str(data)[:500])
    expires_in = int(data.get("expires_in") or 7200)
    path.write_text(json.dumps({"appId": wechat.appId, "accessToken": token, "expiresAt": now + max(60, expires_in - 120)}, ensure_ascii=False, indent=2), encoding="utf-8")
    return token


async def _wechat_api_post(config: DailyReviewConfig, endpoint: str, payload: dict[str, Any], *, retry: bool = True) -> dict[str, Any]:
    token = await _wechat_token(config)
    url = f"https://api.weixin.qq.com{endpoint}?access_token={token}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=8.0)) as client:
        response = await client.post(url, json=payload)
    data = response.json()
    if retry and data.get("errcode") in {40001, 42001, 40014}:
        token = await _wechat_token(config, force=True)
        url = f"https://api.weixin.qq.com{endpoint}?access_token={token}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=8.0)) as client:
            response = await client.post(url, json=payload)
        data = response.json()
    if response.status_code >= 400 or data.get("errcode"):
        raise ApiError(502, "WECHAT_API_FAILED", str(data)[:500])
    return data


async def _wechat_upload_image_material(config: DailyReviewConfig, image_url: str) -> str:
    token = await _wechat_token(config)
    url_value = image_url.strip()
    if not url_value:
        raise ApiError(400, "WECHAT_COVER_REQUIRED", "公众号封面图片缺失")
    if url_value.startswith("/"):
        url_value = _wechat_public_url(config, url_value)
    headers = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=8.0)) as client:
        content_type, data = await _download_remote_image(client, url_value, headers)
        files = {"media": (f"cover{ALLOWED_IMAGE_CONTENT_TYPES.get(content_type, '.png')}", data, content_type)}
        response = await client.post(f"https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={token}&type=image", files=files)
    result = response.json()
    media_id = str(result.get("media_id") or "")
    if response.status_code >= 400 or result.get("errcode") or not media_id:
        raise ApiError(502, "WECHAT_UPLOAD_COVER_FAILED", str(result)[:500])
    return media_id


async def _wechat_upload_content_image(config: DailyReviewConfig, image_url: str) -> str:
    token = await _wechat_token(config)
    url_value = image_url.strip()
    if not url_value:
        raise ApiError(400, "WECHAT_CONTENT_IMAGE_REQUIRED", "正文图片地址为空")
    if url_value.startswith("/"):
        url_value = _wechat_public_asset_url(config, url_value)
    headers = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=8.0)) as client:
        content_type, data = await _download_remote_image(client, url_value, headers)
        suffix = ALLOWED_IMAGE_CONTENT_TYPES.get(content_type, ".png")
        files = {"media": (f"content{suffix}", data, content_type)}
        response = await client.post(f"https://api.weixin.qq.com/cgi-bin/media/uploadimg?access_token={token}", files=files)
    result = response.json()
    url = str(result.get("url") or "")
    if response.status_code >= 400 or result.get("errcode") or not url:
        raise ApiError(502, "WECHAT_UPLOAD_CONTENT_IMAGE_FAILED", str(result)[:500])
    return url


async def _wechat_prepare_content_images(config: DailyReviewConfig, content_html: str) -> tuple[str, int, list[str]]:
    replacements: dict[str, str] = {}
    errors: list[str] = []
    img_sources = re.findall(r'<img\b[^>]*\bsrc=["\']([^"\']+)["\']', content_html, flags=re.IGNORECASE)
    for source in img_sources:
        if not source or source in replacements:
            continue
        if source.startswith(("data:", "blob:")):
            errors.append("跳过内嵌图片，微信草稿正文不支持 data/blob 图片")
            continue
        try:
            replacements[source] = await _wechat_upload_content_image(config, html.unescape(source))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{source[:120]}: {str(getattr(exc, 'message', exc))[:180]}")
    prepared = content_html
    for source, wechat_url in replacements.items():
        prepared = prepared.replace(f'src="{source}"', f'src="{html.escape(wechat_url)}"')
        prepared = prepared.replace(f"src='{source}'", f"src='{html.escape(wechat_url)}'")
    return prepared, len(replacements), errors


def _wechat_trim_text(value: Any, limit: int) -> str:
    text = _repair_mojibake(html.unescape(str(value or "")))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\?{3,}", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _wechat_pick_key_papers(papers: list[dict[str, Any]], limit: int = 7) -> list[tuple[int, dict[str, Any]]]:
    indexed = list(enumerate(papers, 1))
    strong = [
        item for item in indexed
        if int(item[1].get("relevanceScore") or 0) >= 40
        and int(item[1].get("qualityScore") or 0) >= 55
        and int(item[1].get("evidenceScore") or 0) >= 40
    ]
    usable = [
        item for item in indexed
        if int(item[1].get("relevanceScore") or 0) >= 5
        and (
            int(item[1].get("qualityScore") or 0) >= 55
            or int(item[1].get("evidenceScore") or 0) >= 40
        )
    ]
    candidates = strong if len(strong) >= min(3, limit) else (usable if usable else indexed)
    ranked = sorted(
        candidates,
        key=lambda item: (
            int(item[1].get("relevanceScore") or 0) * 2
            + int(item[1].get("qualityScore") or 0)
            + int(item[1].get("evidenceScore") or 0)
            + int(item[1].get("noveltyScore") or 0),
            _citation_count(item[1]),
        ),
        reverse=True,
    )
    return ranked[:limit]


def _wechat_review_paragraphs(run: dict[str, Any], limit: int = 4) -> list[str]:
    raw = _repair_mojibake(str(run.get("reviewMarkdown") or ""))
    raw = re.sub(r"```[\s\S]*?```", " ", raw)
    raw = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", raw)
    raw = re.sub(r"\[[0-9,\s，、;；\-]+\]", "", raw)
    blocks = re.split(r"\n\s*\n+", raw)
    paragraphs: list[str] = []
    skip_prefixes = ("#", "表", "|", "---")
    skip_markers = (
        "本文依据",
        "用户提供",
        "特别需要说明",
        "证据强度",
        "元数据",
        "未启用联网",
        "不编造",
        "不能替代",
        "仅在",
        "谨慎引用",
    )
    for block in blocks:
        text = re.sub(r"^#+\s*", "", block.strip())
        text = re.sub(r"[*_`>~-]+", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text or len(text) < 38:
            continue
        if text.startswith(skip_prefixes):
            continue
        if any(marker in text for marker in skip_markers):
            continue
        if _looks_mojibake(text) or text.count("?") >= 5:
            continue
        paragraphs.append(text)
        if len(paragraphs) >= limit:
            break
    return paragraphs


def _wechat_key_findings(run: dict[str, Any], topic: str) -> list[str]:
    paragraphs = _wechat_review_paragraphs(run, 5)
    findings: list[str] = []
    for text in paragraphs[:3]:
        findings.append(_wechat_trim_text(text, 150))
    if findings:
        return findings
    return [
        f"本期围绕「{topic}」筛选多源证据，重点观察研究是否出现新的技术路线、应用场景和评价指标变化。",
        "相比单篇论文解读，本期更关注多篇证据之间的共同指向：哪些问题被反复验证，哪些方向仍停留在早期探索。",
        "完整 DOI、来源、评分和证据位置保留在研域前沿综述平台，方便进一步追溯与复核。",
    ]


def _wechat_paper_signal(paper: dict[str, Any]) -> str:
    title = _wechat_trim_text(paper.get("title") or "代表性证据", 54)
    venue = _wechat_trim_text(paper.get("venue") or "来源未标注", 28)
    year = str(paper.get("year") or "n.d.")
    citation = paper.get("citationCount")
    score = f"相关 {paper.get('relevanceScore', 'n/a')} / 质量 {paper.get('qualityScore', 'n/a')}"
    cite_text = f" / 引用 {citation}" if citation is not None else ""
    return f"[{year}] {title}（{venue}{cite_text}，{score}）"


def _wechat_link_refs(text: str) -> str:
    escaped = html.escape(text)

    def repl(match: re.Match[str]) -> str:
        nums = re.findall(r"\d+", match.group(1))
        if not nums:
            return match.group(0)
        links = []
        for num in nums:
            links.append(
                f'<span style="display:inline-block;margin:0 2px;padding:1px 5px;'
                f'border-radius:999px;background:#eef1ff;color:#344066;'
                f'font-size:12px;font-weight:800;">[{html.escape(num)}]</span>'
            )
        return "".join(links)

    return re.sub(r"\[((?:\d+)(?:[\s,，、;；-]+(?:\d+))*)\]", repl, escaped)


def _wechat_latex_to_plain(expr: str) -> str:
    value = _repair_mojibake(html.unescape(str(expr or ""))).strip()
    value = re.sub(r"^\${1,2}|\${1,2}$", "", value).strip()
    value = re.sub(r"^\\\(|\\\)$", "", value).strip()
    value = re.sub(r"^\\\[|\\\]$", "", value).strip()
    for _ in range(4):
        value = re.sub(r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}", r"(\1)/(\2)", value)
        value = re.sub(r"\\sqrt\s*\{([^{}]+)\}", r"sqrt(\1)", value)
        value = re.sub(r"\\(?:mathrm|text|mathbf|mathit)\s*\{([^{}]+)\}", r"\1", value)
    replacements = {
        r"\alpha": "α",
        r"\beta": "β",
        r"\gamma": "γ",
        r"\delta": "δ",
        r"\epsilon": "ε",
        r"\theta": "θ",
        r"\lambda": "λ",
        r"\mu": "μ",
        r"\pi": "π",
        r"\rho": "ρ",
        r"\sigma": "σ",
        r"\tau": "τ",
        r"\phi": "φ",
        r"\omega": "ω",
        r"\Delta": "Δ",
        r"\Omega": "Ω",
        r"\times": "×",
        r"\cdot": "·",
        r"\pm": "±",
        r"\leq": "≤",
        r"\geq": "≥",
        r"\neq": "≠",
        r"\approx": "≈",
        r"\sim": "∼",
        r"\infty": "∞",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    value = re.sub(r"\\(?:left|right|,|;|!|quad|qquad)\s*", "", value)
    value = re.sub(r"_\{([^{}]+)\}", r"_(\1)", value)
    value = re.sub(r"\^\{([^{}]+)\}", r"^(\1)", value)
    value = re.sub(r"\\([A-Za-z]+)", r"\1", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or str(expr or "").strip()


def _wechat_math_html(expr: str, block: bool = False) -> str:
    text = html.escape(_wechat_latex_to_plain(expr))
    if block:
        return (
            '<section style="margin:16px 0;padding:13px 14px;background:#f6f0e5;'
            'border:1px solid #dfd0bd;border-radius:14px;box-shadow:0 8px 18px rgba(44,35,24,.08);'
            'overflow-x:auto;text-align:center;">'
            f'<code style="font-family:Menlo,Consolas,monospace;color:#5b321f;font-size:14px;'
            f'line-height:1.8;white-space:nowrap;">{text}</code>'
            '</section>'
        )
    return (
        '<code style="display:inline-block;margin:0 2px;padding:1px 6px;'
        'border-radius:8px;background:#f6f0e5;border:1px solid #dfd0bd;'
        f'color:#5b321f;font-family:Menlo,Consolas,monospace;font-size:13px;line-height:1.6;">{text}</code>'
    )


def _wechat_extract_inline_math(text: str) -> tuple[str, dict[str, str]]:
    value = str(text or "")
    snippets: dict[str, str] = {}

    def store(match: re.Match[str]) -> str:
        token = f"YFRMATHINLINE{len(snippets)}TOKEN"
        snippets[token] = _wechat_math_html(match.group(1))
        return token

    value = re.sub(r"\\\((.+?)\\\)", store, value)
    value = re.sub(r"\$(?!\s)([^$\n]{1,240}?)(?<!\s)\$", store, value)
    return value, snippets


def _wechat_inline_markdown(text: str) -> str:
    value = _repair_mojibake(html.unescape(str(text or "")))
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", value)
    value = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", value)
    value = re.sub(r"\s+", " ", value).strip()
    value, math_snippets = _wechat_extract_inline_math(value)
    rendered = _wechat_link_refs(value)
    for token, snippet in math_snippets.items():
        rendered = rendered.replace(html.escape(token), snippet).replace(token, snippet)
    return rendered


def _wechat_review_markdown_html(review_markdown: str) -> str:
    raw = _repair_mojibake(str(review_markdown or "")).replace("\r\n", "\n")
    raw = re.sub(r"```[\s\S]*?```", "", raw)
    blocks: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    table_rows: list[list[str]] = []
    compact_refs: list[str] = []
    in_references = False

    def flush_paragraph() -> None:
        nonlocal paragraph
        if not paragraph:
            return
        text = " ".join(item.strip() for item in paragraph if item.strip())
        paragraph = []
        if not text:
            return
        blocks.append(
            f'<p style="margin:0 0 14px;color:#2b2925;font-size:15px;'
            f'line-height:1.92;text-align:justify;">{_wechat_inline_markdown(text)}</p>'
        )

    def flush_list() -> None:
        nonlocal list_items
        if not list_items:
            return
        lis = "".join(
            f'<li style="margin:7px 0;color:#2b2925;font-size:14px;line-height:1.8;">{_wechat_inline_markdown(item)}</li>'
            for item in list_items
        )
        blocks.append(
            f'<ul style="margin:0 0 16px;padding:12px 16px 12px 28px;'
            f'background:#fffdf8;border:1px solid #e2d7c5;border-radius:12px;">{lis}</ul>'
        )
        list_items = []

    def flush_table() -> None:
        nonlocal table_rows
        if not table_rows:
            return
        rows = table_rows[:8]
        table_rows = []
        body = []
        for row_index, row in enumerate(rows):
            cells = row[:4]
            tag = "th" if row_index == 0 else "td"
            bg = "background:#f4ecd9;" if row_index == 0 else ""
            body.append(
                "<tr>"
                + "".join(
                    f'<{tag} style="padding:8px 9px;border:1px solid #ded2c2;'
                    f'{bg}color:#242220;font-size:12px;line-height:1.55;text-align:left;">'
                    f'{_wechat_inline_markdown(cell)}</{tag}>'
                    for cell in cells
                )
                + "</tr>"
            )
        blocks.append(
            f'<section style="margin:16px 0;overflow-x:auto;">'
            f'<table style="width:100%;border-collapse:collapse;background:#fffdf8;">{"".join(body)}</table>'
            f'</section>'
        )

    def flush_compact_refs() -> None:
        nonlocal compact_refs
        if not compact_refs:
            return
        items = "".join(
            f'<p style="margin:0 0 8px;color:#5b554d;font-size:12px;line-height:1.62;">{_wechat_inline_markdown(item)}</p>'
            for item in compact_refs[:80]
        )
        blocks.append(
            f'<section style="margin:18px 0;padding:14px;background:#f8f1e6;'
            f'border:1px solid #e2d7c5;border-radius:12px;">'
            f'<p style="margin:0 0 10px;color:#7b2d19;font-size:13px;font-weight:900;">文献索引</p>'
            f'{items}</section>'
        )
        compact_refs = []

    for line in raw.splitlines():
        stripped = line.strip()
        if re.fullmatch(r"[-*_]{3,}", stripped):
            flush_paragraph()
            flush_list()
            flush_table()
            continue
        if not stripped:
            flush_paragraph()
            flush_list()
            flush_table()
            continue
        block_math = re.fullmatch(r"\$\$(.+?)\$\$", stripped) or re.fullmatch(r"\\\[(.+?)\\\]", stripped)
        if block_math:
            flush_paragraph()
            flush_list()
            flush_table()
            flush_compact_refs()
            blocks.append(_wechat_math_html(block_math.group(1), block=True))
            continue
        if re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?", stripped):
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            flush_paragraph()
            flush_list()
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells:
                table_rows.append(cells)
            continue
        flush_table()
        heading = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            flush_list()
            flush_compact_refs()
            level = len(heading.group(1))
            text = _wechat_inline_markdown(heading.group(2))
            plain_heading = re.sub(r"[#*_`]+", "", heading.group(2)).strip()
            in_references = bool(re.search(r"(参考文献|文献索引|References|参考资料)", plain_heading, re.I))
            if level == 1:
                blocks.append(
                    f'<h2 style="margin:26px 0 12px;padding:0 0 8px;'
                    f'border-bottom:2px solid #242220;color:#242220;'
                    f'font-size:20px;line-height:1.35;font-weight:900;">{text}</h2>'
                )
            else:
                blocks.append(
                    f'<h3 style="margin:22px 0 10px;padding-left:10px;'
                    f'border-left:5px solid #d15f3f;color:#242220;'
                    f'font-size:17px;line-height:1.45;font-weight:900;">{text}</h3>'
            )
            continue
        if in_references:
            compact_refs.append(stripped.lstrip("-*+ ").strip())
            continue
        list_match = re.match(r"^[-*+]\s+(.+)$", stripped) or re.match(r"^\d+[.)、]\s+(.+)$", stripped)
        if list_match:
            flush_paragraph()
            list_items.append(list_match.group(1))
            continue
        if stripped.startswith(">"):
            flush_paragraph()
            flush_list()
            quote = stripped.lstrip("> ").strip()
            blocks.append(
                f'<blockquote style="margin:14px 0;padding:12px 14px;'
                f'background:#eef1ff;border-left:5px solid #3e63a8;color:#344066;'
                f'font-size:14px;line-height:1.8;">{_wechat_inline_markdown(quote)}</blockquote>'
            )
            continue
        paragraph.append(stripped)
    flush_paragraph()
    flush_list()
    flush_table()
    flush_compact_refs()
    if not blocks:
        return (
            '<p style="margin:0;color:#2b2925;font-size:15px;line-height:1.9;">'
            '本期综述正文为空，请回到平台查看生成状态。'
            '</p>'
        )
    return "".join(blocks)


def _wechat_review_outline(review_markdown: str, limit: int = 6) -> str:
    titles: list[str] = []
    for line in str(review_markdown or "").splitlines():
        match = re.match(r"^#{2,3}\s+(.+)$", line.strip())
        if not match:
            continue
        title = _wechat_trim_text(match.group(1), 32)
        if not title or re.search(r"(参考文献|文献索引|References)", title, re.I):
            continue
        titles.append(title)
        if len(titles) >= limit:
            break
    if not titles:
        return ""
    tags = "".join(_wechat_tag(title, "#344066", "#eef1ff") for title in titles)
    return (
        '<section style="margin:14px 0 18px;padding:14px;background:#f4ecd9;'
        'border:1px solid #e2d7c5;border-radius:14px;">'
        '<p style="margin:0 0 8px;color:#7b2d19;font-size:13px;font-weight:900;">阅读速览</p>'
        f'<p style="margin:0;">{tags}</p>'
        '</section>'
    )


def _wechat_representative_papers(papers: list[dict[str, Any]], limit: int = 3) -> list[tuple[int, dict[str, Any]]]:
    ranked = _wechat_pick_key_papers(papers, limit=max(limit * 4, 8))
    strong = [
        item for item in ranked
        if int(item[1].get("relevanceScore") or 0) >= 35
        and int(item[1].get("qualityScore") or 0) >= 55
    ]
    if strong:
        return strong[:limit]
    moderate = [
        item for item in ranked
        if int(item[1].get("relevanceScore") or 0) >= 20
        and int(item[1].get("qualityScore") or 0) >= 50
    ]
    return moderate[:limit]


def _wechat_metric(value: Any, fallback: str = "0") -> str:
    if value is None:
        return fallback
    return str(value)


def _wechat_article_title(config: DailyReviewConfig, run: dict[str, Any]) -> str:
    delta = run.get("dailyDelta") if isinstance(run.get("dailyDelta"), dict) else {}
    subtitle = _wechat_trim_text(run.get("subtitle") or delta.get("subtitle"), 28)
    topic = _wechat_trim_text(run.get("topicName") or run.get("topic"), 30)
    if not topic or _looks_mojibake(topic) or topic.count("?") >= 3:
        topic = "科研前沿"
    mode = _mode_label(str(delta.get("mode") or ""))
    if subtitle:
        if _looks_mojibake(subtitle) or subtitle.count("?") >= 3:
            subtitle = ""
    if subtitle:
        return _wechat_trim_text(f"{topic}：{subtitle}", 64)
    if topic == "科研前沿":
        return _wechat_trim_text(f"科研前沿雷达｜{mode}", 64)
    return _wechat_trim_text(f"{topic}｜{mode}", 64)


def _wechat_digest(config: DailyReviewConfig, run: dict[str, Any]) -> str:
    delta = run.get("dailyDelta") if isinstance(run.get("dailyDelta"), dict) else {}
    prefix = _wechat_trim_text(config.wechat.digestPrefix or "研域前沿综述", 18) or "研域前沿综述"
    return _wechat_trim_text(
        f"{prefix}：本期纳入 {len(run.get('papers') or [])} 篇证据，"
        f"{delta.get('modeLabel') or _mode_label(str(delta.get('mode') or ''))}，"
        f"新增 {delta.get('newEvidenceCount', 0)} 篇，高新颖 {delta.get('highNoveltyCount', 0)} 篇。",
        120,
    ) or f"{prefix}：本期科研前沿证据导读。"


def _wechat_tag(text: str, color: str, bg: str) -> str:
    return f'<span style="display:inline-block;padding:4px 9px;margin:3px 5px 3px 0;border-radius:999px;background:{bg};color:{color};font-size:12px;font-weight:700;">{html.escape(text)}</span>'


def _wechat_article_html(config: DailyReviewConfig, run: dict[str, Any], title: str, digest: str) -> str:
    papers = [paper for paper in (run.get("papers") or []) if isinstance(paper, dict)]
    delta = run.get("dailyDelta") if isinstance(run.get("dailyDelta"), dict) else {}
    mode_label = str(delta.get("modeLabel") or _mode_label(str(delta.get("mode") or "")))
    source_url = _wechat_run_url(config, run)
    topic = _wechat_trim_text(run.get("topicName") or run.get("topic"), 40)
    if not topic:
        topic = "本主题"
    subtitle = _wechat_trim_text(run.get("subtitle") or delta.get("subtitle") or "", 60)
    year = datetime.now(BEIJING_TZ).strftime("%Y.%m.%d")
    source_counts: dict[str, int] = {}
    for paper in papers:
        for source in paper.get("sources") or [paper.get("source") or "unknown"]:
            source_counts[str(source)] = source_counts.get(str(source), 0) + 1
    source_tags = "".join(_wechat_tag(name, "#25323f", "#e8f1ff") for name, _ in sorted(source_counts.items(), key=lambda x: -x[1])[:6])
    chips = "".join([
        _wechat_tag(mode_label, "#7b2d19", "#fff0e8"),
        _wechat_tag(f"{len(papers)} 篇证据", "#164331", "#eaf7ef"),
        _wechat_tag(f"{run.get('fullTextFetched') or 0} 篇全文片段", "#344066", "#eef1ff"),
        _wechat_tag(f"新增 {delta.get('newEvidenceCount', 0)}", "#5c3f00", "#fff5cc"),
    ])
    review_markdown = str(run.get("reviewMarkdown") or "")
    review_body = _wechat_review_markdown_html(review_markdown)
    image = run.get("image") if isinstance(run.get("image"), dict) else {}
    image_url = str(image.get("url") or "").strip()
    figure_block = ""
    if image_url and not image_url.startswith("data:"):
        if image_url.startswith("/"):
            image_url = _wechat_public_asset_url(config, image_url)
        figure_block = f"""
  <section style="margin:22px 4px 18px;background:#fffdf8;border:1px solid #e2d7c5;border-radius:18px;overflow:hidden;box-shadow:0 14px 30px rgba(44,35,24,.16),0 2px 0 rgba(255,255,255,.92) inset;">
    <img src="{html.escape(image_url)}" alt="一图看懂" style="display:block;width:100%;height:auto;margin:0;" />
  </section>
"""
    return f"""
<section style="max-width:677px;margin:0 auto;padding:18px 14px 28px;box-sizing:border-box;background:#efe6d7;color:#202124;font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue','PingFang SC','Microsoft YaHei',sans-serif;">
  <section style="margin:0 2px;padding:28px 24px 22px;background:linear-gradient(135deg,#1f2933 0%,#3b4a54 52%,#8b3f2b 100%);border:1px solid rgba(255,255,255,.24);border-radius:20px;color:#fff;box-shadow:0 18px 34px rgba(31,41,51,.26),0 2px 0 rgba(255,255,255,.18) inset;">
    <p style="margin:0 0 10px;color:#ffd18a;font-size:13px;font-weight:800;letter-spacing:.5px;">研域前沿综述 · {html.escape(year)}</p>
    <h1 style="margin:0;color:#fff;font-size:24px;line-height:1.28;font-weight:900;">{html.escape(title)}</h1>
    <p style="margin:12px 0 0;color:#f8ead4;font-size:14px;line-height:1.75;">{html.escape(subtitle or digest)}</p>
  </section>

  <section style="padding:18px 6px 8px;">{chips}</section>

  {figure_block}

  <section style="margin:22px 4px;padding:24px 20px;background:#fffdf8;border:1px solid #e2d7c5;border-radius:20px;box-shadow:0 18px 38px rgba(44,35,24,.15),0 2px 0 rgba(255,255,255,.95) inset;">
    <p style="margin:0 0 14px;color:#7b2d19;font-size:13px;font-weight:900;letter-spacing:.5px;">研域前沿综述 · FULL TEXT</p>
    {review_body}
  </section>

  <section style="margin:22px 4px;padding:18px 17px;background:#f3f5ff;border:1px solid #d7defb;border-radius:18px;box-shadow:0 12px 24px rgba(52,64,102,.11),0 2px 0 rgba(255,255,255,.9) inset;">
    <p style="margin:0 0 8px;color:#344066;font-size:13px;font-weight:900;">检索来源</p>
    <p style="margin:0 0 8px;color:#3a3630;font-size:13px;line-height:1.7;">本期证据来自多源合并、去重和评分筛选；完整 DOI、摘要/全文片段、四项评分与证据位置请点击阅读原文查看。</p>
    <p style="margin:0;">{source_tags or _wechat_tag("Hybrid", "#344066", "#eef1ff")}</p>
  </section>

  <section style="margin:24px 4px 0;padding:20px 18px;background:#202124;border:1px solid rgba(255,255,255,.12);border-radius:18px;color:#fff;box-shadow:0 16px 30px rgba(32,33,36,.23);">
    <p style="margin:0 0 8px;color:#ffd18a;font-size:13px;font-weight:900;">完整证据链</p>
    <p style="margin:0;color:#f4eadb;font-size:14px;line-height:1.8;">点击“阅读原文”进入平台：查看完整综述、文献证据、DOI、来源、评分和一图看懂。</p>
    <p style="margin:10px 0 0;color:#b9c3cc;font-size:12px;line-height:1.6;">{html.escape(source_url)}</p>
  </section>
</section>
""".strip()


def _wechat_article_text(config: DailyReviewConfig, run: dict[str, Any], title: str, digest: str) -> str:
    papers = [paper for paper in (run.get("papers") or []) if isinstance(paper, dict)]
    delta = run.get("dailyDelta") if isinstance(run.get("dailyDelta"), dict) else {}
    review = _repair_mojibake(str(run.get("reviewMarkdown") or "")).strip()
    lines = [
        f"# {title}",
        "",
        digest,
        "",
        f"- 模式：{delta.get('modeLabel') or _mode_label(str(delta.get('mode') or ''))}",
        f"- 证据：{len(papers)} 篇",
        f"- 新增：{delta.get('newEvidenceCount', 0)} 篇",
        f"- 高新颖：{delta.get('highNoveltyCount', 0)} 篇",
        "",
        "## 全文综述",
    ]
    lines.append(review or "本期综述正文为空，请回到平台查看生成状态。")
    lines.extend(["", f"完整证据链：{_wechat_run_url(config, run)}"])
    return "\n".join(lines)


def _build_wechat_article(config: DailyReviewConfig, run: dict[str, Any], title: str | None = None, digest: str | None = None) -> WechatArticleResult:
    title_value = _wechat_trim_text(title or _wechat_article_title(config, run), 64)
    digest_value = _wechat_trim_text(digest or _wechat_digest(config, run), 120)
    source_url = _wechat_run_url(config, run)
    cover = str(config.wechat.coverImageUrl or "").strip()
    if not cover or cover.startswith("data:"):
        cover = _image_asset_url(Path(_save_image_asset(_wechat_cover_png(title_value, digest_value), ".png")).name)
    if cover.startswith("/"):
        cover = _wechat_public_asset_url(config, cover)
    html_value = _wechat_article_html(config, run, title_value, digest_value)
    text_value = _wechat_article_text(config, run, title_value, digest_value)
    wechat_state = run.get("wechat") if isinstance(run.get("wechat"), dict) else {}
    return WechatArticleResult(
        runId=str(run.get("runId") or ""),
        title=title_value,
        digest=digest_value,
        contentHtml=html_value,
        contentText=text_value,
        coverUrl=cover or None,
        sourceUrl=source_url,
        articleUrl=source_url,
        draftMediaId=wechat_state.get("draftMediaId"),
        status=str(wechat_state.get("status") or "generated"),
        message=wechat_state.get("message"),
    )


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _wechat_cover_png(title: str, digest: str = "") -> bytes:
    try:
        return _wechat_cover_png_pillow(title, digest)
    except Exception as exc:  # noqa: BLE001
        log.warning("wechat cover text rendering failed, using vector fallback: %s", exc)
        return _wechat_cover_vector_png(title, digest)


def _wechat_cover_png_pillow(title: str, digest: str = "") -> bytes:
    from io import BytesIO

    from PIL import Image, ImageDraw, ImageFont

    width, height = 900, 383
    title = _wechat_trim_text(title, 42) or "科研前沿日报"
    digest = _wechat_trim_text(digest, 58)
    if not title or "绉戠爺" in title or "鍓嶆部" in title:
        title = "科研前沿日报"
    title_hash = hashlib.sha256(f"{title}|{digest}".encode("utf-8")).digest()
    palettes = [
        ((28, 38, 48), (203, 85, 58), (176, 202, 226), (248, 243, 234)),
        ((30, 47, 55), (113, 151, 132), (235, 188, 102), (246, 242, 233)),
        ((34, 35, 38), (209, 95, 63), (198, 219, 207), (249, 245, 237)),
    ]
    ink, accent, soft, paper = palettes[title_hash[0] % len(palettes)]

    def font(size: int, bold: bool = False) -> Any:
        candidates = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/simsun.ttc",
        ]
        for candidate in candidates:
            try:
                if candidate and Path(candidate).exists():
                    return ImageFont.truetype(candidate, size=size)
            except Exception:
                continue
        return ImageFont.load_default()

    def wrap_text(draw: Any, text: str, font_obj: Any, max_width: int, max_lines: int) -> list[str]:
        lines: list[str] = []
        current = ""
        for char in text:
            trial = f"{current}{char}"
            bbox = draw.textbbox((0, 0), trial, font=font_obj)
            if bbox[2] - bbox[0] <= max_width:
                current = trial
                continue
            if current:
                lines.append(current)
            current = char
            if len(lines) >= max_lines:
                break
        if current and len(lines) < max_lines:
            lines.append(current)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
        if len(lines) == max_lines and len("".join(lines)) < len(text):
            lines[-1] = lines[-1].rstrip("，。；、:： ") + "..."
        return lines

    # 微信封面实际使用 2.35:1，转发卡片/公众号主页会取中心 1:1 裁剪。
    # 因此标题、品牌和日期都放在 x=258..641 的中心安全区内。
    img = Image.new("RGB", (width, height), paper)
    draw = ImageDraw.Draw(img)
    for x in range(0, width, 42):
        shade = tuple(max(0, c - 9) for c in paper)
        draw.line((x, 0, x, height), fill=shade, width=1)
    for y in range(0, height, 42):
        shade = tuple(max(0, c - 9) for c in paper)
        draw.line((0, y, width, y), fill=shade, width=1)
    draw.ellipse((566, 12, 782, 228), fill=tuple(int(paper[i] * 0.72 + soft[i] * 0.28) for i in range(3)))
    draw.ellipse((642, 204, 954, 516), fill=tuple(int(paper[i] * 0.76 + accent[i] * 0.24) for i in range(3)))
    draw.rounded_rectangle((40, 34, 860, 349), radius=24, fill=(255, 253, 248), outline=ink, width=3)
    draw.rounded_rectangle((66, 60, 834, 323), radius=16, outline=tuple(int(ink[i] * 0.82 + 255 * 0.18) for i in range(3)), width=2)
    draw.rectangle((92, 92, 220, 116), outline=ink, width=3)
    for x in range(95, 218, 10):
        draw.line((x, 113, x + 18, 95), fill=accent, width=4)
    bar_specs = [(96, 276, 58, accent), (132, 258, 86, soft), (168, 236, 112, accent), (204, 208, 142, soft)]
    for left, bottom, bar_height, color in bar_specs:
        draw.rounded_rectangle((left, bottom - bar_height, left + 22, bottom), radius=8, fill=color)

    graph_panel = tuple(int(paper[i] * 0.58 + soft[i] * 0.42) for i in range(3))
    draw.rounded_rectangle((672, 92, 792, 252), radius=18, fill=graph_panel, outline=tuple(int(ink[i] * 0.72 + 255 * 0.28) for i in range(3)), width=2)
    node_color = tuple(int(ink[i] * 0.78 + accent[i] * 0.22) for i in range(3))
    edge_color = tuple(int(soft[i] * 0.54 + accent[i] * 0.46) for i in range(3))
    nodes = [(704, 130), (756, 118), (740, 176), (704, 214), (770, 220)]
    for start, end in [(0, 1), (0, 2), (1, 2), (2, 3), (2, 4), (3, 4)]:
        draw.line((nodes[start][0], nodes[start][1], nodes[end][0], nodes[end][1]), fill=edge_color, width=3)
    for index, (cx, cy) in enumerate(nodes):
        radius = 8 if index != 2 else 11
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=accent if index == 2 else (255, 253, 248), outline=node_color, width=3)
    draw.rounded_rectangle((684, 270, 812, 296), radius=13, fill=tuple(int(paper[i] * 0.52 + accent[i] * 0.48) for i in range(3)))
    for x in range(698, 790, 18):
        draw.line((x, 288, x + 18, 278), fill=(255, 253, 248), width=3)

    content_x = 278
    content_width = 344
    small = font(19, True)
    title_font = font(36, True)
    subtitle_font = font(17, False)
    meta_font = font(16, False)
    draw.text((content_x, 78), "研域前沿综述", fill=accent, font=small)
    y = 118
    for line in wrap_text(draw, title, title_font, content_width, 3):
        draw.text((content_x, y), line, fill=ink, font=title_font)
        y += 43
    if digest:
        digest_lines = wrap_text(draw, digest, subtitle_font, content_width, 1 if y > 240 else 2)
        y = min(y + 8, 254)
        for line in digest_lines:
            draw.text((content_x + 1, y), line, fill=(80, 76, 68), font=subtitle_font)
            y += 24
    today = datetime.now(BEIJING_TZ).strftime("%Y.%m.%d")
    draw.rounded_rectangle((content_x, 284, 626, 313), radius=14, fill=tuple(int(paper[i] * 0.55 + soft[i] * 0.45) for i in range(3)))
    draw.text((content_x + 18, 290), f"滚动日报 · 可溯源文献综述 · {today}", fill=ink, font=meta_font)
    draw.text((94, 276), "研域", fill=ink, font=font(30, True))
    out = BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()

    img = Image.new("RGB", (width, height), paper)
    draw = ImageDraw.Draw(img)
    for x in range(0, width, 42):
        shade = tuple(max(0, c - 9) for c in paper)
        draw.line((x, 0, x, height), fill=shade, width=1)
    for y in range(0, height, 42):
        shade = tuple(max(0, c - 9) for c in paper)
        draw.line((0, y, width, y), fill=shade, width=1)
    draw.ellipse((592, 18, 828, 254), fill=tuple(int(paper[i] * 0.72 + soft[i] * 0.28) for i in range(3)))
    draw.ellipse((640, 250, 980, 590), fill=tuple(int(paper[i] * 0.76 + accent[i] * 0.24) for i in range(3)))
    draw.rounded_rectangle((52, 48, 848, 452), radius=28, fill=(255, 253, 248), outline=ink, width=3)
    draw.rounded_rectangle((78, 76, 822, 424), radius=18, outline=tuple(int(ink[i] * 0.82 + 255 * 0.18) for i in range(3)), width=2)
    draw.rectangle((94, 106, 226, 130), outline=ink, width=3)
    for x in range(97, 224, 10):
        draw.line((x, 127, x + 18, 109), fill=accent, width=4)
    bar_specs = [(98, 328, 70, accent), (134, 300, 105, soft), (170, 268, 137, accent), (206, 226, 179, soft)]
    for left, bottom, bar_height, color in bar_specs:
        draw.rounded_rectangle((left, bottom - bar_height, left + 22, bottom), radius=8, fill=color)
    small = font(24, True)
    title_font = font(44, True)
    subtitle_font = font(20, False)
    meta_font = font(18, False)
    draw.text((270, 104), "研域前沿综述", fill=accent, font=small)
    lines = wrap_text(draw, title, title_font, 500, 3)
    y = 154
    for line in lines:
        draw.text((270, y), line, fill=ink, font=title_font)
        y += 54
    if digest:
        digest_lines = wrap_text(draw, digest, subtitle_font, 500, 1 if len(lines) >= 3 else 2)
        y = min(y + 12, 332)
        for line in digest_lines:
            draw.text((272, y), line, fill=(80, 76, 68), font=subtitle_font)
            y += 30
    today = datetime.now(BEIJING_TZ).strftime("%Y.%m.%d")
    draw.rounded_rectangle((270, 370, 760, 404), radius=17, fill=tuple(int(paper[i] * 0.55 + soft[i] * 0.45) for i in range(3)))
    draw.text((292, 376), f"滚动日报 · 可溯源文献综述 · {today}", fill=ink, font=meta_font)
    draw.text((94, 382), "研域", fill=ink, font=font(34, True))
    out = BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _wechat_cover_vector_png(title: str, digest: str = "") -> bytes:
    width, height = 900, 383
    title_hash = hashlib.sha256(f"{title}|{digest}".encode("utf-8")).digest()
    palettes = [
        ((28, 38, 48), (224, 188, 112), (196, 219, 207), (240, 244, 239)),
        ((34, 35, 38), (202, 91, 61), (176, 202, 226), (249, 244, 235)),
        ((27, 46, 58), (115, 160, 140), (236, 193, 116), (246, 241, 232)),
    ]
    ink, accent, soft, paper = palettes[title_hash[0] % len(palettes)]
    grid = (220, 213, 201)
    line = (255, 255, 255)

    def mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
        t = max(0.0, min(1.0, t))
        return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))

    def inside_rounded_rect(x: int, y: int, left: int, top: int, right: int, bottom: int, radius: int) -> bool:
        if not (left <= x <= right and top <= y <= bottom):
            return False
        corners = [
            (left + radius, top + radius, x < left + radius and y < top + radius),
            (right - radius, top + radius, x > right - radius and y < top + radius),
            (left + radius, bottom - radius, x < left + radius and y > bottom - radius),
            (right - radius, bottom - radius, x > right - radius and y > bottom - radius),
        ]
        for cx, cy, active in corners:
            if active:
                return (x - cx) * (x - cx) + (y - cy) * (y - cy) <= radius * radius
        return True

    def on_rect_border(x: int, y: int, left: int, top: int, right: int, bottom: int, radius: int, width_px: int = 2) -> bool:
        outer = inside_rounded_rect(x, y, left, top, right, bottom, radius)
        inner = inside_rounded_rect(x, y, left + width_px, top + width_px, right - width_px, bottom - width_px, max(0, radius - width_px))
        return outer and not inner

    bars = [
        (96, 218, 58 + title_hash[1] % 70, accent),
        (132, 172, 86 + title_hash[2] % 86, soft),
        (168, 124, 112 + title_hash[3] % 112, accent),
        (204, 66, 142 + title_hash[4] % 142, soft),
    ]

    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            base = mix(paper, (232, 222, 208), (x + y) / (width + height) * 0.55)
            if (x - 690) * (x - 690) + (y - 110) * (y - 110) < 108 * 108:
                base = mix(base, soft, 0.35)
            if (x - 780) * (x - 780) + (y - 300) * (y - 300) < 148 * 148:
                base = mix(base, accent, 0.18)
            if x % 56 == 0 or y % 56 == 0:
                base = mix(base, grid, 0.22)
            if inside_rounded_rect(x, y, 40, 34, 860, 349, 24):
                base = mix(base, (255, 253, 248), 0.78)
            if on_rect_border(x, y, 40, 34, 860, 349, 24, 3):
                base = ink
            if inside_rounded_rect(x, y, 66, 60, 834, 323, 16):
                if on_rect_border(x, y, 66, 60, 834, 323, 16, 2):
                    base = mix(ink, line, 0.18)
            for left, top, bar_height, color in bars:
                right = left + 22
                bottom = min(286, top + bar_height)
                if inside_rounded_rect(x, y, left, top, right, bottom, 7):
                    base = color
            if 278 <= x <= 622 and 122 <= y <= 130:
                base = ink
            if 278 <= x <= 596 and 176 <= y <= 184:
                base = accent
            if 278 <= x <= 612 and 232 <= y <= 240:
                base = soft
            if 278 <= x <= 626 and 284 <= y <= 313:
                base = mix(ink, paper, 0.35)
            if 92 <= x <= 220 and 92 <= y <= 116:
                base = ink
            if 95 <= x <= 218 and 95 <= y <= 113:
                base = accent if (x + y) % 11 < 6 else soft
            raw.extend(base)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + _png_chunk(b"IEND", b"")


def _fallback_png(_prompt: str) -> bytes:
    width, height = 1200, 720
    bg = (247, 243, 234)
    panel = (255, 253, 250)
    ink = (31, 41, 51)
    colors = [(200, 221, 207), (244, 201, 118), (183, 201, 226), (233, 184, 166)]

    def pixel(x: int, y: int) -> tuple[int, int, int]:
        if 56 <= x <= 1144 and 52 <= y <= 668:
            if x in {56, 57, 1143, 1144} or y in {52, 53, 667, 668}:
                return ink
            if 90 <= x <= 1110 and 215 <= y <= 350:
                index = min(3, max(0, (x - 90) // 255))
                return colors[index]
            if 90 <= x <= 1110 and 470 <= y <= 535:
                return (232, 238, 231)
            if 90 <= x <= 1110 and 565 <= y <= 610:
                return (225, 233, 244)
            return panel
        return bg

    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(pixel(x, y))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + _png_chunk(b"IEND", b"")


@router.post("/admin/login", response_model=AdminLoginResult)
async def admin_login(body: AdminLoginRequest, request: Request):
    _check_admin_login_rate_limit(request)
    if not _verify_admin_password(body.username, body.password):
        raise ApiError(401, "ADMIN_LOGIN_FAILED", "管理员账号或密码错误")
    token, expires = _make_admin_token(body.username)
    return AdminLoginResult(token=token, username=body.username, expiresAt=expires.isoformat())


@router.post("/admin/password")
async def change_admin_password(body: AdminPasswordChangeRequest, request: Request):
    _require_admin(request)
    data = _decode_admin_token(request.headers.get("Authorization", "").removeprefix("Bearer ").strip())
    username = str((data or {}).get("u") or "")
    if not _verify_admin_password(username, body.oldPassword):
        raise ApiError(401, "ADMIN_PASSWORD_INVALID", "原管理员密码错误")
    _save_admin_auth(username, body.newPassword)
    return {"ok": True}


@router.get("/config", response_model=DailyReviewConfigView)
async def get_config_view(request: Request):
    _require_admin(request)
    return _view_config(_load_config())


@router.put("/config", response_model=DailyReviewConfigView)
async def put_config(body: DailyReviewConfig, request: Request):
    _require_admin(request)
    scope = request.headers.get("X-Daily-Review-Config-Scope", "general").strip().lower()
    old = _load_config()
    resolved = _with_resolved_secrets(body)
    if scope == "cdks":
        old.literatureSearchCdk = resolved.literatureSearchCdk
        old.literatureSearchCdks = resolved.literatureSearchCdks
        _save_config(old)
    else:
        resolved.literatureSearchCdk = old.literatureSearchCdk
        resolved.literatureSearchCdks = old.literatureSearchCdks
        _save_config(resolved)
    return _view_config(_load_config())


@router.get("/latest")
async def get_latest_run(topic: str | None = None):
    config = _load_config()
    if not _is_public_topic_filter(config, topic):
        return {"result": None}
    if topic:
        return {"result": _latest_run(topic)}
    for run in reversed(_iter_runs()):
        if _is_public_run(config, run):
            return {"result": _ensure_run_evidence_scores(run)}
    return {"result": None}


@router.get("/topics", response_model=DailyReviewTopicsResult)
async def get_topics():
    config = _load_config()
    return DailyReviewTopicsResult(items=[t for t in config.topics if t.enabled and not t.privateOnly])


@router.get("/exclusive/topics", response_model=DailyReviewTopicsResult)
async def get_exclusive_topics(request: Request):
    config = _load_config()
    _require_exclusive_access(config, request)
    return DailyReviewTopicsResult(items=[t for t in config.topics if _is_exclusive_topic(t)])


@router.get("/exclusive/latest")
async def get_exclusive_latest_run(request: Request, topic: str | None = None):
    config = _load_config()
    _require_exclusive_access(config, request)
    if not _is_exclusive_topic_filter(config, topic):
        return {"result": None}
    if topic:
        run = _latest_run(topic)
        if run is None or not _is_exclusive_run(config, run):
            return {"result": None}
        return {"result": run}
    for run in reversed(_iter_runs()):
        if _is_exclusive_run(config, run):
            return {"result": _ensure_run_evidence_scores(run)}
    return {"result": None}


@router.get("/exclusive/history", response_model=DailyReviewHistoryResult)
async def get_exclusive_history(request: Request, limit: int = 30, topic: str | None = None):
    config = _load_config()
    _require_exclusive_access(config, request)
    if not _is_exclusive_topic_filter(config, topic):
        return DailyReviewHistoryResult(items=[])
    limit = max(1, min(limit, 200))
    runs = [run for run in reversed(_iter_runs()) if _is_exclusive_run(config, run) and _matches_topic_filter(run, topic)][:limit]
    return DailyReviewHistoryResult(items=[_run_summary(run) for run in runs])


@router.get("/exclusive/runs/{run_id}")
async def get_exclusive_run(run_id: str, request: Request):
    config = _load_config()
    _require_exclusive_access(config, request)
    run = _find_run(run_id)
    if run is None or not _is_exclusive_run(config, run):
        raise ApiError(404, "RUN_NOT_FOUND", "未找到该期专属综述")
    return {"result": run}


@router.get("/history", response_model=DailyReviewHistoryResult)
async def get_history(limit: int = 30, topic: str | None = None):
    config = _load_config()
    if not _is_public_topic_filter(config, topic):
        return DailyReviewHistoryResult(items=[])
    limit = max(1, min(limit, 200))
    runs = [run for run in reversed(_iter_runs()) if _is_public_run(config, run) and _matches_topic_filter(run, topic)][:limit]
    return DailyReviewHistoryResult(items=[_run_summary(run) for run in runs])


@router.get("/admin/runs", response_model=DailyReviewHistoryResult)
async def get_admin_runs(request: Request, limit: int = 80, topic: str | None = None):
    _require_admin(request)
    limit = max(1, min(limit, 300))
    runs = [run for run in reversed(_iter_runs()) if _matches_topic_filter(run, topic)][:limit]
    return DailyReviewHistoryResult(items=[_run_summary(run) for run in runs])


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    config = _load_config()
    run = _find_run(run_id)
    if run is None or not _is_public_run(config, run):
        raise ApiError(404, "RUN_NOT_FOUND", "未找到该期日报")
    return {"result": run}


@router.post("/literature-cdk/status", response_model=LiteratureCdkStatusResult)
async def get_literature_cdk_status(body: LiteratureCdkStatusRequest, request: Request):
    _check_literature_search_rate_limit(request)
    config = _load_config()
    cdk = _find_valid_literature_cdk(config, body.cdk)
    if not cdk:
        return LiteratureCdkStatusResult(ok=False, cdk=None, message="CDK 不存在、已停用、已过期或次数已用完")
    return LiteratureCdkStatusResult(ok=True, cdk=_literature_cdk_public_info(cdk), message="CDK 可用")


async def _execute_literature_search(
    body: LiteratureOnlySearchRequest,
    app: Any,
    request: Request | None = None,
    search_id: str | None = None,
    progress: ProgressCallback | None = None,
) -> LiteratureOnlySearchResult:
    config = _load_config()
    cdk = _find_valid_literature_cdk(config, body.cdk or "")
    has_own_llm = bool(
        body.llm
        and _usable_secret_override(body.llm.apiKey)
        and body.llm.baseUrl.strip()
        and body.llm.model.strip()
    )
    if not cdk and not has_own_llm:
        raise ApiError(400, "LITERATURE_SEARCH_LLM_REQUIRED", "请输入可用 CDK，或填写自己的 OpenAI 兼容 LLM 配置")
    requested_count = min(body.paperCount, cdk.paperCountMax if cdk else 200)
    topic = _clean_text(body.topic)
    if not topic:
        raise ApiError(400, "TOPIC_REQUIRED", "请输入检索主题")
    since_year = body.sinceYear or max(1900, datetime.now(BEIJING_TZ).year - 10)
    provider = body.literatureProvider or (cdk.literatureProvider if cdk and cdk.literatureProvider else config.literatureProvider)
    if provider not in {"sciverse", "paper_search", "hybrid"}:
        provider = config.literatureProvider
    paper_sources = _normalize_paper_search_sources(
        body.paperSearchSources or (cdk.paperSearchSources if cdk and cdk.paperSearchSources else config.paperSearchSources)
    )
    llm = _llm_from_literature_search_request(body, config)
    if progress:
        progress("准备", "正在校验 CDK、模型与检索配置", 1, 0, requested_count, "indeterminate", "本地配置校验")
    extra_queries = await _expand_search_queries_with_clean_prompt(llm, topic, progress=progress)
    fields = [
        "title", "doi", "author", "publication_published_year", "publication_published_date",
        "publication_venue_name_unified", "citation_count", "influential_citation_count", "fwci",
        "doc_id", "unique_id", "abstract", "access_oa_url",
    ]
    if progress:
        progress("文献检索", f"正在检索并筛选 {requested_count} 篇文献", 8, 0, requested_count, "determinate", "按已筛选文献数量计算")
    papers, _meta, search_expression = await _collect_papers(
        _sciverse_for_literature_search(request, app, config),
        topic,
        requested_count,
        since_year,
        "STRONG",
        fields,
        provider=provider,
        paper_search_sources=paper_sources,
        extra_queries=extra_queries,
        progress=progress,
    )
    if progress:
        progress("去重评分", "正在按 DOI、标题和 uniqueId 去重并计算质量分", 72, len(papers), requested_count, "indeterminate", "本地去重与排序")
    dynamic_terms = _dynamic_topic_terms(topic, extra_queries)
    papers = _rank_quality_papers(_dedupe(papers), allow_weak=True, topic=topic, dynamic_terms=dynamic_terms)[:requested_count]
    if settings.paper_search_validate_links:
        papers = await _validate_paper_links(papers, progress=progress)
    _annotate_evidence_scores(papers, topic, dynamic_terms, {})
    _consume_literature_cdk(config, cdk.id if cdk and not has_own_llm else None)
    fresh_cdk = _find_valid_literature_cdk(_load_config(), body.cdk or "") if cdk else None
    created_at = datetime.now(timezone.utc).isoformat()
    if progress:
        progress("完成", f"已返回 {len(papers)} 篇文献", 100, len(papers), requested_count, "determinate", "结果已持久化")
    return LiteratureOnlySearchResult(
        ok=bool(papers),
        searchId=search_id,
        sharePath=f"/literature-search/{search_id}" if search_id else None,
        createdAt=created_at,
        topic=topic,
        requested=requested_count,
        returned=len(papers),
        sinceYear=since_year,
        literatureProvider=provider,
        paperSearchSources=paper_sources,
        llmSearchQueries=extra_queries,
        searchExpression=search_expression,
        cdkId=cdk.id if cdk else None,
        cdkName=cdk.name if cdk else None,
        cdk=_literature_cdk_public_info(fresh_cdk or cdk),
        papers=papers,
    )


@router.post("/literature-search", response_model=LiteratureOnlySearchResult)
async def search_literature_only(body: LiteratureOnlySearchRequest, request: Request):
    _check_literature_search_rate_limit(request)
    search_id = _new_literature_search_id(body.topic)
    result = await _execute_literature_search(body, request.app, request=request, search_id=search_id)
    _save_literature_search_payload(search_id, result=result.model_dump())
    return result


@router.post("/literature-search/async", response_model=LiteratureSearchAccepted)
async def search_literature_only_async(body: LiteratureOnlySearchRequest, request: Request):
    _check_literature_search_rate_limit(request)
    app = request.app
    search_id = _new_literature_search_id(body.topic)
    progress_item = _set_literature_search_progress(
        search_id,
        status="queued",
        stage="排队",
        message="检索任务已创建，正在进入多源检索流程",
        percent=0,
        current=0,
        total=body.paperCount,
        mode="indeterminate",
        detail="任务状态会自动刷新",
    )

    def progress(
        stage: str,
        message: str,
        percent: int,
        current: int = 0,
        total: int | None = None,
        mode: Literal["determinate", "indeterminate"] = "determinate",
        detail: str | None = None,
    ) -> None:
        _set_literature_search_progress(
            search_id,
            status="running",
            stage=stage,
            message=message,
            percent=percent,
            current=current,
            total=total,
            mode=mode,
            detail=detail,
        )

    async def runner() -> None:
        try:
            result = await _execute_literature_search(body, app, request=None, search_id=search_id, progress=progress)
            _save_literature_search_payload(search_id, result=result.model_dump())
            _set_literature_search_progress(
                search_id,
                status="success",
                stage="完成",
                message=f"已完成 {result.returned} 篇文献检索并持久化保存",
                percent=100,
                current=result.returned,
                total=result.requested,
                mode="determinate",
                detail="可复制当前路径分享或稍后再次查看",
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("Literature search task failed: %s", search_id)
            message = exc.message if isinstance(exc, ApiError) else str(exc)
            _set_literature_search_progress(
                search_id,
                status="error",
                stage="失败",
                message=message,
                percent=0,
                current=0,
                total=body.paperCount,
                mode="indeterminate",
                detail="请检查 CDK、LLM 配置或检索源状态后重试",
                error=message,
            )
        finally:
            _LITERATURE_SEARCH_TASKS.pop(search_id, None)

    task = asyncio.create_task(runner())
    _LITERATURE_SEARCH_TASKS[search_id] = task
    return LiteratureSearchAccepted(accepted=True, searchId=search_id, sharePath=f"/literature-search/{search_id}", progress=progress_item)


@router.get("/literature-search/progress/{search_id}", response_model=LiteratureSearchProgressResult)
async def get_literature_search_progress(search_id: str):
    progress = _LITERATURE_SEARCH_PROGRESS.get(search_id)
    if not progress:
        payload = _load_literature_search_payload(search_id)
        progress = payload.get("progress") if payload else None
    if not progress:
        raise ApiError(404, "LITERATURE_SEARCH_NOT_FOUND", "检索记录不存在")
    return LiteratureSearchProgressResult(progress=LiteratureSearchProgressItem(**progress))


@router.get("/literature-search/results/{search_id}", response_model=LiteratureSearchStoredResult)
async def get_literature_search_result(search_id: str):
    payload = _load_literature_search_payload(search_id)
    if not payload:
        raise ApiError(404, "LITERATURE_SEARCH_NOT_FOUND", "检索记录不存在")
    result = payload.get("result")
    progress = payload.get("progress")
    return LiteratureSearchStoredResult(
        result=LiteratureOnlySearchResult(**result) if isinstance(result, dict) else None,
        progress=LiteratureSearchProgressItem(**progress) if isinstance(progress, dict) else None,
    )


@router.get("/admin/literature-searches", response_model=LiteratureSearchListResult)
async def get_admin_literature_searches(request: Request, cdkId: str | None = None, limit: int = 300):
    _require_admin(request)
    limit = max(1, min(limit, 1000))
    rows = _iter_literature_search_summaries()
    if cdkId:
        rows = [item for item in rows if str(item.get("cdkId") or "") == cdkId]
    return LiteratureSearchListResult(items=[LiteratureSearchSummary(**item) for item in rows[:limit]])


@router.post("/pdf", response_model=PdfResolveResult)
async def resolve_open_pdf(body: PdfResolveRequest, request: Request):
    _check_pdf_resolve_rate_limit(request)
    config = _load_config()
    paper = _paper_from_pdf_request(body, request, config)
    if not paper:
        raise ApiError(400, "PAPER_REQUIRED", "请提供 runId+paperId，或 DOI/标题/URL 用于查询开放 PDF")
    return await _resolve_open_pdf_for_paper(paper)


@router.get("/assets/images/{filename}")
async def get_image_asset(filename: str, request: Request):
    if not re.match(r"^[A-Za-z0-9._-]+$", filename):
        raise ApiError(404, "ASSET_NOT_FOUND", "图片不存在")
    _require_asset_access(filename, request)
    path = _image_assets_dir() / filename
    if not path.exists() or not path.is_file():
        raise ApiError(404, "ASSET_NOT_FOUND", "图片不存在")
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(path.suffix.lower())
    if not media_type:
        raise ApiError(404, "ASSET_NOT_FOUND", "图片不存在")
    return FileResponse(path, media_type=media_type)


@router.get("/assets/pdfs/{filename}")
async def get_pdf_asset(filename: str, request: Request):
    if not re.match(r"^[a-f0-9]{24}-[a-f0-9]{16}\.pdf$", filename):
        raise ApiError(404, "PDF_NOT_FOUND", "PDF 不存在")
    _require_asset_access(filename, request)
    path = _pdf_assets_dir() / filename
    if not path.exists() or not path.is_file():
        raise ApiError(404, "PDF_NOT_FOUND", "PDF 不存在")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=filename,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.post("/test/sciverse", response_model=ConnectionTestResult)
async def test_sciverse_connection(body: DailyReviewConfig, request: Request):
    _require_admin(request)
    config = _with_resolved_secrets(body)
    sciverse = _sciverse_from_config(request, config)
    if not sciverse.enabled:
        return ConnectionTestResult(ok=False, service="sciverse", message="Sciverse Token 未配置")
    ok = await sciverse.health()
    return ConnectionTestResult(
        ok=ok,
        service="sciverse",
        message="Sciverse 连接成功" if ok else "Sciverse 请求失败或 Token 无效",
        detail="已真实请求 /meta-catalog 并收到成功响应" if ok else None,
    )


@router.post("/test/paper-search", response_model=ConnectionTestResult)
async def test_paper_search_connection(body: DailyReviewConfig, request: Request):
    _require_admin(request)
    config = _with_resolved_secrets(body)
    topic = (config.topic or _topic_for_request(config).topic).strip()
    sources = _normalize_paper_search_sources(config.paperSearchSources)
    timeout_seconds = min(float(settings.request_timeout), 12.0)
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        searcher = PaperSearchClient(client)

        async def probe(source: str) -> tuple[str, list[dict[str, Any]], str | None]:
            try:
                batch = await asyncio.wait_for(
                    searcher.search_source(source, _search_query(topic), limit=10, since_year=config.sinceYear),
                    timeout=timeout_seconds + 3,
                )
                return source, batch, None
            except Exception as exc:  # noqa: BLE001
                return source, [], str(exc) or exc.__class__.__name__

        results = await asyncio.gather(*(probe(source) for source in sources))
    raw = [paper for _, batch, _ in results for paper in batch]
    papers = _rank_quality_papers(_dedupe([_normalize_paper(paper) for paper in raw if paper.get("title")]), allow_weak=True, topic=topic)
    available = [f"{source}={len(batch)}" for source, batch, _ in results if batch]
    failed = [f"{source}:{error}" for source, batch, error in results if not batch and error]
    return ConnectionTestResult(
        ok=bool(papers),
        service="paper_search",
        message="Paper Search 连接成功" if papers else "Paper Search 未返回可用文献",
        detail=f"可用源 {'; '.join(available) or '无'}；候选 {len(papers)} 篇；失败 {'; '.join(failed[:4]) or '无'}",
    )


@router.post("/test/llm", response_model=ConnectionTestResult)
async def test_llm_connection(body: DailyReviewConfig, request: Request):
    _require_admin(request)
    config = _with_resolved_secrets(body)
    if not config.llm.apiKey.strip():
        return ConnectionTestResult(ok=False, service="llm", message="LLM API Key 未配置")
    try:
        text = await asyncio.wait_for(
            _llm_from_admin_config(config).complete(
                [{"role": "system", "content": "You are a connectivity checker. Reply with exactly: OK"}, {"role": "user", "content": "OK"}],
                temperature=0,
                max_tokens=16,
            ),
            timeout=35.0,
        )
    except asyncio.TimeoutError:
        return ConnectionTestResult(ok=False, service="llm", message="LLM request timeout", detail="LLM connectivity test did not finish within 35 seconds")
    except Exception as exc:  # noqa: BLE001
        return ConnectionTestResult(ok=False, service="llm", message="LLM 请求失败", detail=str(exc))
    return ConnectionTestResult(
        ok=bool(text.strip()),
        service="llm",
        message="LLM 连接成功" if text.strip() else "LLM 返回空内容",
        detail=f"模型 {config.llm.model} 已返回: {text[:80]}" if text.strip() else None,
    )


@router.post("/test/translation", response_model=ConnectionTestResult)
async def test_translation_connection(body: DailyReviewConfig, request: Request):
    _require_admin(request)
    config = _with_resolved_secrets(body)
    if not (config.translation.apiKey or config.llm.apiKey).strip():
        return ConnectionTestResult(ok=False, service="llm", message="翻译 LLM 未配置", detail="未配置时请直接使用浏览器翻译。")
    try:
        text = await asyncio.wait_for(
            _translation_llm_from_config(config).complete(
                [{"role": "system", "content": "You are a translation connectivity checker. Translate the phrase to Chinese only."}, {"role": "user", "content": "structural design"}],
                temperature=0,
                max_tokens=32,
            ),
            timeout=35.0,
        )
    except asyncio.TimeoutError:
        return ConnectionTestResult(ok=False, service="llm", message="Translation LLM request timeout", detail="Translation connectivity test did not finish within 35 seconds")
    except Exception as exc:  # noqa: BLE001
        return ConnectionTestResult(ok=False, service="llm", message="翻译 LLM 请求失败", detail=str(exc))
    return ConnectionTestResult(
        ok=bool(text.strip()),
        service="llm",
        message="翻译 LLM 连接成功" if text.strip() else "翻译 LLM 返回空内容",
        detail=f"模型 {config.translation.model} 已返回: {text[:80]}" if text.strip() else None,
    )


@router.post("/test/image", response_model=ConnectionTestResult)
async def test_image_connection(body: DailyReviewConfig, request: Request):
    _require_admin(request)
    return await _test_image_generation(_with_resolved_secrets(body))


@router.post("/test/wechat", response_model=ConnectionTestResult)
async def test_wechat_connection(body: DailyReviewConfig, request: Request):
    _require_admin(request)
    config = _with_resolved_secrets(body)
    try:
        await _wechat_token(config, force=True)
        quota = await _wechat_api_post(config, "/cgi-bin/openapi/quota/get", {"cgi_path": "/cgi-bin/draft/add"})
    except ApiError as exc:
        return ConnectionTestResult(ok=False, service="wechat", message="公众号连接失败", detail=exc.message)
    except Exception as exc:  # noqa: BLE001
        return ConnectionTestResult(ok=False, service="wechat", message="公众号连接失败", detail=str(exc))
    detail = quota.get("quota") if isinstance(quota.get("quota"), dict) else quota
    return ConnectionTestResult(ok=True, service="wechat", message="公众号连接成功", detail=f"stable_token 可用；draft/add 额度信息：{detail}")


@router.post("/wechat/article", response_model=WechatArticleResult)
async def build_wechat_article(body: WechatDraftRequest, request: Request):
    _require_admin(request)
    config = _load_config()
    run = _find_run(body.runId)
    if run is None:
        raise ApiError(404, "RUN_NOT_FOUND", "未找到该期日报")
    return _build_wechat_article(config, run, body.title, body.digest)


@router.post("/wechat/draft", response_model=WechatArticleResult)
async def create_wechat_draft(body: WechatDraftRequest, request: Request):
    _require_admin(request)
    config = _load_config()
    _wechat_require_config(config)
    run = _find_run(body.runId)
    if run is None:
        raise ApiError(404, "RUN_NOT_FOUND", "未找到该期日报")
    article = _build_wechat_article(config, run, body.title, body.digest)
    content_html = body.contentHtml.strip() if body.contentHtml and body.contentHtml.strip() else article.contentHtml
    content_html, uploaded_content_images, content_image_errors = await _wechat_prepare_content_images(config, content_html)
    cover_url = (body.coverUrl or article.coverUrl or "").strip()
    thumb_media_id = await _wechat_upload_image_material(config, cover_url)
    payload = {
        "articles": [
            {
                "title": article.title,
                "author": _wechat_trim_text(config.wechat.author or "研域前沿综述", 32),
                "digest": article.digest,
                "content": content_html,
                "content_source_url": article.sourceUrl,
                "thumb_media_id": thumb_media_id,
                "need_open_comment": 0,
                "only_fans_can_comment": 0,
            }
        ]
    }
    data = await _wechat_api_post(config, "/cgi-bin/draft/add", payload)
    media_id = str(data.get("media_id") or "")
    if not media_id:
        raise ApiError(502, "WECHAT_DRAFT_FAILED", str(data)[:500])
    updated = dict(run)
    updated["wechat"] = {
        "status": "draft_created",
        "draftMediaId": media_id,
        "thumbMediaId": thumb_media_id,
        "contentImageUploaded": uploaded_content_images,
        "contentImageErrors": content_image_errors[:5],
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "title": article.title,
        "sourceUrl": article.sourceUrl,
        "message": "草稿已创建，请前往公众号后台发布。" if not content_image_errors else f"草稿已创建，正文图片上传 {uploaded_content_images} 张，{len(content_image_errors)} 张失败。",
    }
    _replace_run(updated)
    article.draftMediaId = media_id
    article.status = "draft_created"
    article.contentHtml = content_html
    article.message = updated["wechat"]["message"]
    return article


@router.get("/progress", response_model=DailyReviewProgressResult)
async def get_progress(request: Request):
    _require_admin(request)
    return DailyReviewProgressResult(items=_progress_items(_load_config()))


@router.get("/drafts", response_model=DailyReviewDraftsResult)
async def get_drafts(request: Request):
    _require_admin(request)
    return DailyReviewDraftsResult(items=_list_drafts(_load_config()))


@router.post("/drafts/resume", response_model=DailyReviewRunAccepted)
async def resume_draft(body: DailyReviewResumeRequest, request: Request):
    _require_admin(request)
    config = _load_config()
    draft = _load_draft(body.draftId)
    topic = _topic_for_request(config, str(draft.get("topicId") or None), str(draft.get("topicSlug") or None))
    if str(draft.get("topicId") or "") != topic.id:
        raise ApiError(404, "DRAFT_TOPIC_NOT_FOUND", "暂存任务对应主题不存在")
    if not draft.get("canResume"):
        raise ApiError(400, "DRAFT_NOT_RESUMABLE", "该暂存任务当前不可继续")
    existing = _RUN_TASKS.get(topic.id)
    if existing and not existing.done():
        return DailyReviewRunAccepted(accepted=False, topicId=topic.id, progress=_progress_item_for_topic(config, topic.id))

    _set_run_progress(
        topic,
        status="running",
        stage="继续生成",
        message=f"正在从暂存任务 {body.draftId} 继续生成",
        percent=70,
        current=len(draft.get("papers") or []),
        total=(draft.get("query") or {}).get("paperCount") or topic.paperCount,
        mode="indeterminate",
        detail="复用已暂存的文献证据，不重新落库",
        draft_id=body.draftId,
        draft_stage=str(draft.get("stage") or ""),
        draft_can_resume=False,
    )
    llm = _llm_from_config(request, config)

    def progress(
        stage: str,
        message: str,
        percent: int,
        current: int = 0,
        total: int | None = None,
        mode: Literal["determinate", "indeterminate"] = "determinate",
        detail: str | None = None,
    ) -> None:
        _set_run_progress(topic, status="running", stage=stage, message=message, percent=percent, current=current, total=total, mode=mode, detail=detail, draft_id=body.draftId, draft_stage=str(draft.get("stage") or ""), draft_can_resume=False)

    async def runner() -> None:
        try:
            result = await _finalize_review_run(config, topic, draft, llm, progress=progress)
            papers = result.get("papers") or []
            delta = result.get("dailyDelta") or {}
            _set_run_progress(
                topic,
                status="success",
                stage="完成",
                message=f"已从暂存任务生成 {len(papers)} 篇证据的{delta.get('modeLabel', '日报')}",
                percent=100,
                current=len(papers),
                total=(draft.get("query") or {}).get("paperCount") or len(papers),
                mode="determinate",
                detail="已完成并持久化保存",
                run_id=result.get("runId"),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("Daily review draft resume failed for topic %s", topic.id)
            _set_run_progress(
                topic,
                status="error",
                stage="继续失败",
                message=str(getattr(exc, "message", exc)),
                percent=100,
                mode="determinate",
                detail="暂存任务仍保留，可调整配置后再次继续",
                error=str(getattr(exc, "message", exc)),
                draft_id=body.draftId,
                draft_stage=str((draft.get("stage") or "")),
                draft_can_resume=True,
            )
        finally:
            current = _RUN_TASKS.get(topic.id)
            if current is task:
                _RUN_TASKS.pop(topic.id, None)

    task = asyncio.create_task(runner())
    _RUN_TASKS[topic.id] = task
    return DailyReviewRunAccepted(accepted=True, topicId=topic.id, progress=_progress_item_for_topic(config, topic.id))


@router.post("/translate", response_model=TranslateResult)
async def translate_text(body: TranslateRequest, request: Request):
    _check_translation_rate_limit(request)
    config = _load_config()
    if not (config.translation.apiKey or config.llm.apiKey).strip():
        raise ApiError(400, "TRANSLATION_NOT_CONFIGURED", "翻译模型未配置，请直接使用浏览器翻译。")
    try:
        translated = await _translation_llm_from_config(config).complete(
            [
                {"role": "system", "content": "你是严谨的学术翻译助手。保留专有名词、DOI、编号和技术缩写。"},
                {"role": "user", "content": f"目标语言: {body.targetLanguage}\n上下文: {body.context or '文献证据'}\n请只输出译文，不要解释。\n\n{body.text}"},
            ],
            temperature=0,
            max_tokens=min(6000, max(1000, len(body.text) * 3)),
        )
    except Exception as exc:  # noqa: BLE001
        raise ApiError(502, "TRANSLATION_FAILED", str(exc))
    return TranslateResult(translatedText=translated, model=config.translation.model)


async def _write_review(
    config: DailyReviewConfig,
    topic: str,
    papers: list[dict[str, Any]],
    texts: dict[str, str],
    include_web: bool,
    llm: Any,
    daily_delta: dict[str, Any] | None = None,
    progress: ProgressCallback | None = None,
) -> str:
    if len(papers) <= 80:
        if progress:
            progress("综述写作", f"LLM 正在基于 {len(papers)} 篇证据撰写完整综述", 78, len(papers), len(papers), "indeterminate", "模型内部生成进度不可见，等待接口返回")
        review = await llm.complete(_review_prompt(topic, papers, texts, include_web, daily_delta), temperature=config.llm.temperature, max_tokens=config.llm.maxTokens)
        if progress:
            progress("综述整理", "LLM 综述正文已返回，正在补齐证据章节", 90, len(papers), len(papers), "indeterminate", "正在做本地结构补齐")
        return _ensure_daily_delta_section(_ensure_review_sections(review, papers, texts), daily_delta)

    summaries: list[str] = []
    chunk_size = 80
    total_chunks = (len(papers) + chunk_size - 1) // chunk_size
    for offset in range(0, len(papers), chunk_size):
        chunk_index = offset // chunk_size + 1
        if progress:
            progress("分批归纳", f"LLM 正在归纳第 {chunk_index}/{total_chunks} 批文献", 68 + int(chunk_index / max(1, total_chunks) * 18), chunk_index, total_chunks, "determinate", "按批次数计算，单批内部生成进度不可见")
        summary = await llm.complete(
            _chunk_prompt(topic, papers[offset:offset + chunk_size], texts, offset),
            temperature=config.llm.temperature,
            max_tokens=min(8000, max(3000, config.llm.maxTokens // 4)),
        )
        summaries.append(summary)
    if progress:
        progress("总综述生成", f"LLM 正在综合 {total_chunks} 个批次归纳生成完整综述", 90, total_chunks, total_chunks, "indeterminate", "模型内部生成进度不可见，等待接口返回")
    review = await llm.complete(
        _final_from_summaries_prompt(topic, papers, texts, include_web, summaries, daily_delta),
        temperature=config.llm.temperature,
        max_tokens=config.llm.maxTokens,
    )
    return _ensure_daily_delta_section(_ensure_review_sections(review, papers, texts), daily_delta)


async def _finalize_review_run(
    config: DailyReviewConfig,
    topic_config: ReviewTopicConfig,
    draft: dict[str, Any],
    llm: Any,
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    topic = str(draft.get("topic") or topic_config.topic or "").strip()
    papers = [paper for paper in (draft.get("papers") or []) if isinstance(paper, dict)]
    texts = draft.get("texts") if isinstance(draft.get("texts"), dict) else {}
    daily_delta = draft.get("dailyDelta") if isinstance(draft.get("dailyDelta"), dict) else {}
    query = draft.get("query") if isinstance(draft.get("query"), dict) else {}
    include_web = bool(query.get("includeWeb", topic_config.includeWeb))
    if not topic or not papers:
        raise ApiError(400, "DRAFT_INCOMPLETE", "暂存任务缺少主题或文献证据，无法继续")

    draft["status"] = "running"
    draft["stage"] = "review"
    draft["canResume"] = False
    draft["error"] = None
    _save_draft(draft)
    try:
        review = await _write_review(config, topic, papers, texts, include_web, llm, daily_delta=daily_delta, progress=progress)
    except Exception as exc:  # noqa: BLE001
        draft["status"] = "failed"
        draft["stage"] = "review_failed"
        draft["canResume"] = True
        draft["error"] = str(exc)
        _save_draft(draft)
        raise ApiError(502, "LLM_FAILED", str(exc)) from exc

    draft["reviewMarkdown"] = review
    draft["stage"] = "image_prompt"
    _save_draft(draft)
    try:
        image_prompt = await _build_image_prompt(topic, review, papers, llm, progress=progress)
    except Exception as exc:  # noqa: BLE001
        draft["status"] = "failed"
        draft["stage"] = "image_prompt_failed"
        draft["canResume"] = True
        draft["error"] = str(exc)
        _save_draft(draft)
        raise ApiError(502, "IMAGE_PROMPT_FAILED", str(exc)) from exc

    draft["imagePrompt"] = image_prompt
    draft["stage"] = "image"
    _save_draft(draft)
    if progress:
        progress("一图看懂", "正在根据专用生图提示词生成一图看懂内容", 95, len(papers), len(papers), "indeterminate", "等待生图接口返回或生成本地占位图")
    try:
        image = await _generate_image(config, image_prompt)
    except Exception as exc:  # noqa: BLE001
        draft["status"] = "failed"
        draft["stage"] = "image_failed"
        draft["canResume"] = True
        draft["error"] = str(exc)
        _save_draft(draft)
        raise ApiError(502, "IMAGE_GENERATION_FAILED", str(exc)) from exc

    run_id = str(draft.get("runId") or hashlib.sha256(f"{topic}{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()[:12])
    result = {
        "runId": run_id,
        "topicId": topic_config.id,
        "topicSlug": topic_config.slug,
        "topicName": topic_config.name,
        "topic": topic,
        "subtitle": daily_delta.get("subtitle"),
        "dailyDelta": daily_delta,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "papers": papers,
        "fullTextFetched": len(texts),
        "reviewMarkdown": review,
        "image": image,
        "sciverseTotal": draft.get("sciverseTotal"),
    }
    _persist_daily_delta(topic_config, papers, run_id, daily_delta)
    _append_run(result)
    draft["status"] = "completed"
    draft["stage"] = "completed"
    draft["canResume"] = False
    draft["runId"] = run_id
    draft["result"] = {"runId": run_id, "createdAt": result["createdAt"]}
    _save_draft(draft)
    _delete_draft(str(draft["draftId"]))
    return result


async def _run_review(config: DailyReviewConfig, body: DailyReviewRunRequest, sciverse: SciverseClient, llm: Any) -> dict[str, Any]:
    topic_config = _topic_for_request(config, body.topicId, body.topicSlug)
    topic = (body.topic or topic_config.topic or config.topic).strip()
    draft_id = hashlib.sha256(f"draft:{topic_config.id}:{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()[:12]
    draft: dict[str, Any] = {
        "draftId": draft_id,
        "topicId": topic_config.id,
        "topicSlug": topic_config.slug,
        "topicName": topic_config.name,
        "topic": topic,
        "status": "running",
        "stage": "init",
        "canResume": False,
    }

    def progress(
        stage: str,
        message: str,
        percent: int,
        current: int = 0,
        total: int | None = None,
        mode: Literal["determinate", "indeterminate"] = "determinate",
        detail: str | None = None,
    ) -> None:
        _set_run_progress(
            topic_config,
            status="running",
            stage=stage,
            message=message,
            percent=percent,
            current=current,
            total=total,
            mode=mode,
            detail=detail,
            draft_id=draft_id,
            draft_stage=str(draft.get("stage") or ""),
            draft_can_resume=bool(draft.get("canResume")),
        )

    try:
        progress("准备", "正在校验主题、模型与检索配置", 1, mode="indeterminate", detail="本地配置校验")
        if not topic:
            raise ApiError(400, "VALIDATION_ERROR", "主题不能为空")
        if _looks_mojibake(topic):
            raise ApiError(400, "TOPIC_ENCODING_INVALID", "主题文本包含乱码问号，请在管理员后台重新保存正确的 UTF-8 主题。")
        count = body.paperCount or topic_config.paperCount
        since_year = body.sinceYear or topic_config.sinceYear
        freshness = body.freshnessBoost or topic_config.freshnessBoost
        include_fulltext = topic_config.includeFullText if body.includeFullText is None else body.includeFullText
        include_web = topic_config.includeWeb if body.includeWeb is None else body.includeWeb
        literature_provider = config.literatureProvider
        draft.update({
            "stage": "validated",
            "query": {
                "paperCount": count,
                "sinceYear": since_year,
                "freshnessBoost": freshness,
                "includeFullText": include_fulltext,
                "includeWeb": include_web,
                "literatureProvider": literature_provider,
                "paperSearchSources": _normalize_paper_search_sources(config.paperSearchSources),
                "qualityFiltered": True,
            },
        })
        _save_draft(draft)

        if literature_provider == "sciverse" and not sciverse.enabled:
            raise ApiError(400, "SCIVERSE_NOT_CONFIGURED", "请先在后台配置 Sciverse API Token")

        fields = [
            "title", "doi", "author", "publication_published_year", "publication_published_date",
            "publication_venue_name_unified", "citation_count", "influential_citation_count", "fwci",
            "doc_id", "unique_id", "abstract", "access_oa_url",
        ]
        extra_queries = await _expand_search_queries_with_clean_prompt(llm, topic, progress=progress)
        draft["query"]["llmSearchQueries"] = extra_queries
        draft["stage"] = "search_plan"
        _save_draft(draft)
        progress("文献检索", f"正在检索并筛选 {count} 篇高质量文献", 5, 0, count, "determinate", "按筛选出的高质量证据数量计算")
        papers, meta, search_expression = await _collect_papers(
            sciverse,
            topic,
            count,
            since_year,
            freshness,
            fields,
            progress=progress,
            provider=literature_provider,
            paper_search_sources=config.paperSearchSources,
            extra_queries=extra_queries,
        )
        if not papers:
            raise ApiError(404, "NO_PAPERS", "未返回可用文献")
        draft["stage"] = "papers_collected"
        draft["papers"] = papers
        draft["sciverseTotal"] = (meta or {}).get("total_count")
        draft["query"]["searchExpression"] = search_expression
        _save_draft(draft)
        dynamic_terms = _dynamic_topic_terms(topic, extra_queries)

        progress("证据增强", f"正在补充摘要、检索片段与 docId，当前 {len(papers)} 篇", 48, len(papers), count, "indeterminate", "等待 Sciverse 证据补全接口返回")
        if sciverse.enabled:
            await _enrich_with_agentic_evidence(sciverse, topic, papers)
            await _enrich_with_sciverse_doc_ids(sciverse, papers)
            draft["stage"] = "evidence_enriched"
            draft["papers"] = papers
            _save_draft(draft)
        papers = _rank_quality_papers(papers, allow_weak=True, topic=topic, dynamic_terms=dynamic_terms)[:count]
        papers = _rank_quality_papers(
            await _validate_paper_links(papers, progress=progress),
            allow_weak=True,
            topic=topic,
            dynamic_terms=dynamic_terms,
        )[:count]
        draft["stage"] = "quality_filtered"
        draft["papers"] = papers
        _save_draft(draft)
        expected_floor = min(count, int((meta or {}).get("total_count") or count), 30)
        if len(papers) < expected_floor:
            raise ApiError(404, "LOW_QUALITY_PAPERS", f"高质量文献证据不足，仅筛选到 {len(papers)} 篇。请收窄或调整主题。")

        progress("PDF 预缓存", f"正在为 {len(papers)} 篇高质量证据下载可开放获取 PDF", 58, 0, len(papers), "determinate", "只缓存合法开放 PDF，任一来源成功后停止尝试同篇文献其他来源")
        papers = await _prefetch_open_pdfs(papers, progress=progress)
        draft["stage"] = "pdf_prefetched"
        draft["papers"] = papers
        _save_draft(draft)

        texts = await _fetch_texts(sciverse, papers, progress=progress) if include_fulltext and sciverse.enabled else {}
        draft["stage"] = "texts_fetched"
        draft["texts"] = texts
        _save_draft(draft)
        if include_fulltext and not sciverse.enabled:
            progress("全文片段", "当前检索源不提供 Sciverse docId，使用摘要/元数据证据", 65, len(papers), len(papers), "determinate", "Paper Search 暂不读取全文片段")
        elif not include_fulltext:
            progress("全文片段", "当前主题未启用全文片段读取，使用摘要/元数据证据", 65, len(papers), len(papers), "determinate", "跳过全文读取")
        for paper in papers:
            paper["evidenceSource"] = "fulltext" if texts.get(paper["id"]) else "abstract"
        _annotate_evidence_scores(papers, topic, dynamic_terms, texts)
        daily_delta = _annotate_daily_delta(papers, topic_config, count)
        daily_delta["subtitle"] = await _generate_daily_subtitle(llm, topic_config, topic, papers, daily_delta, progress=progress)
        daily_delta["modeLabel"] = _mode_label(str(daily_delta.get("mode") or ""))
        draft.update({
            "stage": "ready_to_finalize",
            "status": "ready",
            "canResume": True,
            "papers": papers,
            "texts": texts,
            "dailyDelta": daily_delta,
            "dynamicTerms": dynamic_terms,
        })
        _save_draft(draft)

        result = await _finalize_review_run(config, topic_config, draft, llm, progress=progress)
        _set_run_progress(
            topic_config,
            status="success",
            stage="完成",
            message=f"已生成 {len(papers)} 篇证据的{daily_delta.get('modeLabel', '日报')}，新增 {daily_delta.get('newEvidenceCount', 0)} 篇，全文片段 {len(texts)} 篇",
            percent=100,
            current=len(papers),
            total=count,
            mode="determinate",
            detail="已完成并持久化保存",
            run_id=result["runId"],
            draft_id=None,
            draft_stage=None,
            draft_can_resume=False,
        )
        return result
    except Exception as exc:
        draft["status"] = "failed"
        draft["error"] = str(getattr(exc, "message", exc))
        draft["canResume"] = draft.get("stage") in {"ready_to_finalize", "review_failed", "image_prompt_failed", "image_failed"} or bool(draft.get("papers"))
        _save_draft(draft)
        _set_run_progress(
            topic_config,
            status="error",
            stage="失败",
            message=str(getattr(exc, "message", exc)),
            percent=100,
            mode="determinate",
            detail="已保存暂存任务，可在后台调整后继续" if draft.get("canResume") else "任务失败，查看错误信息",
            error=str(getattr(exc, "message", exc)),
            draft_id=draft_id,
            draft_stage=str(draft.get("stage") or ""),
            draft_can_resume=bool(draft.get("canResume")),
        )
        raise


@router.post("/run")
async def run_daily_review(body: DailyReviewRunRequest, request: Request):
    _require_admin(request)
    config = _load_config()
    return await _run_review(config, body, _sciverse_from_config(request, config), _llm_from_config(request, config))


@router.post("/run-async", response_model=DailyReviewRunAccepted)
async def run_daily_review_async(body: DailyReviewRunRequest, request: Request):
    _require_admin(request)
    config = _load_config()
    topic = _topic_for_request(config, body.topicId, body.topicSlug)
    existing = _RUN_TASKS.get(topic.id)
    if existing and not existing.done():
        return DailyReviewRunAccepted(
            accepted=False,
            topicId=topic.id,
            progress=_progress_item_for_topic(config, topic.id),
        )

    _set_run_progress(
        topic,
        status="running",
        stage="排队",
        message=f"已提交「{topic.name}」生成任务，正在启动检索",
        percent=1,
        current=0,
        total=body.paperCount or topic.paperCount,
        mode="indeterminate",
        detail="后台任务已创建，进度将自动刷新",
    )
    sciverse = _sciverse_from_config(request, config)
    llm = _llm_from_config(request, config)

    async def runner() -> None:
        try:
            await _run_review(config, body, sciverse, llm)
        except Exception:
            log.exception("Daily review async run failed for topic %s", topic.id)
        finally:
            current = _RUN_TASKS.get(topic.id)
            if current is task:
                _RUN_TASKS.pop(topic.id, None)

    task = asyncio.create_task(runner())
    _RUN_TASKS[topic.id] = task
    return DailyReviewRunAccepted(
        accepted=True,
        topicId=topic.id,
        progress=_progress_item_for_topic(config, topic.id),
    )


@router.post("/smoke", response_model=SmokeResult)
async def smoke_search(body: SmokeRequest, request: Request):
    _require_admin(request)
    config = _load_config()
    topic_config = _topic_for_request(config, body.topicId, body.topicSlug)
    topic = (body.topic or topic_config.topic).strip()
    since_year = body.sinceYear or topic_config.sinceYear
    freshness = body.freshnessBoost or topic_config.freshnessBoost
    extra_queries = await _expand_search_queries_with_clean_prompt(_llm_from_config(request, config), topic)
    fields = [
        "title", "doi", "author", "publication_published_year", "publication_published_date",
        "publication_venue_name_unified", "citation_count", "influential_citation_count", "fwci",
        "doc_id", "unique_id", "abstract", "access_oa_url",
    ]
    papers, meta, search_expression = await _collect_papers(
        _sciverse_from_config(request, config),
        topic,
        body.paperCount,
        since_year,
        freshness,
        fields,
        provider=config.literatureProvider,
        paper_search_sources=config.paperSearchSources,
        extra_queries=extra_queries,
    )
    dynamic_terms = _dynamic_topic_terms(topic, extra_queries)
    year_counter: dict[str, int] = {}
    venue_counter: dict[str, int] = {}
    for paper in papers:
        year = str(int(paper.get("year") or 0)) if paper.get("year") else "n.d."
        venue = str(paper.get("venue") or "UNKNOWN")
        year_counter[year] = year_counter.get(year, 0) + 1
        venue_counter[venue] = venue_counter.get(venue, 0) + 1
    return SmokeResult(
        ok=len(papers) >= min(body.paperCount, 50),
        topic=topic,
        requested=body.paperCount,
        returned=len(papers),
        sinceYear=since_year,
        sciverseTotal=(meta or {}).get("total_count"),
        searchExpression=search_expression,
        withAbstractCount=sum(1 for p in papers if len(_clean_text(p.get("abstract"))) >= 80),
        withSnippetCount=sum(1 for p in papers if len(_clean_text(p.get("snippet"))) >= 80),
        strongDomainCount=sum(1 for p in papers if _relevance_score_100(p, topic, dynamic_terms) >= 60),
        yearCounts=sorted(year_counter.items(), key=lambda item: item[0], reverse=True)[:12],
        venueTop=sorted(venue_counter.items(), key=lambda item: item[1], reverse=True)[:12],
        sampleTitles=[str(p.get("title") or "") for p in papers[:12]],
    )


async def scheduler_loop(app: Any) -> None:
    """Run configured topics once per Beijing day at each topic scheduleTime."""
    last_runs: dict[str, date] = {}
    while True:
        try:
            config = _load_config()
            now = datetime.now(BEIJING_TZ)
            due_topics = [
                topic
                for topic in config.topics
                if topic.enabled and topic.scheduleEnabled and _is_due(now, topic.scheduleTime, last_runs.get(topic.id))
            ]
            if due_topics:
                concurrency = max(1, min(settings.daily_review_scheduler_concurrency, len(due_topics)))
                semaphore = asyncio.Semaphore(concurrency)

                async def run_topic(topic: ReviewTopicConfig) -> None:
                    async with semaphore:
                        log.info("daily review scheduler due: topic=%s", topic.topic)
                        topic_config = _run_config_for_topic(config, topic)
                        try:
                            await _run_review(
                                topic_config,
                                DailyReviewRunRequest(topicId=topic.id),
                                _sciverse_from_app(app, topic_config),
                                _llm_from_admin_config(topic_config),
                            )
                        except Exception:
                            log.exception("daily review scheduler topic failed: topic=%s", topic.id)
                        finally:
                            last_runs[topic.id] = now.date()

                await asyncio.gather(*(run_topic(topic) for topic in due_topics))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("daily review scheduler skipped run: %s", exc)
        await asyncio.sleep(60)


def _is_due(now: datetime, schedule_time: str, last_run: date | None) -> bool:
    match = re.match(r"^(\d{2}):(\d{2})$", schedule_time or "")
    if not match:
        return False
    hour, minute = int(match.group(1)), int(match.group(2))
    return last_run != now.date() and now.hour == hour and now.minute == minute
