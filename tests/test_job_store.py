from datetime import UTC, datetime

import fakeredis

from app.jobs.models import Job, JobResult, JobStatus, SegmentResult
from app.jobs.store import JobStore, job_key, result_key

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


def test_mark_done_stores_result_and_refreshes_ttl() -> None:
    redis = fakeredis.FakeRedis()
    store = JobStore(redis, ttl_sec=TTL_SEC)
    store.create(make_job())
    redis.expire(job_key("abc123"), 10)  # pretend most of the TTL has passed
    result = JobResult(
        text="Hi.",
        language="en",
        duration_sec=1.0,
        segments=[SegmentResult(start=0, end=1, text="Hi.")],
    )

    store.mark_done("abc123", result, datetime(2026, 1, 2, 3, 5, tzinfo=UTC))

    job = store.get("abc123")
    assert job is not None
    assert (job.status, job.progress, job.language_detected) == (JobStatus.DONE, 100, "en")
    assert store.get_result("abc123") == result
    assert redis.ttl(job_key("abc123")) > 10
    assert 0 < redis.ttl(result_key("abc123")) <= TTL_SEC


def test_mark_failed_records_error() -> None:
    store = JobStore(fakeredis.FakeRedis(), ttl_sec=TTL_SEC)
    store.create(make_job())

    store.mark_failed("abc123", "transcription_failed", "Nope.", datetime(2026, 1, 2, tzinfo=UTC))

    job = store.get("abc123")
    assert job is not None
    assert (job.status, job.error_code, job.error_message) == (
        JobStatus.FAILED,
        "transcription_failed",
        "Nope.",
    )
    assert store.get_result("abc123") is None


def test_delete_removes_job_and_result() -> None:
    redis = fakeredis.FakeRedis()
    store = JobStore(redis, ttl_sec=TTL_SEC)
    store.create(make_job())

    store.delete("abc123")

    assert redis.keys("job:*") == []
