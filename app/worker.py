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


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    # No socket timeout: the worker blocks on Redis while waiting for jobs.
    redis = Redis.from_url(settings.redis_url)
    model_name = _model_name(settings)
    engine = create_engine(settings)
    tasks.configure(
        tasks.WorkerContext(
            engine=engine,
            store=JobStore(redis, ttl_sec=settings.job_ttl_hours * 3600),
            model_name=model_name,
        )
    )

    logger.info("worker ready", extra={"queue": QUEUE_NAME, "model": model_name})
    SimpleWorker([Queue(QUEUE_NAME, connection=redis)], connection=redis).work()


def _model_name(settings: Settings) -> str:
    return settings.whisper_model if settings.engine == "faster-whisper" else settings.engine


if __name__ == "__main__":
    main()
