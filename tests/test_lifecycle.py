"""Full job lifecycle: upload through the API, run the worker task with FakeEngine, read results."""

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from rq.timeouts import JobTimeoutException

from app.engines.base import Segment, TranscriptionResult
from app.engines.fake_engine import FakeEngine
from app.jobs.store import JobStore
from app.jobs.tasks import ProgressReporter, WorkerContext, process_job
from tests.conftest import FakeJobQueue


def submit(client: TestClient, job_queue: FakeJobQueue, filename: str = "talk.mp3") -> Path:
    response = client.post("/api/jobs", files={"file": (filename, b"audio")})
    assert response.status_code == 202
    job_id, media_path = job_queue.enqueued[-1]
    assert job_id == response.json()["id"]
    return media_path


def job_id_of(media_path: Path) -> str:
    return media_path.stem


def context(store: JobStore, engine: object) -> WorkerContext:
    return WorkerContext(engine=engine, store=store, model_name="fake")  # type: ignore[arg-type]


# Success


def test_job_goes_from_queued_to_done_with_result(
    client: TestClient, job_queue: FakeJobQueue, store: JobStore
) -> None:
    media_path = submit(client, job_queue)
    job_id = job_id_of(media_path)
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "queued"

    process_job(job_id, media_path, context(store, FakeEngine()))

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "done"
    assert job["progress"] == 100
    assert job["language_detected"] == "en"
    assert job["started_at"] is not None
    assert job["finished_at"] is not None
    assert job["result"]["text"].startswith("This is a fake transcription.")
    assert job["result"]["segments"][0] == {
        "start": 0.0,
        "end": 2.0,
        "text": "This is a fake transcription.",
    }


def test_media_file_is_deleted_after_success(
    client: TestClient, job_queue: FakeJobQueue, store: JobStore
) -> None:
    media_path = submit(client, job_queue)
    assert media_path.exists()

    process_job(job_id_of(media_path), media_path, context(store, FakeEngine()))

    assert not media_path.exists()


def test_transcript_downloads(client: TestClient, job_queue: FakeJobQueue, store: JobStore) -> None:
    media_path = submit(client, job_queue, filename="Interview Ünïcode.mp3")
    job_id = job_id_of(media_path)
    process_job(job_id, media_path, context(store, FakeEngine()))

    txt = client.get(f"/api/jobs/{job_id}/transcript.txt")
    srt = client.get(f"/api/jobs/{job_id}/transcript.srt")

    assert txt.status_code == 200
    assert txt.headers["content-type"] == "text/plain; charset=utf-8"
    assert txt.text.splitlines()[0] == "This is a fake transcription."
    assert txt.headers["content-disposition"] == (
        'attachment; filename="Interview ncode.txt"; '
        "filename*=UTF-8''Interview%20%C3%9Cn%C3%AFcode.txt"
    )
    assert srt.status_code == 200
    assert srt.headers["content-type"].startswith("application/x-subrip")
    assert srt.text.startswith("1\n00:00:00,000 --> 00:00:02,000\nThis is a fake transcription.\n")


# Failure


def test_engine_failure_marks_job_failed(
    client: TestClient, job_queue: FakeJobQueue, store: JobStore
) -> None:
    media_path = submit(client, job_queue)
    job_id = job_id_of(media_path)

    process_job(job_id, media_path, context(store, FakeEngine(fail=True)))

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "failed"
    assert job["error_code"] == "transcription_failed"
    assert job["error_message"]
    assert job["result"] is None
    assert not media_path.exists()
    response = client.get(f"/api/jobs/{job_id}/transcript.txt")
    assert response.status_code == 409


def test_timeout_is_reported_as_its_own_error(
    client: TestClient, job_queue: FakeJobQueue, store: JobStore
) -> None:
    class SlowEngine:
        def transcribe(self, *args: object, **kwargs: object) -> TranscriptionResult:
            raise JobTimeoutException("took too long")

    media_path = submit(client, job_queue)
    job_id = job_id_of(media_path)

    process_job(job_id, media_path, context(store, SlowEngine()))

    assert client.get(f"/api/jobs/{job_id}").json()["error_code"] == "transcription_timeout"


def test_failure_is_logged_with_job_id_and_stack_trace(
    client: TestClient, job_queue: FakeJobQueue, store: JobStore, caplog: pytest.LogCaptureFixture
) -> None:
    media_path = submit(client, job_queue)
    job_id = job_id_of(media_path)

    process_job(job_id, media_path, context(store, FakeEngine(fail=True)))

    failed = [r for r in caplog.records if r.getMessage() == "job failed"]
    assert len(failed) == 1
    assert failed[0].job_id == job_id  # type: ignore[attr-defined]
    assert failed[0].error_code == "transcription_failed"  # type: ignore[attr-defined]
    assert failed[0].exc_info is not None


def test_missing_job_is_skipped_and_file_deleted(tmp_path: Path, store: JobStore) -> None:
    media_path = tmp_path / "orphan.mp3"
    media_path.write_bytes(b"audio")

    process_job("unknown", media_path, context(store, FakeEngine()))

    assert not media_path.exists()


# Progress


class RecordingStore:
    def __init__(self) -> None:
        self.progress: list[int] = []

    def set_progress(self, job_id: str, progress: int) -> None:
        self.progress.append(progress)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_progress_writes_are_throttled() -> None:
    store, clock = RecordingStore(), FakeClock()
    reporter = ProgressReporter("job1", store, interval_sec=1.0, clock=clock)  # type: ignore[arg-type]

    reporter(10)  # written (first call)
    clock.now = 0.5
    reporter(20)  # skipped: less than a second since the last write
    clock.now = 1.0
    reporter(30)  # written

    assert store.progress == [10, 30]


def test_progress_is_capped_at_99_until_done() -> None:
    store = RecordingStore()
    reporter = ProgressReporter("job1", store, clock=FakeClock())  # type: ignore[arg-type]

    reporter(100.0)

    assert store.progress == [99]


def test_progress_milestones_are_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    reporter = ProgressReporter("job1", RecordingStore(), clock=FakeClock())  # type: ignore[arg-type]

    for percent in (10, 30, 30, 80, 100):
        reporter(percent)

    logged = [r for r in caplog.records if r.getMessage() == "job progress"]
    assert [r.progress for r in logged] == [25, 50, 75]  # type: ignore[attr-defined]


def test_result_from_engine_dataclass_round_trips(
    client: TestClient, job_queue: FakeJobQueue, store: JobStore
) -> None:
    segments = [Segment(0.0, 1.5, "Shalom."), Segment(1.5, 3.0, "Hello.")]
    media_path = submit(client, job_queue)
    job_id = job_id_of(media_path)

    process_job(job_id, media_path, context(store, FakeEngine(segments=segments)))

    result = store.get_result(job_id)
    assert result is not None
    assert [(s.start, s.end, s.text) for s in result.segments] == [
        (0.0, 1.5, "Shalom."),
        (1.5, 3.0, "Hello."),
    ]
