"""Application startup, docs, and CORS behaviour."""

from fastapi.testclient import TestClient


class TestStartup:
    def test_lifespan_startup_and_shutdown(self, client: TestClient) -> None:
        # The `client` fixture enters/exits the lifespan context; reaching an
        # assertion here proves startup completed without errors.
        response = client.get("/health")
        assert response.status_code == 200

    def test_create_app_is_idempotent(self) -> None:
        from app.main import create_app

        app = create_app()
        assert app.title == "ReconAgent API"


class TestOpenAPIDocs:
    def test_openapi_schema_served(self, client: TestClient) -> None:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert schema["info"]["title"] == "ReconAgent API"
        assert "/health" in schema["paths"]
        assert "/readiness" in schema["paths"]

    def test_swagger_ui_served(self, client: TestClient) -> None:
        response = client.get("/docs")
        assert response.status_code == 200

    def test_redoc_served(self, client: TestClient) -> None:
        response = client.get("/redoc")
        assert response.status_code == 200


class TestCors:
    def test_preflight_allows_configured_origin(self, client: TestClient) -> None:
        origin = "http://localhost:5173"
        response = client.options(
            "/health",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin

    def test_actual_request_echoes_configured_origin(self, client: TestClient) -> None:
        response = client.get("/health", headers={"Origin": "http://localhost:5173"})
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"

    def test_unknown_origin_is_not_allowed(self, client: TestClient) -> None:
        response = client.get("/health", headers={"Origin": "http://evil.example"})
        assert "access-control-allow-origin" not in response.headers

    def test_cors_origins_come_from_settings(self) -> None:
        from app.core.config import get_settings

        assert get_settings().cors_origins == ["http://localhost:5173"]
