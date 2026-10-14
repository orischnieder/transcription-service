from datetime import UTC, datetime

import fakeredis

from app.jobs.models import Job, JobStatus
from app.jobs.store import JobStore, job_key

TTL_SEC = 3600


def make_job() -> Job:
    return Job(
        id="abc123",
        status=JobStatus.QUEUED,
        filename="talk.mp3",
        size_bytes=1234,
        media_duration_sec=12.5,
        language_requested="en",
        created_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )


def test_create_and_get_round_trip() -> None:
    store = JobStore(fakeredis.FakeRedis(), ttl_sec=TTL_SEC)
    job = make_job()

    store.create(job)

    assert store.get(job.id) == job


def test_job_expires_after_ttl() -> None:
    redis = fakeredis.FakeRedis()
    JobStore(redis, ttl_sec=TTL_SEC).create(make_job())

    assert 0 < redis.ttl(job_key("abc123")) <= TTL_SEC


def test_unset_fields_are_not_stored() -> None:
    redis = fakeredis.FakeRedis()
    JobStore(redis, ttl_sec=TTL_SEC).create(make_job())

    assert not redis.hexists(job_key("abc123"), "error_code")


def test_get_unknown_job_returns_none() -> None:
    assert JobStore(fakeredis.FakeRedis(), ttl_sec=TTL_SEC).get("missing") is None
