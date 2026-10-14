from pathlib import Path

import fakeredis
import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import Settings
from app.services.media_probe import MediaProbeError
from tests.conftest import FakeJobQueue, FakeMediaProbe

AUDIO_BYTES = b"fake audio content"


def upload(
    client: TestClient,
    filename: str = "talk.mp3",
    content: bytes = AUDIO_BYTES,
    language: str | None = None,
) -> Response:
    data = {"language": language} if language is not None else {}
    return client.post("/api/jobs", files={"file": (filename, content)}, data=data)


def stored_files(settings: Settings) -> list[Path]:
    return list(settings.upload_dir.iterdir())


def assert_error(response: Response, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    body = response.json()
    assert body["error"]["code"] == code
    assert body["error"]["message"]


# Valid upload


def test_valid_upload_creates_queued_job(client: TestClient, settings: Settings) -> None:
    response = upload(client)

    assert response.status_code == 202
    job = response.json()
    assert response.headers["Location"] == f"/api/jobs/{job['id']}"
    assert job["status"] == "queued"
    assert job["filename"] == "talk.mp3"
    assert job["size_bytes"] == len(AUDIO_BYTES)
    assert job["media_duration_sec"] == 10.0
    assert job["progress"] == 0
    assert job["language_requested"] is None
    assert stored_files(settings) == [settings.upload_dir / f"{job['id']}.mp3"]


def test_created_job_can_be_fetched(client: TestClient) -> None:
    job = upload(client).json()

    response = client.get(f"/api/jobs/{job['id']}")

    assert response.status_code == 200
    assert response.json() == {**job, "result": None}


def test_language_is_normalized(client: TestClient) -> None:
    assert upload(client, language=" EN ").json()["language_requested"] == "en"
    assert upload(client, language="auto").json()["language_requested"] is None


@pytest.mark.parametrize("filename", ["lecture.mp4", "clip.MOV", "talk.mkv", "call.webm"])
def test_video_files_are_accepted(client: TestClient, filename: str) -> None:
    assert upload(client, filename=filename).status_code == 202


def test_extension_check_is_case_insensitive(client: TestClient) -> None:
    assert upload(client, filename="TALK.WAV").status_code == 202


def test_path_in_filename_is_stripped(client: TestClient) -> None:
    job = upload(client, filename="C:\\Users\\someone\\talk.mp3").json()

    assert job["filename"] == "talk.mp3"


# Validation errors (and nothing left on disk)


@pytest.mark.parametrize("filename", ["notes.txt", "talk", "archive.mp3.zip"])
def test_unsupported_type_returns_415(
    client: TestClient, settings: Settings, filename: str
) -> None:
    assert_error(upload(client, filename=filename), 415, "unsupported_file_type")
    assert stored_files(settings) == []


def test_empty_file_returns_400(client: TestClient, settings: Settings) -> None:
    assert_error(upload(client, content=b""), 400, "empty_file")
    assert stored_files(settings) == []


def test_too_large_file_returns_413(client: TestClient, settings: Settings) -> None:
    too_large = b"x" * (settings.max_upload_mb * 1024 * 1024 + 1)

    assert_error(upload(client, content=too_large), 413, "file_too_large")
    assert stored_files(settings) == []


def test_file_at_size_limit_is_accepted(client: TestClient, settings: Settings) -> None:
    at_limit = b"x" * (settings.max_upload_mb * 1024 * 1024)

    assert upload(client, content=at_limit).status_code == 202


def test_invalid_media_returns_422(
    client: TestClient, settings: Settings, media_probe: FakeMediaProbe
) -> None:
    media_probe.error = MediaProbeError("no audio stream")

    assert_error(upload(client), 422, "invalid_media")
    assert stored_files(settings) == []


def test_too_long_media_returns_422(
    client: TestClient, settings: Settings, media_probe: FakeMediaProbe
) -> None:
    media_probe.duration_sec = settings.max_duration_sec + 1

    assert_error(upload(client), 422, "media_too_long")
    assert stored_files(settings) == []


def test_invalid_language_returns_422(client: TestClient, settings: Settings) -> None:
    assert_error(upload(client, language="klingon"), 422, "invalid_language")
    assert stored_files(settings) == []


def test_missing_file_returns_422(client: TestClient) -> None:
    assert_error(client.post("/api/jobs", data={"language": "en"}), 422, "invalid_request")


def test_redis_down_returns_503_and_removes_file(
    client: TestClient, settings: Settings, redis_server: fakeredis.FakeServer
) -> None:
    redis_server.connected = False

    assert_error(upload(client), 503, "queue_unavailable")
    assert stored_files(settings) == []


# Other endpoints


def test_unknown_job_returns_404(client: TestClient) -> None:
    assert_error(client.get("/api/jobs/does-not-exist"), 404, "job_not_found")


def test_limits(client: TestClient) -> None:
    response = client.get("/api/limits")

    assert response.status_code == 200
    limits = response.json()
    assert limits["max_upload_mb"] == 1
    assert limits["max_duration_sec"] == 60
    assert "mp3" in limits["allowed_extensions"]
    assert limits["allowed_extensions"] == sorted(limits["allowed_extensions"])
    assert "he" in limits["languages"]
    assert "en" in limits["languages"]


def test_valid_upload_is_enqueued(
    client: TestClient, settings: Settings, job_queue: FakeJobQueue
) -> None:
    job = upload(client).json()

    assert job_queue.enqueued == [(job["id"], settings.upload_dir / f"{job['id']}.mp3")]


def test_enqueue_failure_returns_503_and_cleans_up(
    client: TestClient,
    settings: Settings,
    job_queue: FakeJobQueue,
    fake_redis: fakeredis.FakeRedis,
) -> None:
    job_queue.error = RedisConnectionError("redis down")

    response = upload(client)

    assert_error(response, 503, "queue_unavailable")
    assert stored_files(settings) == []
    assert fake_redis.keys("job:*") == []  # the job record created before enqueue was removed


def test_transcript_of_queued_job_returns_409(client: TestClient) -> None:
    job = upload(client).json()

    assert_error(client.get(f"/api/jobs/{job['id']}/transcript.txt"), 409, "job_not_ready")
    assert_error(client.get(f"/api/jobs/{job['id']}/transcript.srt"), 409, "job_not_ready")


@pytest.mark.parametrize("fmt", ["txt", "srt"])
def test_transcript_of_unknown_job_returns_404(client: TestClient, fmt: str) -> None:
    assert_error(client.get(f"/api/jobs/nope/transcript.{fmt}"), 404, "job_not_found")


# Early Content-Length check


def test_declared_size_over_limit_is_rejected_before_reading(
    client: TestClient, settings: Settings, job_queue: FakeJobQueue
) -> None:
    declared = settings.max_upload_mb * 1024 * 1024 + 64 * 1024 + 1
    response = client.post(
        "/api/jobs",
        files={"file": ("talk.mp3", AUDIO_BYTES)},
        headers={"Content-Length": str(declared)},
    )

    assert_error(response, 413, "file_too_large")
    assert stored_files(settings) == []
    assert job_queue.enqueued == []


# Raw bytes: a header is latin-1 on the wire, so "²" (0xB2) can arrive; str.isdigit() accepts it.
@pytest.mark.parametrize("content_length", [b"not-a-number", b"-5", "²".encode("latin-1")])
def test_invalid_content_length_falls_through_to_upload_checks(
    client: TestClient, content_length: bytes
) -> None:
    response = client.post(
        "/api/jobs",
        files={"file": ("talk.mp3", AUDIO_BYTES)},
        headers={"Content-Length": content_length},
    )

    assert response.status_code == 202


def test_size_guard_only_applies_to_uploads(client: TestClient) -> None:
    response = client.get("/api/limits", headers={"Content-Length": str(10**12)})

    assert response.status_code == 200
