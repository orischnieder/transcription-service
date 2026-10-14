from typing import Annotated

from fastapi import Depends, Request
from redis import Redis

from app.config import Settings
from app.jobs.store import JobStore
from app.services.media_probe import FfprobeMediaProbe, MediaProbe


def get_settings(request: Request) -> Settings:
    """The Settings the app was created with (see `app.main.create_app`)."""
    settings: Settings = request.app.state.settings
    return settings


def get_redis(request: Request) -> Redis:
    """The Redis client created once at startup (see `app.main.lifespan`)."""
    redis: Redis = request.app.state.redis
    return redis


SettingsDep = Annotated[Settings, Depends(get_settings)]
RedisDep = Annotated[Redis, Depends(get_redis)]


def get_job_store(redis: RedisDep, settings: SettingsDep) -> JobStore:
    return JobStore(redis, ttl_sec=settings.job_ttl_hours * 3600)


def get_media_probe() -> MediaProbe:
    return FfprobeMediaProbe()


JobStoreDep = Annotated[JobStore, Depends(get_job_store)]
MediaProbeDep = Annotated[MediaProbe, Depends(get_media_probe)]
