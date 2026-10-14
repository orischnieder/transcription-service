from pathlib import Path

import fakeredis
from rq import Queue

from app.jobs.queue import QUEUE_NAME, TASK, JobQueue


def test_enqueue_creates_rq_job_with_our_id_and_timeout() -> None:
    redis = fakeredis.FakeRedis()
    JobQueue(redis, job_timeout_sec=600, failure_ttl_sec=3600).enqueue("abc123", Path("/x/a.mp3"))

    queue = Queue(QUEUE_NAME, connection=redis)
    assert queue.job_ids == ["abc123"]
    rq_job = queue.fetch_job("abc123")
    assert rq_job is not None
    assert rq_job.func_name == TASK
    assert rq_job.args == ("abc123", str(Path("/x/a.mp3")))
    assert rq_job.timeout == 600
