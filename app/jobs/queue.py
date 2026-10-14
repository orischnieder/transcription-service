from pathlib import Path

from redis import Redis
from rq import Queue

QUEUE_NAME = "transcription"
# Referenced by import path so the API process never imports the worker task (or the engine).
TASK = "app.jobs.tasks.run_transcription"


class JobQueue:
    def __init__(self, redis: Redis, job_timeout_sec: int, failure_ttl_sec: int) -> None:
        self._queue = Queue(QUEUE_NAME, connection=redis)
        self._job_timeout_sec = job_timeout_sec
        self._failure_ttl_sec = failure_ttl_sec

    def enqueue(self, job_id: str, media_path: Path) -> None:
        self._queue.enqueue_call(
            TASK,
            args=(job_id, str(media_path)),
            job_id=job_id,  # same id in RQ and in our job store, so logs line up
            timeout=self._job_timeout_sec,
            result_ttl=0,  # the task stores its own result; RQ keeps nothing on success
            failure_ttl=self._failure_ttl_sec,
        )
