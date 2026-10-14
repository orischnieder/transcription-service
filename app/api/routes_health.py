import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from redis import Redis
from redis.exceptions import RedisError

from app.api.deps import get_redis
from app.api.errors import AppError
from app.api.schemas import ErrorResponse, HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter()


# Sync `def` on purpose: the Redis client is blocking, so FastAPI runs this in its threadpool.
@router.get("/health", responses={503: {"model": ErrorResponse}})
def health(redis: Annotated[Redis, Depends(get_redis)]) -> HealthResponse:
    try:
        redis.ping()
    except RedisError as exc:
        logger.warning("redis unreachable", extra={"error": repr(exc)})
        raise AppError("redis_unavailable", "Redis is unreachable.", 503) from exc
    return HealthResponse(status="ok", redis="ok")
