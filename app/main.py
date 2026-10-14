import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from redis import Redis

from app.api import routes_health, routes_jobs
from app.api.errors import register_error_handlers
from app.config import Settings, get_settings
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)

REDIS_TIMEOUT_SEC = 2


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Short timeouts so /health fails fast instead of hanging when Redis is down.
        app.state.redis = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=REDIS_TIMEOUT_SEC,
            socket_timeout=REDIS_TIMEOUT_SEC,
        )
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        logger.info("api started", extra={"upload_dir": str(settings.upload_dir)})
        yield
        app.state.redis.close()
        logger.info("api stopped")

    app = FastAPI(title="Transcription Service", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    register_error_handlers(app)
    app.middleware("http")(log_requests)
    app.include_router(routes_health.router)
    app.include_router(routes_jobs.router)
    return app


async def log_requests(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get("X-Request-ID") or uuid4().hex
    request.state.request_id = request_id
    start = time.perf_counter()
    status = 500  # Stays 500 if call_next raises; the error handler builds the actual response.
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        # Docker probes /health every few seconds; keep successful probes out of INFO logs.
        quiet = request.url.path == "/health" and status < 400
        logger.log(
            logging.DEBUG if quiet else logging.INFO,
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": status,
                "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                "request_id": request_id,
            },
        )
