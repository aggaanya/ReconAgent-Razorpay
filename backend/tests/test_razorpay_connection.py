"""Razorpay connection verification: service probe + /connection endpoint.

Every HTTP interaction is served by ``httpx.MockTransport`` — no test here
touches the live Razorpay API. Credential-leakage assertions accompany every
failure-path check.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import razorpay as razorpay_api
from app.core.config import get_settings
from app.integrations.razorpay import (
    RazorpayAuthenticationError,
    RazorpayClient,
    RazorpayConnectionError,
    RazorpayError,
    RazorpayInvalidResponseError,
    RazorpayRateLimitError,
    RazorpayServerError,
    RazorpayService,
    RazorpayValidationError,
)
from app.main import app

DUMMY_KEY_ID = "rzp_test_dummykey12345"
DUMMY_KEY_SECRET = "dummy-secret-value"
PROBE_URL = "https://api.razorpay.test/v1"


def make_probe_service(
    handler,
    *,
    key_id: str = DUMMY_KEY_ID,
    max_retries: int = 0,
) -> RazorpayService:
    client = RazorpayClient(
        key_id=key_id,
        key_secret=DUMMY_KEY_SECRET,
        base_url=PROBE_URL,
        timeout_seconds=5.0,
        max_retries=max_retries,
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )
    return RazorpayService(client)


def ok_envelope(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"entity": "collection", "count": 0, "items": []})


class TestServiceVerifyConnection:
    def test_success_returns_connected_with_test_mode(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return ok_envelope(request)

        probe = make_probe_service(handler).verify_connection()
        assert probe.connected is True
        assert probe.environment == "test"
        # Smallest authenticated page: one record, zero skip.
        assert "count=1" in seen["url"] and "skip=0" in seen["url"]
        assert seen["url"].startswith(f"{PROBE_URL}/payments")

    def test_live_key_reports_live_environment(self) -> None:
        probe = make_probe_service(
            ok_envelope, key_id="rzp_live_dummykey12345"
        ).verify_connection()
        assert probe.environment == "live"

    def test_unrecognized_key_prefix_is_unknown(self) -> None:
        probe = make_probe_service(ok_envelope, key_id="odd_prefix").verify_connection()
        assert probe.environment == "unknown"

    def test_items_seen_reflects_payload(self) -> None:
        def handler(request):
            return httpx.Response(
                200, json={"count": 1, "items": [{"id": "pay_x", "amount": 5}]}
            )

        assert make_probe_service(handler).verify_connection().items_seen == 1

    @pytest.mark.parametrize(
        ("status", "expected_exc"),
        [
            (401, RazorpayAuthenticationError),
            (429, RazorpayRateLimitError),
            (500, RazorpayServerError),
            (400, RazorpayValidationError),
        ],
    )
    def test_http_failures_propagate_as_typed_errors(self, status, expected_exc):
        def handler(request):
            return httpx.Response(status, json={"error": {"description": "x"}})

        with pytest.raises(expected_exc):
            make_probe_service(handler).verify_connection()

    def test_timeout_propagates_connection_error(self) -> None:
        def handler(request):
            raise httpx.ReadTimeout("too slow")

        with pytest.raises(RazorpayConnectionError, match="timed out"):
            make_probe_service(handler).verify_connection()

    def test_malformed_body_propagates_invalid_response(self) -> None:
        def handler(request):
            return httpx.Response(200, content=b"{not-json")

        with pytest.raises(RazorpayInvalidResponseError):
            make_probe_service(handler).verify_connection()


# --- endpoint --------------------------------------------------------------


def install_service(monkeypatch, handler, *, key_id=DUMMY_KEY_ID) -> None:
    """Point the API at a mock-transport service and matching credentials."""
    monkeypatch.setattr(razorpay_api, "_service", make_probe_service(
        handler, key_id=key_id
    ))
    monkeypatch.setenv("RAZORPAY_KEY_ID", key_id)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", DUMMY_KEY_SECRET)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def api():
    return TestClient(app)


class TestConnectionEndpointSuccess:
    def test_returns_connected_true_for_test_keys(self, api, monkeypatch) -> None:
        install_service(monkeypatch, ok_envelope)
        response = api.get("/api/v1/razorpay/connection")
        assert response.status_code == 200
        body = response.json()
        assert body == {"connected": True, "environment": "test", "error": None}

    def test_live_keys_reported_as_live(self, api, monkeypatch) -> None:
        install_service(
            monkeypatch, ok_envelope, key_id="rzp_live_dummykey12345"
        )
        body = api.get("/api/v1/razorpay/connection").json()
        assert body["environment"] == "live"


class TestConnectionEndpointFailures:
    def assert_sanitized(self, response: httpx.Response) -> None:
        text = response.text
        assert DUMMY_KEY_SECRET not in text
        assert DUMMY_KEY_ID not in text
        assert "Authorization" not in text
        assert "Basic" not in text

    def test_invalid_credentials_structured_502(self, api, monkeypatch) -> None:
        def handler(request):
            return httpx.Response(
                401, json={"error": {"code": "BAD_REQUEST_ERROR",
                                     "description": "authentication failed"}}
            )

        install_service(monkeypatch, handler)
        response = api.get("/api/v1/razorpay/connection")
        assert response.status_code == 502
        body = response.json()
        assert body["connected"] is False
        assert body["error"]["code"] == "authentication_failed"
        self.assert_sanitized(response)

    def test_rate_limit_structured_503(self, api, monkeypatch) -> None:
        def handler(request):
            return httpx.Response(429, json={"error": {}})

        install_service(monkeypatch, handler)
        response = api.get("/api/v1/razorpay/connection")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "rate_limited"
        self.assert_sanitized(response)

    def test_upstream_5xx_structured_502(self, api, monkeypatch) -> None:
        def handler(request):
            return httpx.Response(503, json={"error": {}})

        install_service(monkeypatch, handler)
        response = api.get("/api/v1/razorpay/connection")
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "upstream_error"
        self.assert_sanitized(response)

    def test_other_4xx_structured_502(self, api, monkeypatch) -> None:
        def handler(request):
            return httpx.Response(400, json={"error": {}})

        install_service(monkeypatch, handler)
        response = api.get("/api/v1/razorpay/connection")
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "upstream_rejected"
        self.assert_sanitized(response)

    @pytest.mark.parametrize(
        "transport_exc",
        [httpx.ReadTimeout("read timed out"), httpx.ConnectError("refused")],
    )
    def test_timeout_and_network_failure_structured_504(
        self, api, monkeypatch, transport_exc
    ) -> None:
        def handler(request):
            raise transport_exc

        install_service(monkeypatch, handler)
        response = api.get("/api/v1/razorpay/connection")
        assert response.status_code == 504
        body = response.json()
        assert body["connected"] is False
        assert body["error"]["code"] == "unreachable"
        self.assert_sanitized(response)

    def test_malformed_response_structured_502(self, api, monkeypatch) -> None:
        def handler(request):
            return httpx.Response(200, content=b"{not-json")

        install_service(monkeypatch, handler)
        response = api.get("/api/v1/razorpay/connection")
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "invalid_response"

    def test_missing_credentials_structured_503(self, api, monkeypatch) -> None:
        monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
        monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
        get_settings.cache_clear()
        response = api.get("/api/v1/razorpay/connection")
        assert response.status_code == 503
        body = response.json()
        assert body == {
            "connected": False,
            "environment": "unknown",
            "error": {
                "code": "not_configured",
                "message": "Razorpay credentials are not configured",
            },
        }
        self.assert_sanitized(response)


class TestOpenApiDocumentation:
    def test_connection_path_documented(self, api) -> None:
        schema = api.get("/openapi.json").json()
        path = schema["paths"]["/api/v1/razorpay/connection"]
        assert "get" in path
        documented = set(path["get"]["responses"].keys())
        assert {"200", "502", "503", "504"} <= documented

    def test_response_model_in_components(self, api) -> None:
        schema = api.get("/openapi.json").json()
        components = ", ".join(schema["components"]["schemas"].keys())
        assert "ConnectionStatusResponse" in components
        assert "ConnectionErrorDetail" in components
