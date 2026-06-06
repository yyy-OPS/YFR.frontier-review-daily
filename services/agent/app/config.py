"""运行配置, 从环境变量读取 (不引入 pydantic-settings 依赖, 保持轻量)。"""
import os
from pathlib import Path
from urllib.parse import urlparse

# 本地/测试: 加载 services/agent/.env (生产走 docker-compose env_file)。缺 python-dotenv 时跳过。
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ModuleNotFoundError:
    pass


def _validate_url(u: str) -> str:
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError(f"R_ANALYSIS_URL 非法 (需 http/https): {u!r}")
    return u


def _repo_root() -> Path:
    path = Path(__file__).resolve()
    return path.parents[3] if len(path.parents) > 3 else path.parents[1]


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


class Settings:
    r_analysis_url: str = _validate_url(os.environ.get("R_ANALYSIS_URL", "http://localhost:8001"))
    request_timeout: float = float(os.environ.get("R_REQUEST_TIMEOUT", "120"))
    # 数据接入 (OpenAlex 主题检索/参考文献反查 + 引用补全) 可达数十秒, 单独长超时
    ingest_timeout: float = float(os.environ.get("R_INGEST_TIMEOUT", "300"))
    health_timeout: float = float(os.environ.get("R_HEALTH_TIMEOUT", "5"))
    max_upload_bytes: int = int(os.environ.get("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
    cors_origins: list[str] = _env_list(
        "CORS_ORIGINS",
        "http://localhost:5175,http://127.0.0.1:5175",
    )
    allow_insecure_defaults: bool = _env_bool("FRONTIER_REVIEW_ALLOW_INSECURE_DEFAULTS", False)
    require_secure_config: bool = _env_bool("FRONTIER_REVIEW_REQUIRE_SECURE_CONFIG", False)
    enable_api_docs: bool = _env_bool("FRONTIER_REVIEW_ENABLE_DOCS", False)
    enable_daily_review_scheduler: bool = _env_bool("FRONTIER_REVIEW_ENABLE_SCHEDULER", True)
    daily_review_scheduler_concurrency: int = int(os.environ.get("DAILY_REVIEW_SCHEDULER_CONCURRENCY", "2"))
    admin_login_rate_limit_per_minute: int = int(os.environ.get("ADMIN_LOGIN_RATE_LIMIT_PER_MINUTE", "10"))
    translation_rate_limit_per_minute: int = int(os.environ.get("TRANSLATION_RATE_LIMIT_PER_MINUTE", "30"))
    max_cached_image_bytes: int = int(os.environ.get("MAX_CACHED_IMAGE_BYTES", str(10 * 1024 * 1024)))
    max_cached_pdf_bytes: int = int(os.environ.get("MAX_CACHED_PDF_BYTES", str(150 * 1024 * 1024)))
    # LLM (综述/AI 功能)。无 key 时回退到 FakeStreamClient (测试/本地)。
    deepseek_api_key: str = os.environ.get("DEEPSEEK_API_KEY", "")
    deepseek_base_url: str = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    # OpenAI 兼容 LLM（用户自定义 URL/key/model，优先级高于 DeepSeek）
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    openai_base_url: str = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_model: str = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    deepseek_model: str = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    review_records_limit: int = int(os.environ.get("REVIEW_RECORDS_LIMIT", "40"))
    # 三层领域数据层 (Library/Project/Corpus)
    database_url: str = os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://bibliocn@localhost/bibliocn")
    test_database_url: str = os.environ.get(
        "TEST_DATABASE_URL", "postgresql+asyncpg://bibliocn@localhost/bibliocn_test")
    # MinerU 全文摄取 (阶段5-1)
    ocr_token: str = os.environ.get("OCR_AUTHORIZATION_TOKEN", "")
    mineru_base_url: str = os.environ.get("MINERU_BASE_URL", "https://mineru.net/api/v4")
    # Sciverse 学术检索: token 仅从运行时环境读取, 不落盘。
    sciverse_api_token: str = os.environ.get("SCIVERSE_API_TOKEN", "")
    sciverse_base_url: str = os.environ.get("SCIVERSE_BASE_URL", "https://api.sciverse.space")
    daily_review_literature_provider: str = os.environ.get("DAILY_REVIEW_LITERATURE_PROVIDER", "hybrid")
    paper_search_sources: str = os.environ.get("PAPER_SEARCH_SOURCES", "semantic,openalex,crossref,europepmc,hal,base,core,unpaywall")
    semantic_scholar_api_key: str = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "") or os.environ.get("PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY", "")
    semantic_scholar_min_interval_seconds: float = float(os.environ.get("SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS", "1.15"))
    openalex_mailto: str = os.environ.get("OPENALEX_MAILTO", "") or os.environ.get("PAPER_SEARCH_MCP_UNPAYWALL_EMAIL", "")
    core_api_key: str = os.environ.get("CORE_API_KEY", "") or os.environ.get("PAPER_SEARCH_MCP_CORE_API_KEY", "")
    unpaywall_email: str = os.environ.get("UNPAYWALL_EMAIL", "") or os.environ.get("PAPER_SEARCH_MCP_UNPAYWALL_EMAIL", "")
    zenodo_access_token: str = os.environ.get("ZENODO_ACCESS_TOKEN", "") or os.environ.get("PAPER_SEARCH_MCP_ZENODO_ACCESS_TOKEN", "")
    google_scholar_proxy_url: str = os.environ.get("GOOGLE_SCHOLAR_PROXY_URL", "") or os.environ.get("PAPER_SEARCH_MCP_GOOGLE_SCHOLAR_PROXY_URL", "")
    acm_api_key: str = os.environ.get("ACM_API_KEY", "") or os.environ.get("PAPER_SEARCH_MCP_ACM_API_KEY", "")
    ieee_api_key: str = os.environ.get("IEEE_API_KEY", "") or os.environ.get("PAPER_SEARCH_MCP_IEEE_API_KEY", "")
    paper_search_validate_links: bool = os.environ.get("PAPER_SEARCH_VALIDATE_LINKS", "true").strip().lower() not in {"0", "false", "no", "off"}
    wechat_app_id: str = os.environ.get("WECHAT_APP_ID", "")
    wechat_app_secret: str = os.environ.get("WECHAT_APP_SECRET", "")
    # 全文 Markdown 存储根目录
    corpora_dir: str = os.environ.get("BIBLIOCN_CORPORA_DIR", "/tmp/bibliocn_corpora")
    frontier_review_data_dir: str = os.environ.get(
        "FRONTIER_REVIEW_DATA_DIR",
        str(_repo_root() / "data" / "frontier_review"),
    )


settings = Settings()
