"""Settlement ingestion retrieval: service + GET /api/v1/razorpay/settlements.

Every Razorpay interaction is served by ``httpx.MockTransport`` — no test
touches the live API. Failure-path assertions include credential-leakage
checks; success assertions verify the normalized (provider-independent)
response shape with exact minor-unit money and the bank UTR join key.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.razorpay import get_razorpay_service
from app.core.config import get_settings
from app.integrations.razorpay import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    MAX_SKIP,
    RazorpayAuthenticationError,
    RazorpayClient,
    RazorpayError,
    RazorpayRateLimitError,
    RazorpayService,
)
from app.main import app

DUMMY_KEY_ID = "rzp_test_dummykey12345"
DUMMY_KEY_SECRET = "dummy-secret-value"
PROBE_URL = "https://api.razorpay.test/v1"

CREATED_AT_EPOCH = 1755993600  # 2025-08-24T00:00:00Z


def settlement_payload(sid="setl_dummy0001", **overrides):
    payload = {
        "id": sid,
        "entity": "settlement",
        "amount": 9_986_400,
        "currency": "INR",
        "status": "processed",
        "fees": 5900,
        "tax": 900,
        "utr": "UTRDUMMY12345678",
        "created_at": CREATED_AT_EPOCH,
    }
    payload.update(overrides)
    return payload


def collection(*items):
    return {"entity": "collection", "count": len(items), "items": list(items)}


def make_service(
    handler,
    *,
    key_id=DUMMY_KEY_ID,
    max_retries=0,
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


# --- service layer ----------------------------------------------------------


class TestListSettlementsService:
    def test_successful_page_normalized(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json=collection(settlement_payload()))

        result = make_service(handler).list_settlements(count=10, skip=0)
        assert len(result.items) == 1
        item = result.items[0]
        assert item.provider == "razorpay"
        assert item.external_id == "setl_dummy0001"
        assert item.amount_minor == 9_986_400
        assert item.status == "processed"
        assert item.fees_minor == 5900
        assert item.tax_minor == 900
        assert item.currency == "INR"
        assert item.utr == "UTRDUMMY12345678"
        assert item.created_at is not None and item.created_at.year == 2025
        # Raw wire fields beyond the curated schema must be dropped.
        assert "remarks" not in item.model_dump()

    def test_pagination_params_passed_through(self) -> None:
        seen: list[dict[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(dict(request.url.params))
            return httpx.Response(200, json=collection())

        service = make_service(handler)
        service.list_settlements(
            count=25, skip=75, from_epoch=1755907200, to_epoch=1755993600
        )
        assert seen[0] == {
            "count": "25", "skip": "75",
            "from": "1755907200", "to": "1755993600",
        }

    def test_sequential_pages_are_independent(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            skip = int(request.url.params["skip"])
            if skip == 0:
                items = [settlement_payload("setl_a"), settlement_payload("setl_b")]
            else:
                items = [settlement_payload("setl_c")]
            return httpx.Response(200, json=collection(*items))

        service = make_service(handler)
        first = service.list_settlements(count=2, skip=0)
        second = service.list_settlements(count=2, skip=2)
        assert [i.external_id for i in first.items] == ["setl_a", "setl_b"]
        assert [i.external_id for i in second.items] == ["setl_c"]

    def test_empty_collection(self) -> None:
        def handler(request):
            return httpx.Response(200, json=collection())

        result = make_service(handler).list_settlements()
        assert result.items == [] and result.count == 0

    def test_missing_fees_tax_default_to_zero(self) -> None:
        payload = settlement_payload()
        del payload["fees"], payload["tax"], payload["utr"], payload["currency"]

        def handler(request):
            return httpx.Response(200, json=collection(payload))

        item = make_service(handler).list_settlements().items[0]
        assert item.fees_minor == 0 and item.tax_minor == 0
        assert item.utr is None and item.currency is None

    @pytest.mark.parametrize(
        ("status", "expected_exc"),
        [(401, RazorpayAuthenticationError),
         (403, RazorpayAuthenticationError),
         (429, RazorpayRateLimitError)],
    )
    def test_typed_failures_propagate(self, status, expected_exc):
        def handler(request):
            return httpx.Response(status, json={"error": {"description": "x"}})

        with pytest.raises(expected_exc):
            make_service(handler).list_settlements()

    def test_out_of_bounds_skip_rejected_locally(self) -> None:
        def handler(request):
            return httpx.Response(200, json=collection())

        with pytest.raises(RazorpayError):
            make_service(handler).list_settlements(skip=MAX_SKIP + 1)


# --- endpoint ---------------------------------------------------------------


@pytest.fixture
def api():
    return TestClient(app)


@pytest.fixture
def install_service():
    """Route requests at a mock-transport service via dependency override."""
    def _install(service: RazorpayService) -> None:
        app.dependency_overrides[get_razorpay_service] = lambda: service
    yield _install
    app.dependency_overrides.pop(get_razorpay_service, None)


class TestSettlementsEndpointSuccess:
    def test_returns_normalized_settlements(self, api, install_service) -> None:
        install_service(make_service(
            lambda request: httpx.Response(200, json=collection(settlement_payload()))
        ))
        response = api.get("/api/v1/razorpay/settlements")
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 1 and body["limit"] == DEFAULT_PAGE_SIZE
        item = body["items"][0]
        for field in ("external_id", "amount_minor", "status", "fees_minor",
                      "tax_minor", "utr", "currency", "created_at"):
            assert field in item
        assert item["amount_minor"] == 9_986_400
        assert item["utr"] == "UTRDUMMY12345678"
        assert item["created_at"].startswith("2025-08-24T00:00:00")
        assert item["provider"] == "razorpay"

    def test_query_filters_forwarded_to_provider(
        self, api, install_service
    ) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(request.url.params))
            return httpx.Response(200, json=collection())

        install_service(make_service(handler))
        response = api.get(
            "/api/v1/razorpay/settlements"
            "?from=1755907200&to=1755993600&count=50&skip=100"
        )
        assert response.status_code == 200
        assert seen == {"count": "50", "skip": "100",
                        "from": "1755907200", "to": "1755993600"}

    def test_empty_collection_returns_empty_items(
        self, api, install_service
    ) -> None:
        install_service(make_service(
            lambda request: httpx.Response(200, json=collection())
        ))
        body = api.get("/api/v1/razorpay/settlements").json()
        assert body == {"items": [], "count": 0, "limit": DEFAULT_PAGE_SIZE,
                        "offset": 0}


class TestSettlementsEndpointFailures:
    def assert_sanitized(self, response: httpx.Response) -> None:
        text = response.text
        assert DUMMY_KEY_SECRET not in text
        assert DUMMY_KEY_ID not in text
        assert "Authorization" not in text
        assert "Basic" not in text

    @pytest.mark.parametrize("upstream_status", [401, 403])
    def test_authentication_errors_sanitized_502(
        self, api, install_service, upstream_status
    ) -> None:
        def handler(request):
            return httpx.Response(upstream_status, json={
                "error": {"description": "auth failure details"}})

        install_service(make_service(handler))
        response = api.get("/api/v1/razorpay/settlements")
        assert response.status_code == 502
        assert response.json()["detail"] == "Razorpay authentication failed"
        self.assert_sanitized(response)

    def test_rate_limit_structured_503(self, api, install_service) -> None:
        install_service(make_service(
            lambda request: httpx.Response(429, json={"error": {}})
        ))
        response = api.get("/api/v1/razorpay/settlements")
        assert response.status_code == 503
        assert "rate limit" in response.json()["detail"]
        self.assert_sanitized(response)

    def test_server_error_structured_502(self, api, install_service) -> None:
        install_service(make_service(
            lambda request: httpx.Response(500, json={"error": {}})
        ))
        response = api.get("/api/v1/razorpay/settlements")
        assert response.status_code == 502
        assert response.json()["detail"] == "Razorpay service error"
        self.assert_sanitized(response)

    def test_not_found_passthrough_404(self, api, install_service) -> None:
        install_service(make_service(
            lambda request: httpx.Response(404, json={"error": {}})
        ))
        assert api.get("/api/v1/razorpay/settlements").status_code == 404

    def test_upstream_validation_structured_502(
        self, api, install_service
    ) -> None:
        install_service(make_service(
            lambda request: httpx.Response(400, json={"error": {}})
        ))
        response = api.get("/api/v1/razorpay/settlements")
        assert response.status_code == 502
        assert "rejected the request" in response.json()["detail"]
        self.assert_sanitized(response)

    @pytest.mark.parametrize(
        "transport_exc",
        [httpx.ReadTimeout("read timed out"), httpx.ConnectError("refused")],
    )
    def test_network_failures_structured_504(
        self, api, install_service, transport_exc
    ) -> None:
        def handler(request):
            raise transport_exc

        install_service(make_service(handler))
        response = api.get("/api/v1/razorpay/settlements")
        assert response.status_code == 504
        assert response.json()["detail"] == "Could not reach Razorpay"
        self.assert_sanitized(response)

    def test_malformed_json_structured_502(self, api, install_service) -> None:
        install_service(make_service(
            lambda request: httpx.Response(200, content=b"{not-json")
        ))
        response = api.get("/api/v1/razorpay/settlements")
        assert response.status_code == 502
        assert response.json()["detail"] == "Unexpected Razorpay response"
        self.assert_sanitized(response)

    def test_wrong_shape_payload_structured_502(
        self, api, install_service
    ) -> None:
        install_service(make_service(
            lambda request: httpx.Response(200, json={"unexpected": True})
        ))
        response = api.get("/api/v1/razorpay/settlements")
        assert response.status_code == 502

    def test_missing_credentials_structured_503(
        self, api, monkeypatch
    ) -> None:
        monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
        monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
        get_settings.cache_clear()
        try:
            response = api.get("/api/v1/razorpay/settlements")
        finally:
            get_settings.cache_clear()
        assert response.status_code == 503
        assert "not configured" in response.json()["detail"]
        self.assert_sanitized(response)


class TestSettlementsParameterValidation:
    @pytest.mark.parametrize(
        "query",
        [
            "?count=0",
            f"?count={MAX_PAGE_SIZE + 1}",
            "?skip=-1",
            f"?skip={MAX_SKIP + 1}",
            "?from=946684799",  # one second below EPOCH_MIN
            "?to=4765046401",   # one second above EPOCH_MAX
            "?from=1755993600&to=1755907200",  # inverted range
        ],
    )
    def test_invalid_queries_yield_422_without_upstream_call(
        self, api, install_service, query
    ) -> None:
        called = False

        def handler(request):
            nonlocal called
            called = True
            return httpx.Response(200, json=collection())

        install_service(make_service(handler))
        response = api.get(f"/api/v1/razorpay/settlements{query}")
        assert response.status_code == 422
        assert called is False


class TestSettlementsOpenApi:
    def test_path_and_failure_responses_documented(self, api) -> None:
        schema = api.get("/openapi.json").json()
        entry = schema["paths"]["/api/v1/razorpay/settlements"]["get"]
        documented = set(entry["responses"].keys())
        assert {"200", "422", "502", "503", "504"} <= documented
        params = {p["name"] for p in entry["parameters"]}
        assert {"count", "skip", "from", "to"} <= params

    def test_response_schema_exposes_no_credential_fields(self, api) -> None:
        schema = api.get("/openapi.json").json()
        props = schema["components"]["schemas"]["NormalizedSettlement"][
            "properties"
        ].keys()
        assert "key_id" not in props and "secret" not in props
