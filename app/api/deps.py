from fastapi import Request
from redis import Redis


def get_redis(request: Request) -> Redis:
    """The Redis client created once at startup (see `app.main.lifespan`)."""
    redis: Redis = request.app.state.redis
    return redis
