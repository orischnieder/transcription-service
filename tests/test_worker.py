import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import fakeredis
import pytest

from app.jobs.models import Job, JobResult, JobStatus
from app.jobs.store import JobStore
from app.jobs.tasks import fail_orphaned_jobs
from app.worker import DemoteRqSuccessLogs

NOW = datetime(2026, 1, 2, tzinfo=UTC)


def make_job(job_id: str) -> Job:
    return Job(
        id=job_id,
        status=JobStatus.QUEUED,
        filename="talk.mp3",
        size_bytes=10,
        media_duration_sec=5.0,
        created_at=NOW,
    )


@pytest.fixture
def store() -> JobStore:
    return JobStore(fakeredis.FakeRedis(), ttl_sec=3600)


# Orphaned jobs


def test_processing_job_ids_finds_only_processing_jobs(store: JobStore) -> None:
    for job_id in ("queued1", "processing1", "processing2", "done1"):
        store.create(make_job(job_id))
    store.mark_processing("processing1", NOW)
    store.mark_processing("processing2", NOW)
    result = JobResult(text="", language="en", duration_sec=1.0, segments=[])
    store.mark_done("done1", result, NOW)  # also writes a `job:done1:result` string key

    assert sorted(store.processing_job_ids()) == ["processing1", "processing2"]


def test_orphaned_job_is_failed_and_its_file_deleted(
    store: JobStore, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store.create(make_job("abc"))
    store.mark_processing("abc", NOW)
    store.create(make_job("def"))  # still queued: must not be touched
    orphan_file = tmp_path / "abc.mp3"
    queued_file = tmp_path / "def.mp3"
    orphan_file.write_bytes(b"audio")
    queued_file.write_bytes(b"audio")

    fail_orphaned_jobs(store, tmp_path)

    orphan = store.get("abc")
    assert orphan is not None
    assert orphan.status == JobStatus.FAILED
    assert orphan.error_code == "worker_restarted"
    assert orphan.error_message
    assert orphan.finished_at is not None
    assert not orphan_file.exists()

    queued = store.get("def")
    assert queued is not None
    assert queued.status == JobStatus.QUEUED
    assert queued_file.exists()

    logged = [r for r in caplog.records if r.getMessage() == "orphaned job marked failed"]
    assert [r.job_id for r in logged] == ["abc"]  # type: ignore[attr-defined]
    assert logged[0].levelno == logging.WARNING


def test_no_orphans_is_a_no_op(store: JobStore, tmp_path: Path) -> None:
    store.create(make_job("abc"))

    fail_orphaned_jobs(store, tmp_path)

    job = store.get("abc")
    assert job is not None
    assert job.status == JobStatus.QUEUED


# RQ success log demotion


@pytest.fixture
def rq_logger() -> Iterator[logging.Logger]:
    logger = logging.getLogger("rq.worker")
    original_level = logger.level
    yield logger
    logger.setLevel(original_level)


def rq_record(message: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord("rq.worker", logging.INFO, __file__, 1, message, args, None)


@pytest.mark.parametrize(
    ("message", "args"),
    [
        ("Worker %s: %s: %s (%s)", ("w1", "transcription", "Job OK", "abc")),
        ("Successfully completed job %s in %ss on worker %s", ("abc", "3.9", "w1")),
    ],
)
def test_rq_success_lines_are_hidden_at_info(
    rq_logger: logging.Logger, message: str, args: tuple[object, ...]
) -> None:
    rq_logger.setLevel(logging.INFO)

    assert DemoteRqSuccessLogs().filter(rq_record(message, *args)) is False


def test_rq_success_lines_are_kept_as_debug_at_debug(rq_logger: logging.Logger) -> None:
    rq_logger.setLevel(logging.DEBUG)
    record = rq_record("Worker %s: %s: %s (%s)", "w1", "transcription", "Job OK", "abc")

    assert DemoteRqSuccessLogs().filter(record) is True
    assert (record.levelno, record.levelname) == (logging.DEBUG, "DEBUG")


def test_other_rq_lines_are_untouched(rq_logger: logging.Logger) -> None:
    rq_logger.setLevel(logging.INFO)
    record = rq_record("*** Listening on %s...", "transcription")

    assert DemoteRqSuccessLogs().filter(record) is True
    assert record.levelno == logging.INFO
