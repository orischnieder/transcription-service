import logging
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from redis.exceptions import RedisError

from app.api.errors import AppError
from app.config import Settings
from app.jobs.models import Job, JobResult, JobStatus
from app.jobs.queue import JobQueue
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
    queue: JobQueue,
    probe: MediaProbe,
) -> Job:
    """Validate and store an upload, record a queued job, enqueue it. Blocking: run in a thread."""
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
        # Record first, then enqueue: the worker must always find the job it is given.
        store.create(job)
        queue.enqueue(job_id, stored.path)
    except RedisError as exc:
        logger.warning("could not queue job", extra={"job_id": job_id, "error": repr(exc)})
        _discard(job_id, stored.path, store)
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
            "language_requested": language_requested or "auto",
        },
    )
    return job


def _discard(job_id: str, media_path: Path, store: JobStore) -> None:
    media_path.unlink(missing_ok=True)
    # Best effort: if Redis is down, the job record may be gone or unreachable anyway, and any
    # record left behind still expires with its TTL.
    with suppress(RedisError):
        store.delete(job_id)


def get_job(store: JobStore, job_id: str) -> Job:
    job = store.get(job_id)
    if job is None:
        raise AppError("job_not_found", "This job does not exist or has expired.", 404)
    return job


def get_finished_result(store: JobStore, job_id: str) -> tuple[Job, JobResult]:
    job = get_job(store, job_id)
    if job.status == JobStatus.FAILED:
        raise AppError("job_not_ready", "This job failed, so there is no transcript.", 409)
    if job.status != JobStatus.DONE:
        raise AppError("job_not_ready", "The transcript is not ready yet.", 409)
    result = store.get_result(job_id)
    if result is None:
        raise AppError("job_not_found", "This job does not exist or has expired.", 404)
    return job, result
