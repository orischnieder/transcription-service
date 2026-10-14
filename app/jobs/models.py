from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class Job(BaseModel):
    id: str
    status: JobStatus
    filename: str
    size_bytes: int
    media_duration_sec: float
    language_requested: str | None = None
    language_detected: str | None = None
    progress: int = Field(default=0, ge=0, le=100)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class SegmentResult(BaseModel):
    start: float
    end: float
    text: str


class JobResult(BaseModel):
    text: str
    language: str
    duration_sec: float
    segments: list[SegmentResult]
