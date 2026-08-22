"""GET /health — liveness probe contract."""


def test_health_returns_200(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200


def test_health_response_body_is_exact(client) -> None:
    response = client.get("/health")
    assert response.json() == {"status": "ok"}


def test_health_content_type_is_json(client) -> None:
    response = client.get("/health")
    assert response.headers["content-type"].startswith("application/json")


def test_health_method_not_allowed(client) -> None:
    response = client.post("/health", json={})
    assert response.status_code == 405
