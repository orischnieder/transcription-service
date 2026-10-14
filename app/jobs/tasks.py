"""The transcription task, executed by the RQ worker (see `app.worker`)."""

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rq.timeouts import JobTimeoutException

from app.engines.base import TranscriptionEngine
from app.jobs.models import Job, JobResult
from app.jobs.store import JobStore

logger = logging.getLogger(__name__)

PROGRESS_INTERVAL_SEC = 1.0
PROGRESS_LOG_MILESTONES = (25, 50, 75)


@dataclass(frozen=True)
class WorkerContext:
    engine: TranscriptionEngine
    store: JobStore
    model_name: str


# Set once by the worker at start-up, after the model is loaded. SimpleWorker runs jobs in the
# same process, so every job reuses this engine instead of loading the model again.
_context: WorkerContext | None = None


def configure(context: WorkerContext) -> None:
    global _context
    _context = context


def run_transcription(job_id: str, media_path: str) -> None:
    """Entry point that RQ calls by import path."""
    if _context is None:
        raise RuntimeError("Worker context is not configured; start jobs via app.worker.")
    process_job(job_id, Path(media_path), _context)


def process_job(job_id: str, media_path: Path, context: WorkerContext) -> None:
    try:
        job = context.store.get(job_id)
        if job is None:
            logger.warning("job not found, skipping (expired?)", extra={"job_id": job_id})
            return
        _transcribe(job, media_path, context)
    finally:
        # Success or failure, the media file is not needed any more (privacy and disk space).
        media_path.unlink(missing_ok=True)


def _transcribe(job: Job, media_path: Path, context: WorkerContext) -> None:
    store = context.store
    store.mark_processing(job.id, datetime.now(UTC))
    logger.info(
        "job started",
        extra={
            "job_id": job.id,
            "file_name": job.filename,
            "size_bytes": job.size_bytes,
            "media_duration_sec": round(job.media_duration_sec, 1),
            "language_requested": job.language_requested,
            "model": context.model_name,
        },
    )

    start = time.monotonic()
    try:
        result = context.engine.transcribe(
            media_path, job.language_requested, ProgressReporter(job.id, store)
        )
    except Exception as exc:
        error_code, error_message = _describe_failure(exc)
        logger.exception("job failed", extra={"job_id": job.id, "error_code": error_code})
        store.mark_failed(job.id, error_code, error_message, datetime.now(UTC))
        return

    took_sec = time.monotonic() - start
    store.mark_done(
        job.id, JobResult.model_validate(result, from_attributes=True), datetime.now(UTC)
    )
    logger.info(
        "job done",
        extra={
            "job_id": job.id,
            "took_sec": round(took_sec, 2),
            # Real-time factor: processing time / audio duration (below 1 = faster than real time).
            "rtf": round(took_sec / job.media_duration_sec, 3) if job.media_duration_sec else None,
            "language_detected": result.language,
            "segments": len(result.segments),
        },
    )


def fail_orphaned_jobs(store: JobStore, upload_dir: Path) -> None:
    """Mark jobs left in "processing" by a worker that died mid-job as failed.

    Call this at worker start-up, before taking new work. It assumes a single worker: with
    several workers, a job in "processing" may belong to another live worker, so each job would
    need a heartbeat to tell live jobs from orphans.
    """
    for job_id in store.processing_job_ids():
        store.mark_failed(
            job_id,
            "worker_restarted",
            "The transcription was interrupted because the server restarted. Please try again.",
            datetime.now(UTC),
        )
        # The stored file is `{job_id}.{ext}`; job ids are hex, so the pattern cannot match others.
        for media_path in upload_dir.glob(f"{job_id}.*"):
            media_path.unlink(missing_ok=True)
        logger.warning(
            "orphaned job marked failed", extra={"job_id": job_id, "error_code": "worker_restarted"}
        )


def _describe_failure(exc: Exception) -> tuple[str, str]:
    """Map an exception to the error code and user-facing message stored on the job."""
    if isinstance(exc, JobTimeoutException):
        return "transcription_timeout", "Transcription took too long and was stopped."
    return "transcription_failed", "We could not transcribe this file."


class ProgressReporter:
    """Engine progress callback: writes to Redis at most once per interval, logs milestones."""

    def __init__(
        self,
        job_id: str,
        store: JobStore,
        interval_sec: float = PROGRESS_INTERVAL_SEC,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._job_id = job_id
        self._store = store
        self._interval_sec = interval_sec
        self._clock = clock
        self._last_write = -math.inf
        self._milestones = list(PROGRESS_LOG_MILESTONES)

    def __call__(self, percent: float) -> None:
        # Capped at 99 while processing: 100% is shown only once the job is actually done.
        progress = min(int(percent), 99)
        now = self._clock()
        if now - self._last_write >= self._interval_sec:
            self._store.set_progress(self._job_id, progress)
            self._last_write = now
        while self._milestones and progress >= self._milestones[0]:
            milestone = self._milestones.pop(0)
            logger.info("job progress", extra={"job_id": self._job_id, "progress": milestone})
