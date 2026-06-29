from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from .daily_review import router as daily_review_router
from .daily_review import scheduler_loop as daily_review_scheduler_loop
from .daily_review import validate_runtime_security
from .errors import ApiError
from .sciverse_client import SciverseClient

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_runtime_security()
    timeout = httpx.Timeout(settings.request_timeout, connect=10.0)
    limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
    client = httpx.AsyncClient(base_url=settings.sciverse_base_url.rstrip("/"), timeout=timeout, limits=limits)
    app.state.sciverse_client = SciverseClient(client)
    app.state.daily_review_scheduler_task = (
        asyncio.create_task(daily_review_scheduler_loop(app))
        if settings.enable_daily_review_scheduler
        else None
    )
    try:
        yield
    finally:
        if app.state.daily_review_scheduler_task:
            app.state.daily_review_scheduler_task.cancel()
        await client.aclose()


app = FastAPI(
    title="Frontier Review Studio API",
    lifespan=lifespan,
    docs_url="/docs" if settings.enable_api_docs else None,
    redoc_url="/redoc" if settings.enable_api_docs else None,
    openapi_url="/openapi.json" if settings.enable_api_docs else None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(daily_review_router)


@app.middleware("http")
async def request_log_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except asyncio.CancelledError:
        logging.getLogger("agent.http").warning(
            "request_cancelled request_id=%s method=%s path=%s elapsed_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            (time.perf_counter() - started) * 1000,
        )
        raise
    except Exception:
        logging.getLogger("agent.http").exception(
            "request_failed request_id=%s method=%s path=%s elapsed_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            (time.perf_counter() - started) * 1000,
        )
        raise
    response.headers["X-Request-ID"] = request_id
    logging.getLogger("agent.http").info(
        "request_completed request_id=%s method=%s path=%s status=%s elapsed_ms=%.1f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        (time.perf_counter() - started) * 1000,
    )
    return response


@app.exception_handler(ApiError)
async def api_error_handler(_request: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status_code, content=exc.body())


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "frontier-review"}
