from collections.abc import Iterator
from pathlib import Path

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_media_probe, get_redis
from app.config import Settings
from app.main import create_app
from app.services.media_probe import MediaInfo, MediaProbeError


class FakeMediaProbe:
    """Stands in for ffprobe. Set `duration_sec`, or `error` to simulate unreadable media."""

    def __init__(self) -> None:
        self.duration_sec = 10.0
        self.error: MediaProbeError | None = None

    def probe(self, path: Path) -> MediaInfo:
        if self.error:
            raise self.error
        return MediaInfo(duration_sec=self.duration_sec)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        engine="fake",
        upload_dir=tmp_path / "uploads",
        max_upload_mb=1,
        max_duration_sec=60,
    )


@pytest.fixture
def redis_server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


@pytest.fixture
def fake_redis(redis_server: fakeredis.FakeServer) -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(server=redis_server)


@pytest.fixture
def media_probe() -> FakeMediaProbe:
    return FakeMediaProbe()


@pytest.fixture
def app(
    settings: Settings, fake_redis: fakeredis.FakeRedis, media_probe: FakeMediaProbe
) -> FastAPI:
    app = create_app(settings)
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_media_probe] = lambda: media_probe
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # raise_server_exceptions=False: unexpected errors come back as 500 responses, as in production.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
