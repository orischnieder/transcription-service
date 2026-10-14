from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import AppError


def test_unknown_route_uses_error_shape(client: TestClient) -> None:
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found"}}


def test_app_error_is_converted(app: FastAPI, client: TestClient) -> None:
    @app.get("/boom-app-error")
    def boom() -> None:
        raise AppError("file_too_large", "The file is too large.", 413)

    response = client.get("/boom-app-error")

    assert response.status_code == 413
    assert response.json() == {
        "error": {"code": "file_too_large", "message": "The file is too large."}
    }


def test_unexpected_error_returns_generic_500(app: FastAPI, client: TestClient) -> None:
    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    response = client.get("/boom")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "secret" not in response.text


def test_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "abc123"})

    assert response.headers["X-Request-ID"] == "abc123"


def test_request_id_is_generated(client: TestClient) -> None:
    response = client.get("/health")

    assert len(response.headers["X-Request-ID"]) == 32
