from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Values come from environment variables (or a local `.env`)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    engine: Literal["faster-whisper", "fake"] = "faster-whisper"
    whisper_model: str = "Systran/faster-whisper-small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"

    max_upload_mb: int = Field(default=200, gt=0)
    max_duration_sec: int = Field(default=3600, gt=0)

    redis_url: str = "redis://redis:6379/0"
    upload_dir: Path = Path("/data/uploads")
    job_ttl_hours: int = Field(default=24, gt=0)
    job_timeout_sec: int = Field(default=3600, gt=0)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["text", "json"] = "text"


@lru_cache
def get_settings() -> Settings:
    return Settings()
