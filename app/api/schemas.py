from typing import Literal

from pydantic import BaseModel

from app.jobs.models import Job, JobResult


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthResponse(BaseModel):
    status: Literal["ok"]
    redis: Literal["ok"]


class JobResponse(Job):
    result: JobResult | None = None  # present only when status is "done"


class LimitsResponse(BaseModel):
    allowed_extensions: list[str]
    max_upload_mb: int
    max_duration_sec: int
