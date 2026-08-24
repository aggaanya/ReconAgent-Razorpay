"""Finance API tests: /api/v1/finance/* over seeded SQLite storage.

Pins the HTTP contract: response shapes with exact minor-unit integers,
filter validation (clean 422s), 503 when the database is not configured,
and OpenAPI documentation for every route. No provider calls anywhere —
the finance surface reads only local persistence.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.periods import REPORTING_TIMEZONE
from app.db.session import get_db
from app.main import app

UTC = timezone.utc


@pytest.fixture
def finance_client(session_factory):
    """TestClient bound to a fresh SQLite database via get_db override."""
    def _override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client, session_factory
    app.dependency_overrides.pop(get_db, None)
    get_settings.cache_clear()


def seed(session, *, payment=True, refund=False):
    from app.db.mappers import (
        order_from_normalized,
        payment_from_normalized,
        refund_from_normalized,
    )
    from app.db.repositories import (
        OrderRepository,
        PaymentRepository,
        RefundRepository,
    )
    from app.schemas import (
        NormalizedOrder,
        NormalizedPayment,
        NormalizedRefund,
    )

    if payment:
        row = payment_from_normalized(
            NormalizedPayment(
                provider="razorpay",
                external_id="pay_seed000000001",
                amount_minor=500_000,
                currency="INR",
                status="captured",
                method="upi",
                order_id="order_seed0000001",
                created_at=datetime(2026, 3, 10, 12, 0, tzinfo=UTC),
            )
        )
        PaymentRepository(session).upsert(row)
    if refund:
        row = refund_from_normalized(
            NormalizedRefund(
                provider="razorpay",
                external_id="rfnd_seed00000001",
                razorpay_payment_id="pay_seed000000001",
                amount_minor=100_000,
                currency="INR",
                status="processed",
                created_at=datetime(2026, 3, 11, 9, 0, tzinfo=UTC),
            )
        )
        RefundRepository(session).upsert(row)
    row = order_from_normalized(
        NormalizedOrder(
            provider="razorpay",
            external_id="order_seed0000001",
            amount_minor=500_000,
            amount_paid_minor=500_000,
            amount_due_minor=0,
            currency="INR",
            status="paid",
            receipt="rcpt_seed_1",
            created_at=datetime(2026, 3, 10, 8, 0, tzinfo=UTC),
        )
    )
    OrderRepository(session).upsert(row)


class TestSummaryEndpoint:
    def test_summary_shape_and_exact_amounts(self, finance_client):
        client, session_factory = finance_client
        session = session_factory()
        seed(session, refund=True)
        session.commit()

        response = client.get("/api/v1/finance/summary")
        assert response.status_code == 200
        body = response.json()
        assert body["start_date"] is None
        item = body["currencies"][0]
        assert item["currency"] == "INR"
        assert item["gross_amount_minor"] == 500_000
        assert item["transaction_count"] == 1
        assert item["successful_amount_minor"] == 500_000
        assert item["refunded_amount_minor"] == 100_000
        # Exact integer math across the three §3.4 net variants
        # (no single ambiguous "net" field is exposed).
        assert item["net_of_refunds_minor"] == 400_000  # 500_000 - 100_000
        assert item["net_of_fees_minor"] == 500_000  # no fees seeded
        assert item["net_of_refunds_and_fees_minor"] == 400_000

    def test_summary_window_filter(self, finance_client):
        client, session_factory = finance_client
        session = session_factory()
        seed(session, refund=True)
        session.commit()

        day1 = client.get(
            "/api/v1/finance/summary", params={"start": "2026-03-10", "end": "2026-03-10"}
        ).json()
        assert day1["currencies"][0]["refund_count"] == 0

        both = client.get(
            "/api/v1/finance/summary",
            params={"start": "2026-03-10", "end": "2026-03-11"},
        ).json()
        assert both["currencies"][0]["refund_count"] == 1

    def test_invalid_range_is_422_not_500(self, finance_client):
        client, _ = finance_client
        response = client.get(
            "/api/v1/finance/summary", params={"start": "2026-03-11", "end": "2026-03-10"}
        )
        assert response.status_code == 422

    def test_bad_date_format_is_422(self, finance_client):
        client, _ = finance_client
        response = client.get(
            "/api/v1/finance/summary", params={"start": "not-a-date"}
        )
        assert response.status_code == 422


class TestListEndpoints:
    def test_payments_page_contract(self, finance_client):
        client, session_factory = finance_client
        session = session_factory()
        seed(session)
        session.commit()

        response = client.get("/api/v1/finance/payments")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1 and len(body["items"]) == 1
        record = body["items"][0]
        assert record["razorpay_payment_id"] == "pay_seed000000001"
        assert record["amount_minor"] == 500_000
        assert record["notes"] == {}
        assert record["provider_created_at"].startswith("2026-03-10")

    def test_refunds_orders_settlements_pages(self, finance_client):
        client, session_factory = finance_client
        session = session_factory()
        seed(session, refund=True)
        session.commit()

        refunds = client.get("/api/v1/finance/refunds").json()
        assert refunds["total"] == 1
        assert refunds["items"][0]["razorpay_payment_id"] == "pay_seed000000001"

        orders = client.get(
            "/api/v1/finance/orders", params={"receipt": "rcpt_seed_1"}
        ).json()
        assert orders["total"] == 1
        assert orders["items"][0]["amount_due_minor"] == 0

        settlements = client.get("/api/v1/finance/settlements").json()
        assert settlements == {"items": [], "limit": 50, "offset": 0, "total": 0}

    def test_pagination_bounds_validated(self, finance_client):
        client, _ = finance_client
        assert client.get(
            "/api/v1/finance/payments", params={"limit": 501}
        ).status_code == 422
        assert client.get(
            "/api/v1/finance/payments", params={"limit": 0}
        ).status_code == 422
        assert client.get(
            "/api/v1/finance/payments", params={"offset": -1}
        ).status_code == 422

    def test_empty_database_returns_empty_pages_not_errors(self, finance_client):
        client, _ = finance_client
        for path in (
            "/api/v1/finance/payments",
            "/api/v1/finance/refunds",
            "/api/v1/finance/settlements",
            "/api/v1/finance/orders",
        ):
            response = client.get(path)
            assert response.status_code == 200
            assert response.json()["total"] == 0
        summary = client.get("/api/v1/finance/summary")
        assert summary.status_code == 200
        assert summary.json()["currencies"] == []


class TestUnconfiguredDatabase:
    def test_missing_database_url_yields_503(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "")
        get_settings.cache_clear()
        try:
            with TestClient(app) as client:
                response = client.get("/api/v1/finance/summary")
                assert response.status_code == 503
                detail = response.json()["detail"]
                assert isinstance(detail, str) and detail
        finally:
            get_settings.cache_clear()


class TestMetricEndpoints:
    """Deterministic metric routes: /payment-performance, /revenue, /trends.

    Named periods resolve against the reporting clock in IST, so seeds are
    stamped relative to the current IST day (stored as UTC instants).
    """

    @staticmethod
    def _seed_one_per_ist_day(session):
        from app.db.mappers import payment_from_normalized
        from app.db.repositories import PaymentRepository
        from app.schemas import NormalizedPayment

        now_ist = datetime.now(tz=REPORTING_TIMEZONE)
        today_start = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
        # "today" must land inside the half-open [today_start, now) window
        # at ANY hour of day, so stamp it relative to now rather than a
        # fixed 01:00 (which falls outside the window right after midnight).
        stamps = (
            ("today", max(today_start, now_ist - timedelta(minutes=5))),
            ("yesterday", today_start - timedelta(hours=1)),
        )
        for label, when_ist in stamps:
            row = payment_from_normalized(
                NormalizedPayment(
                    provider="razorpay",
                    external_id=f"pay_metric_{label}",
                    amount_minor=400_000,
                    currency="INR",
                    status="captured",
                    method="upi",
                    order_id=f"order_metric_{label}",
                    created_at=when_ist.astimezone(timezone.utc),
                )
            )
            PaymentRepository(session).upsert(row)
        session.commit()

    def test_payment_performance_named_period(self, finance_client):
        client, session_factory = finance_client
        self._seed_one_per_ist_day(session_factory())

        yesterday = client.get(
            "/api/v1/finance/payment-performance", params={"period": "yesterday"}
        ).json()
        assert yesterday["transaction_volume"] == 1
        assert yesterday["successful_transactions"] == 1
        assert yesterday["success_rate"]["value"] == 100.0
        assert yesterday["period"]["timezone"] == "Asia/Kolkata"
        assert yesterday["currency"] == "INR"

        today = client.get(
            "/api/v1/finance/payment-performance", params={"period": "today"}
        ).json()
        assert today["transaction_volume"] == 1

    def test_payment_performance_rejects_unknown_period(self, finance_client):
        client, _ = finance_client
        response = client.get(
            "/api/v1/finance/payment-performance", params={"period": "tomorrow"}
        )
        assert response.status_code == 422

    def test_revenue_endpoint_exact_variants(self, finance_client):
        client, session_factory = finance_client
        self._seed_one_per_ist_day(session_factory())

        body = client.get(
            "/api/v1/finance/revenue", params={"period": "today"}
        ).json()
        assert body["gross_revenue_minor"] == 400_000
        assert body["net_revenue"]["net_of_refunds_minor"] == 400_000
        assert body["net_revenue"]["net_of_fees_minor"] == 400_000
        assert body["refunds"]["refund_count"] == 0
        # Zero gross would be undefined; here gross > 0 with no refunds -> 0%.
        assert body["refunds"]["refund_rate"]["value"] == 0.0

    def test_trend_endpoint_contract_and_validation(self, finance_client):
        client, session_factory = finance_client
        self._seed_one_per_ist_day(session_factory())

        body = client.get(
            "/api/v1/finance/trends/transaction_volume",
            params={"granularity": "day"},
        ).json()
        assert body["metric"] == "transaction_volume"
        assert body["current_period"]["value"] == 1
        assert body["previous_period"]["value"] == 1
        assert body["absolute_change"] == 0
        assert body["percentage_change"] == 0.0
        assert body["change_type"] == "normal"
        assert body["timezone"] == "Asia/Kolkata"

    def test_trend_endpoint_rejects_unknown_metric_and_granularity(
        self, finance_client
    ):
        client, _ = finance_client
        assert client.get("/api/v1/finance/trends/not_a_metric").status_code == 422
        assert client.get(
            "/api/v1/finance/trends/gross_revenue",
            params={"granularity": "quarter"},
        ).status_code == 422

    def test_metric_endpoints_require_database(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "")
        get_settings.cache_clear()
        try:
            with TestClient(app) as client:
                for path in (
                    "/api/v1/finance/payment-performance",
                    "/api/v1/finance/revenue",
                    "/api/v1/finance/trends/gross_revenue",
                ):
                    response = client.get(path)
                    assert response.status_code == 503, path
        finally:
            get_settings.cache_clear()


class TestOpenApiDocumentation:
    def test_all_finance_routes_documented(self, client):
        schema = client.get("/openapi.json").json()
        finance_paths = [
            path for path in schema["paths"] if path.startswith("/api/v1/finance")
        ]
        assert sorted(finance_paths) == [
            "/api/v1/finance/orders",
            "/api/v1/finance/payment-performance",
            "/api/v1/finance/payments",
            "/api/v1/finance/refunds",
            "/api/v1/finance/revenue",
            "/api/v1/finance/settlements",
            "/api/v1/finance/summary",
            "/api/v1/finance/trends/{metric}",
        ]
        for path in finance_paths:
            operation = schema["paths"][path]["get"]
            assert operation.get("summary"), path
            assert operation.get("description"), path
