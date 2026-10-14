import logging
from datetime import UTC, datetime
from typing import BinaryIO
from uuid import uuid4

from redis.exceptions import RedisError

from app.api.errors import AppError
from app.config import Settings
from app.jobs.models import Job, JobStatus
from app.jobs.store import JobStore
from app.services.languages import normalize_language
from app.services.media_probe import MediaProbe
from app.services.uploads import save_upload

logger = logging.getLogger(__name__)


def submit_job(
    source: BinaryIO,
    filename: str | None,
    language: str | None,
    settings: Settings,
    store: JobStore,
    probe: MediaProbe,
) -> Job:
    """Validate and store an upload, then record a queued job. Blocking: run it in a thread."""
    language_requested = normalize_language(language)
    job_id = uuid4().hex
    stored = save_upload(source, filename, job_id, settings, probe)

    job = Job(
        id=job_id,
        status=JobStatus.QUEUED,
        filename=stored.filename,
        size_bytes=stored.size_bytes,
        media_duration_sec=stored.duration_sec,
        language_requested=language_requested,
        created_at=datetime.now(UTC),
    )
    try:
        store.create(job)
    except RedisError as exc:
        stored.path.unlink(missing_ok=True)
        logger.warning("could not record job", extra={"job_id": job_id, "error": repr(exc)})
        raise AppError(
            "queue_unavailable",
            "The service is temporarily unavailable. Please try again in a moment.",
            503,
        ) from exc

    logger.info(
        "job created",
        extra={
            "job_id": job_id,
            # "filename" is a reserved LogRecord attribute, hence "file_name".
            "file_name": stored.filename,
            "size_bytes": stored.size_bytes,
            "media_duration_sec": round(stored.duration_sec, 1),
            "language_requested": language_requested,
        },
    )
    return job
