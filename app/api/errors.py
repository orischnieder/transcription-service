import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.schemas import ErrorDetail, ErrorResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """A domain error that is safe to show to the user."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump())


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    level = logging.WARNING if exc.status_code >= 500 else logging.INFO
    logger.log(level, "request failed", extra={"code": exc.code, "path": request.url.path})
    return error_response(exc.status_code, exc.code, exc.message)


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    first = exc.errors()[0] if exc.errors() else {}
    location = ".".join(str(part) for part in first.get("loc", ()))
    message = f"Invalid request: {location} {first.get('msg', '')}".strip()
    return error_response(422, "invalid_request", message)


_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


async def _handle_http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _HTTP_CODES.get(exc.status_code, "http_error")
    return error_response(exc.status_code, code, str(exc.detail))


async def _handle_redis_error(request: Request, exc: Exception) -> JSONResponse:
    # Redis holds the queue and the job state; when it is unreachable (e.g. restarting), the
    # request can be retried, so this is a 503, not a 500.
    logger.warning("redis unavailable", extra={"path": request.url.path, "error": repr(exc)})
    return error_response(
        503,
        "queue_unavailable",
        "The service is temporarily unavailable. Please try again in a moment.",
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    logger.exception("unhandled error", extra={"request_id": request_id, "path": request.url.path})
    return error_response(
        500, "internal_error", "Something went wrong on our side. Please try again."
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_error)
    app.add_exception_handler(RedisError, _handle_redis_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
