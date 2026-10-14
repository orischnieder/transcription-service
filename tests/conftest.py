from collections.abc import Iterator

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_redis
from app.config import Settings
from app.main import create_app


@pytest.fixture
def redis_server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


@pytest.fixture
def app(redis_server: fakeredis.FakeServer) -> FastAPI:
    app = create_app(Settings(_env_file=None, engine="fake"))
    fake_redis = fakeredis.FakeRedis(server=redis_server)
    app.dependency_overrides[get_redis] = lambda: fake_redis
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # raise_server_exceptions=False: unexpected errors come back as 500 responses, as in production.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
