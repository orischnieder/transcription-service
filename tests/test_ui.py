from fastapi.testclient import TestClient


def test_index_serves_the_ui(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>Transcribe</title>" in response.text


def test_static_assets_are_served(client: TestClient) -> None:
    for path, content_type in [
        ("/static/app.js", "javascript"),
        ("/static/styles.css", "text/css"),
    ]:
        response = client.get(path)
        assert response.status_code == 200
        assert content_type in response.headers["content-type"]


def test_ui_loads_no_external_assets(client: TestClient) -> None:
    page = client.get("/").text + client.get("/static/styles.css").text

    assert "http://" not in page.replace("http://www.w3.org/2000/svg", "")
    assert "https://" not in page
