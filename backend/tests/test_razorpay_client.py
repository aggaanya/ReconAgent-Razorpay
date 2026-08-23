"""RazorpayClient behaviour against mocked HTTP transports.

No test here touches the real Razorpay API: every request is served by
``httpx.MockTransport`` and backoff sleeps are captured, never performed.
"""

import base64
import json

import httpx
import pytest

from app.integrations.razorpay import (
    RETRY_MAX_DELAY_SECONDS,
    RazorpayAuthenticationError,
    RazorpayClient,
    RazorpayConnectionError,
    RazorpayError,
    RazorpayInvalidResponseError,
    RazorpayNotFoundError,
    RazorpayRateLimitError,
    RazorpayServerError,
    RazorpayValidationError,
)

DUMMY_KEY_ID = "rzp_test_dummy_key"
DUMMY_KEY_SECRET = "dummy-secret-value"
TEST_BASE_URL = "https://api.razorpay.test/v1"


def counting_handler(responder):
    """Wrap a responder so each call is counted and requests captured."""
    state = {"calls": 0, "requests": []}

    def wrapped(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        state["requests"].append(request)
        return responder(request)

    wrapped.state = state
    return wrapped


def make_client(
    handler,
    *,
    max_retries: int = 2,
    sleep=None,
    timeout_seconds: float = 5.0,
    retry_base_delay_seconds: float | None = None,
    retry_max_delay_seconds: float | None = None,
    jitter=None,
) -> RazorpayClient:
    """Build a client wired to a mock transport and a no-op sleeper."""
    kwargs: dict[str, object] = {}
    if retry_base_delay_seconds is not None:
        kwargs["retry_base_delay_seconds"] = retry_base_delay_seconds
    if retry_max_delay_seconds is not None:
        kwargs["retry_max_delay_seconds"] = retry_max_delay_seconds
    return RazorpayClient(
        key_id=DUMMY_KEY_ID,
        key_secret=DUMMY_KEY_SECRET,
        base_url=TEST_BASE_URL,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        jitter=jitter,
        transport=httpx.MockTransport(handler),
        sleep=sleep if sleep is not None else (lambda _seconds: None),
        **kwargs,
    )


class TestSuccessfulRequests:
    def test_get_returns_parsed_json(self) -> None:
        handler = counting_handler(
            lambda request: httpx.Response(
                200, json={"id": "pay_123", "status": "captured"}
            )
        )
        with make_client(handler) as client:
            body = client.get("/payments/pay_123")
        assert body == {"id": "pay_123", "status": "captured"}
        assert handler.state["calls"] == 1

    def test_url_join_and_query_params(self) -> None:
        handler = counting_handler(
            lambda request: httpx.Response(200, json={"items": []})
        )
        with make_client(handler) as client:
            client.get("payments", params={"count": 10, "skip": 5})
        request = handler.state["requests"][0]
        assert str(request.url) == f"{TEST_BASE_URL}/payments?count=10&skip=5"
        assert request.url.params["count"] == "10"
        assert request.url.params["skip"] == "5"

    def test_empty_success_body_returns_none(self) -> None:
        handler = counting_handler(lambda request: httpx.Response(204))
        with make_client(handler) as client:
            assert client.delete("/payments/pay_123") is None


class TestAuthentication:
    def test_basic_auth_header_generated_by_library(self) -> None:
        handler = counting_handler(lambda request: httpx.Response(200, json={}))
        with make_client(handler) as client:
            client.get("/payments")
        request = handler.state["requests"][0]
        scheme, _, encoded = request.headers["Authorization"].partition(" ")
        assert scheme == "Basic"
        decoded = base64.b64decode(encoded).decode()
        assert decoded == f"{DUMMY_KEY_ID}:{DUMMY_KEY_SECRET}"

    @pytest.mark.parametrize("status", [401, 403])
    def test_permanent_authentication_errors_are_never_retried(
        self, status
    ) -> None:
        """Auth failures are permanent: exactly one attempt, no sleeps."""
        handler = counting_handler(
            lambda request, s=status: httpx.Response(s, json={"error": {}})
        )
        sleeps: list[float] = []
        with make_client(handler, max_retries=5, sleep=sleeps.append) as client:
            with pytest.raises(RazorpayAuthenticationError):
                client.get("/payments")
        assert handler.state["calls"] == 1
        assert sleeps == []


class TestErrorMapping:
    @pytest.mark.parametrize("status", [401, 403])
    def test_authentication_errors(self, status) -> None:
        handler = counting_handler(
            lambda request, s=status: httpx.Response(
                s,
                json={
                    "error": {
                        "code": "BAD_REQUEST_ERROR",
                        "description": "authentication failed",
                    }
                },
            )
        )
        with make_client(handler) as client:
            with pytest.raises(RazorpayAuthenticationError) as exc_info:
                client.get("/payments")
        assert exc_info.value.status_code == status

    def test_validation_error_preserves_razorpay_detail(self) -> None:
        payload = {
            "error": {
                "code": "BAD_REQUEST_ERROR",
                "description": "amount is required",
                "field": "amount",
                "source": "business",
                "step": "payment_initiation",
                "reason": "input_validation_failed",
                "metadata": {"order_id": "order_x"},
            }
        }
        handler = counting_handler(lambda request: httpx.Response(400, json=payload))
        with make_client(handler) as client:
            with pytest.raises(RazorpayValidationError) as exc_info:
                client.get("/payments")
        exc = exc_info.value
        assert exc.status_code == 400
        assert exc.detail.code == "BAD_REQUEST_ERROR"
        assert exc.detail.description == "amount is required"
        assert exc.detail.field == "amount"
        assert exc.detail.source == "business"
        assert exc.detail.step == "payment_initiation"
        assert exc.detail.reason == "input_validation_failed"
        assert exc.detail.metadata == {"order_id": "order_x"}

    @pytest.mark.parametrize("status", [400, 422])
    def test_validation_statuses(self, status) -> None:
        handler = counting_handler(
            lambda request, s=status: httpx.Response(s, json={"error": {}})
        )
        with make_client(handler) as client:
            with pytest.raises(RazorpayValidationError):
                client.post("/payments", json={})

    def test_not_found_error(self) -> None:
        handler = counting_handler(
            lambda request: httpx.Response(
                404,
                json={
                    "error": {
                        "code": "BAD_URL",
                        "description": "The requested URL does not exist",
                    }
                },
            )
        )
        with make_client(handler) as client:
            with pytest.raises(RazorpayNotFoundError) as exc_info:
                client.get("/payments/pay_missing")
        assert exc_info.value.detail.code == "BAD_URL"

    def test_unmapped_client_error_is_single_attempt(self) -> None:
        handler = counting_handler(
            lambda request: httpx.Response(409, json={"error": {}})
        )
        with make_client(handler, max_retries=2) as client:
            with pytest.raises(RazorpayError) as exc_info:
                client.post("/payments", json={})
        assert handler.state["calls"] == 1
        assert exc_info.value.status_code == 409

    def test_error_body_with_html_still_maps(self) -> None:
        handler = counting_handler(
            lambda request: httpx.Response(500, content=b"<html>boom</html>")
        )
        with make_client(handler, max_retries=0) as client:
            with pytest.raises(RazorpayServerError):
                client.get("/payments")

    def test_malformed_json_on_success_is_typed_error(self) -> None:
        handler = counting_handler(
            lambda request: httpx.Response(200, content=b"{not-json")
        )
        with make_client(handler) as client:
            with pytest.raises(
                RazorpayInvalidResponseError, match="malformed JSON"
            ) as exc_info:
                client.get("/payments")
        # Still part of the general integration error hierarchy.
        assert isinstance(exc_info.value, RazorpayError)


class TestRetryBehavior:
    def test_retry_then_success(self) -> None:
        responses = iter(
            [
                httpx.Response(500, json={"error": {}}),
                httpx.Response(200, json={"items": ["ok"]}),
            ]
        )

        def handler(request):
            return next(responses)

        sleeps: list[float] = []
        with make_client(handler, max_retries=2, sleep=sleeps.append) as client:
            assert client.get("/payments") == {"items": ["ok"]}
        assert sleeps == [0.5]

    def test_backoff_steps_grow_exponentially_and_are_capped(self) -> None:
        handler = counting_handler(
            lambda request: httpx.Response(429, json={"error": {}})
        )
        sleeps: list[float] = []
        with make_client(handler, max_retries=8, sleep=sleeps.append) as client:
            with pytest.raises(RazorpayRateLimitError):
                client.get("/payments")
        assert len(sleeps) == 8
        assert sleeps[0] == 0.5
        assert sleeps[1] == 1.0
        assert sleeps[2] == 2.0
        assert all(delay <= RETRY_MAX_DELAY_SECONDS for delay in sleeps)
        assert sleeps[-1] == RETRY_MAX_DELAY_SECONDS

    def test_persistent_429_is_bounded(self) -> None:
        handler = counting_handler(
            lambda request: httpx.Response(429, json={"error": {}})
        )
        with make_client(handler, max_retries=2) as client:
            with pytest.raises(RazorpayRateLimitError) as exc_info:
                client.get("/payments")
        assert handler.state["calls"] == 3  # initial + 2 retries, never unbounded
        assert exc_info.value.status_code == 429

    def test_persistent_500_is_bounded(self) -> None:
        handler = counting_handler(
            lambda request: httpx.Response(503, json={"error": {}})
        )
        with make_client(handler, max_retries=2) as client:
            with pytest.raises(RazorpayServerError) as exc_info:
                client.get("/settlements")
        assert handler.state["calls"] == 3
        assert exc_info.value.status_code == 503

    def test_numeric_retry_after_is_respected_and_capped(self) -> None:
        handler = counting_handler(
            lambda request: httpx.Response(
                429, headers={"Retry-After": "999999"}, json={}
            )
        )
        sleeps: list[float] = []
        with make_client(handler, max_retries=1, sleep=sleeps.append) as client:
            with pytest.raises(RazorpayRateLimitError):
                client.get("/payments")
        assert sleeps == [RETRY_MAX_DELAY_SECONDS]

    def test_non_numeric_retry_after_falls_back_to_backoff(self) -> None:
        handler = counting_handler(
            lambda request: httpx.Response(
                429, headers={"Retry-After": "soon"}, json={}
            )
        )
        sleeps: list[float] = []
        with make_client(handler, max_retries=1, sleep=sleeps.append) as client:
            with pytest.raises(RazorpayRateLimitError):
                client.get("/payments")
        assert sleeps == [0.5]

    def test_writes_are_never_retried(self) -> None:
        handler = counting_handler(
            lambda request: httpx.Response(500, json={"error": {}})
        )
        sleeps: list[float] = []
        with make_client(handler, max_retries=3, sleep=sleeps.append) as client:
            with pytest.raises(RazorpayServerError):
                client.post("/payments", json={"amount": 100})
        assert handler.state["calls"] == 1
        assert sleeps == []

    def test_zero_max_retries_means_single_attempt(self) -> None:
        handler = counting_handler(
            lambda request: httpx.Response(429, json={"error": {}})
        )
        sleeps: list[float] = []
        with make_client(handler, max_retries=0, sleep=sleeps.append) as client:
            with pytest.raises(RazorpayRateLimitError):
                client.get("/payments")
        assert handler.state["calls"] == 1
        assert sleeps == []


class TestNetworkFailures:
    @pytest.mark.parametrize(
        ("transport_exc", "match"),
        [
            (httpx.ReadTimeout("read timed out"), "timed out"),
            (httpx.ConnectTimeout("connect timed out"), "timed out"),
            (httpx.ConnectError("connection refused"), "connection failed"),
        ],
    )
    def test_transient_network_errors_retried_then_typed_error(
        self, transport_exc, match
    ) -> None:
        """Network errors are transient: retried on reads within the same
        bounded budget as 429/5xx, then surfaced as a connection error."""
        handler = counting_handler(lambda request: (_ for _ in ()).throw(transport_exc))

        sleeps: list[float] = []
        with make_client(
            handler, max_retries=3, sleep=sleeps.append
        ) as client:
            with pytest.raises(RazorpayConnectionError, match=match):
                client.get("/payments")
        assert handler.state["calls"] == 4  # initial + 3 retries, never unbounded
        assert len(sleeps) == 3
        assert all(delay >= 0 for delay in sleeps)

    def test_timeout_once_then_success_is_recovered(self) -> None:
        outcomes = iter(["timeout", "ok"])

        def responder(request):
            if next(outcomes) == "timeout":
                raise httpx.ReadTimeout("blip")
            return httpx.Response(200, json={"items": ["recovered"]})

        handler = counting_handler(responder)
        sleeps: list[float] = []
        with make_client(handler, max_retries=2, sleep=sleeps.append) as client:
            assert client.get("/payments") == {"items": ["recovered"]}
        assert handler.state["calls"] == 2 and sleeps == [0.5]

    def test_writes_never_retry_network_errors(self) -> None:
        handler = counting_handler(
            lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused"))
        )

        sleeps: list[float] = []
        with make_client(handler, max_retries=3, sleep=sleeps.append) as client:
            with pytest.raises(RazorpayConnectionError):
                client.post("/payments", json={"amount": 1})
        assert handler.state["calls"] == 1 and sleeps == []

    def test_timeout_message_reports_configured_timeout(self) -> None:
        def handler(request):
            raise httpx.ReadTimeout("too slow")

        with make_client(
            handler, max_retries=0, timeout_seconds=7.5
        ) as client:
            with pytest.raises(RazorpayConnectionError, match="7\\.5"):
                client.get("/payments")


class TestJitter:
    def test_jitter_output_used_as_delay_within_backoff_bounds(self) -> None:
        handler = counting_handler(
            lambda request: httpx.Response(500, json={"error": {}})
        )
        requested_backoffs: list[float] = []

        def deterministic_jitter(backoff: float) -> float:
            requested_backoffs.append(backoff)
            return backoff / 2  # always inside [0, backoff]

        sleeps: list[float] = []
        with make_client(
            handler,
            max_retries=3,
            sleep=sleeps.append,
            jitter=deterministic_jitter,
        ) as client:
            with pytest.raises(RazorpayServerError):
                client.get("/payments")
        assert sleeps == [b / 2 for b in requested_backoffs]
        # Stepped backoff sequence 0.5, 1.0, 2.0 was handed to jitter.
        assert requested_backoffs == [0.5, 1.0, 2.0]
        assert all(0 <= delay <= backoff
                   for delay, backoff in zip(sleeps, requested_backoffs))

    def test_pathological_jitter_is_clamped(self) -> None:
        """Negative or oversized jitter output can never break the loop:
        delays stay in [0, max_delay]."""
        flip = True

        def pathological_jitter(backoff: float) -> float:
            nonlocal flip
            flip = not flip
            return -5.0 if flip else 10_000.0

        handler = counting_handler(
            lambda request: httpx.Response(429, json={"error": {}})
        )
        sleeps: list[float] = []
        max_cap = 8.0
        with make_client(
            handler,
            max_retries=4,
            retry_max_delay_seconds=max_cap,
            sleep=sleeps.append,
            jitter=pathological_jitter,
        ) as client:
            with pytest.raises(RazorpayRateLimitError):
                client.get("/payments")
        assert sleeps == [max_cap, 0.0, max_cap, 0.0]
        assert all(0 <= delay <= max_cap for delay in sleeps)

    def test_server_provided_retry_after_bypasses_jitter(self) -> None:
        handler = counting_handler(
            lambda request: httpx.Response(
                429, headers={"Retry-After": "3"}, json={}
            )
        )
        sleeps: list[float] = []

        def loud_jitter(backoff: float) -> float:
            raise AssertionError("jitter must not run for Retry-After delays")

        with make_client(
            handler, max_retries=1, sleep=sleeps.append, jitter=loud_jitter
        ) as client:
            with pytest.raises(RazorpayRateLimitError):
                client.get("/payments")
        assert sleeps == [3.0]


class TestStructuredLogs:
    """Client logs are JSON lines with operational fields — and never any
    credential material or query strings."""

    @pytest.fixture
    def captured_logs(self, caplog):
        import logging as _logging

        records: list[str] = []

        class Capture(_logging.Handler):
            def emit(self, record: _logging.LogRecord) -> None:
                records.append(record.getMessage())

        logger = _logging.getLogger("app.integrations.razorpay.client")
        # Save and restore the full logger state: global logging
        # reconfiguration (e.g. Alembic's fileConfig in migration tests)
        # may have disabled this logger or raised its level.
        state = (logger.disabled, logger.level)
        logger.disabled = False
        logger.setLevel(_logging.DEBUG)
        handler = Capture()
        logger.addHandler(handler)
        yield records
        logger.removeHandler(handler)
        logger.disabled, logger.level = state

    def test_response_and_retry_lines_are_structured_json(
        self, captured_logs
    ) -> None:
        responses = iter(
            [
                httpx.Response(503, json={"error": {}}),
                httpx.Response(200, json={"items": []}),
            ]
        )

        def handler(request):
            return next(responses)

        with make_client(
            handler, max_retries=2
        ) as client:
            client.get("/payments?count=10&skip=0")

        events = [json.loads(line) for line in captured_logs]
        by_event = {e["event"]: e for e in events}
        retry_line = by_event["retry_scheduled"]
        response_line = by_event["http_response"]

        for line in (retry_line, response_line):
            assert line["method"] == "GET"
            assert line["path"] == "payments"  # endpoint only, no query string
            assert isinstance(line["correlation_id"], str) and line["correlation_id"]
            assert isinstance(line["duration_ms"], int | float)

        assert retry_line["status"] == 503
        assert retry_line["attempt"] == 1 and retry_line["max_retries"] == 2
        assert retry_line["delay_seconds"] == 0.5
        assert response_line["status"] == 200 and response_line["attempt"] == 2
        # One correlation id ties the whole logical request together.
        assert retry_line["correlation_id"] == response_line["correlation_id"]

    def test_no_credentials_or_authorization_in_any_log_line(
        self, captured_logs
    ) -> None:
        def handler(request):
            return httpx.Response(401, json={"error": {"description": "x"}})

        with make_client(handler, max_retries=0) as client:
            with pytest.raises(RazorpayAuthenticationError):
                client.get("/payments")
        text = "\n".join(captured_logs)
        assert DUMMY_KEY_SECRET not in text
        assert DUMMY_KEY_ID not in text
        assert "Authorization" not in text
        assert "Basic" not in text


class TestAllVerbsSupported:
    @pytest.mark.parametrize(
        ("call", "method"),
        [
            (lambda c: c.get("/x"), "GET"),
            (lambda c: c.post("/x", json={"a": 1}), "POST"),
            (lambda c: c.put("/x", json={"a": 1}), "PUT"),
            (lambda c: c.patch("/x", json={"a": 1}), "PATCH"),
            (lambda c: c.delete("/x"), "DELETE"),
        ],
    )
    def test_method_forwarding(self, call, method) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            return httpx.Response(200, json={})

        with make_client(handler) as client:
            call(client)
        assert seen["method"] == method

    def test_json_body_round_trips(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(201, json={"ok": True})

        with make_client(handler) as client:
            assert client.post("/orders", json={"amount": 500}) == {"ok": True}
        assert captured["body"] == {"amount": 500}


class TestCredentialLeakage:
    """Credentials must never surface in messages or representations."""

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
    def test_http_errors_do_not_leak_credentials(self, status) -> None:
        def handler(request):
            return httpx.Response(status, json={"error": {"description": "oops"}})

        with make_client(handler, max_retries=0) as client:
            with pytest.raises(RazorpayError) as exc_info:
                client.get("/payments")
        message = str(exc_info.value)
        assert DUMMY_KEY_SECRET not in message
        assert DUMMY_KEY_ID not in message
        assert "Authorization" not in message

    def test_network_errors_do_not_leak_credentials(self) -> None:
        def handler(request):
            raise httpx.ConnectError("refused")

        with make_client(handler) as client:
            with pytest.raises(RazorpayConnectionError) as exc_info:
                client.get("/payments")
        message = str(exc_info.value)
        assert DUMMY_KEY_SECRET not in message
        assert DUMMY_KEY_ID not in message


class TestSettingsConstruction:
    def test_from_settings_builds_configured_client(
        self, monkeypatch
    ) -> None:
        from app.core.config import Settings

        monkeypatch.setenv("RAZORPAY_KEY_ID", DUMMY_KEY_ID)
        monkeypatch.setenv("RAZORPAY_KEY_SECRET", DUMMY_KEY_SECRET)
        monkeypatch.setenv("RAZORPAY_TIMEOUT_SECONDS", "4")
        monkeypatch.setenv("RAZORPAY_MAX_RETRIES", "1")
        settings = Settings(_env_file=None)
        client = RazorpayClient.from_settings(settings)
        try:
            assert client.base_url == "https://api.razorpay.com/v1"
            assert client.max_retries == 1
        finally:
            client.close()

    def test_from_settings_without_credentials_fails_clearly(
        self, monkeypatch
    ) -> None:
        from app.core.config import Settings

        for name in (
            "RAZORPAY_KEY_ID",
            "RAZORPAY_KEY_SECRET",
        ):
            monkeypatch.delenv(name, raising=False)
        settings = Settings(_env_file=None)
        with pytest.raises(RazorpayError, match="not configured"):
            RazorpayClient.from_settings(settings)

    def test_constructor_rejects_blank_credentials(self) -> None:
        with pytest.raises(RazorpayError, match="credentials are required"):
            RazorpayClient(
                key_id="",
                key_secret=DUMMY_KEY_SECRET,
                base_url=TEST_BASE_URL,
                timeout_seconds=5.0,
            )

    def test_backoff_and_jitter_settings_flow_into_client(
        self, monkeypatch
    ) -> None:
        import random

        from app.core.config import Settings

        monkeypatch.setenv("RAZORPAY_KEY_ID", DUMMY_KEY_ID)
        monkeypatch.setenv("RAZORPAY_KEY_SECRET", DUMMY_KEY_SECRET)
        monkeypatch.setenv("RAZORPAY_RETRY_BASE_DELAY_SECONDS", "1.25")
        monkeypatch.setenv("RAZORPAY_RETRY_MAX_DELAY_SECONDS", "9")
        settings = Settings(_env_file=None)
        client = RazorpayClient.from_settings(settings)
        try:
            assert client.retry_base_delay == 1.25
            assert client.retry_max_delay == 9
            assert client.max_retries == settings.razorpay_max_retries
            assert client._jitter is random.uniform  # jitter on by default
        finally:
            client.close()

    def test_jitter_can_be_disabled_via_settings(self, monkeypatch) -> None:
        from app.core.config import Settings

        monkeypatch.setenv("RAZORPAY_KEY_ID", DUMMY_KEY_ID)
        monkeypatch.setenv("RAZORPAY_KEY_SECRET", DUMMY_KEY_SECRET)
        monkeypatch.setenv("RAZORPAY_RETRY_JITTER", "false")
        client = RazorpayClient.from_settings(Settings(_env_file=None))
        try:
            assert client._jitter is None
        finally:
            client.close()

    def test_max_delay_below_base_delay_is_rejected(self, monkeypatch) -> None:
        from app.core.config import Settings

        monkeypatch.setenv("RAZORPAY_RETRY_MAX_DELAY_SECONDS", "0.1")
        with pytest.raises(Exception, match="greater than or equal"):
            Settings(_env_file=None)

    def test_constructor_rejects_inverted_delay_window(self) -> None:
        with pytest.raises(ValueError, match="retry_max_delay_seconds"):
            RazorpayClient(
                key_id=DUMMY_KEY_ID,
                key_secret=DUMMY_KEY_SECRET,
                base_url=TEST_BASE_URL,
                timeout_seconds=5.0,
                retry_base_delay_seconds=2.0,
                retry_max_delay_seconds=1.0,
            )
