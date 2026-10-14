import fakeredis
from fastapi.testclient import TestClient


def test_health_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "redis": "ok"}


def test_health_redis_down(client: TestClient, redis_server: fakeredis.FakeServer) -> None:
    redis_server.connected = False

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "redis_unavailable"
