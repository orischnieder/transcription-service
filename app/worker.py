"""Worker entrypoint: `python -m app.worker`.

Loads the transcription engine once, then processes jobs from the RQ queue with a SimpleWorker
(jobs run in this process, no fork per job), so the model is never reloaded between jobs.
"""

import logging

from redis import Redis
from rq import Queue, SimpleWorker

from app.config import Settings, get_settings
from app.engines.factory import create_engine
from app.jobs import tasks
from app.jobs.queue import QUEUE_NAME
from app.jobs.store import JobStore
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)


class DemoteRqSuccessLogs(logging.Filter):
    """Turn RQ's per-job success lines into DEBUG.

    Our task records failures in the job store instead of raising, so RQ reports every job as
    successful ("Job OK"), even a failed transcription. Our own "job done" / "job failed" lines
    are the source of truth; RQ's success lines only remain visible with LOG_LEVEL=DEBUG.
    """

    MARKERS = ("Job OK", "Successfully completed")

    def filter(self, record: logging.LogRecord) -> bool:
        if not any(marker in record.getMessage() for marker in self.MARKERS):
            return True
        record.levelno, record.levelname = logging.DEBUG, "DEBUG"
        return logging.getLogger(record.name).isEnabledFor(logging.DEBUG)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    logging.getLogger("rq.worker").addFilter(DemoteRqSuccessLogs())

    # No socket timeout: the worker blocks on Redis while waiting for jobs.
    redis = Redis.from_url(settings.redis_url)
    store = JobStore(redis, ttl_sec=settings.job_ttl_hours * 3600)
    # Before taking new work (and before the slow model load, so users see the failure sooner):
    # jobs still "processing" were interrupted by a previous worker crash.
    tasks.fail_orphaned_jobs(store, settings.upload_dir)

    model_name = _model_name(settings)
    engine = create_engine(settings)
    tasks.configure(tasks.WorkerContext(engine=engine, store=store, model_name=model_name))

    logger.info("worker ready", extra={"queue": QUEUE_NAME, "model": model_name})
    SimpleWorker([Queue(QUEUE_NAME, connection=redis)], connection=redis).work()


def _model_name(settings: Settings) -> str:
    return settings.whisper_model if settings.engine == "faster-whisper" else settings.engine


if __name__ == "__main__":
    main()
