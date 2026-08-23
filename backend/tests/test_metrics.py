"""FinanceService tests: aggregates + filtered reads over seeded SQLite.

No provider calls — the finance layer reads only local persistence. Money
assertions use exact minor-unit integers throughout.
"""

from datetime import date, datetime, timezone

from app.db.mappers import (
    order_from_normalized,
    payment_from_normalized,
    refund_from_normalized,
    settlement_from_normalized,
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
    NormalizedSettlement,
)
from app.services.metrics import FinanceService, _utc_day_bounds

UTC = timezone.utc

# Window used by the seeds below; filters target slices of it.
DAY1 = date(2026, 3, 10)
DAY2 = date(2026, 3, 11)


def seed_payment(session, *, i=1, amount=1000, status="captured",
                 currency="INR", method="upi", when=None):
    row = payment_from_normalized(
        NormalizedPayment(
            provider="razorpay",
            external_id=f"pay_{i:014d}",
            amount_minor=amount,
            currency=currency,
            status=status,
            order_id=f"order_{i:014d}",
            method=method,
            created_at=when or datetime(2026, 3, 10, 12, i % 60, tzinfo=UTC),
        )
    )
    PaymentRepository(session).upsert(row)


def seed_refund(session, *, i=1, amount=500, currency="INR",
                payment_id="pay_00000000000001", when=None):
    row = refund_from_normalized(
        NormalizedRefund(
            provider="razorpay",
            external_id=f"rfnd_{i:014d}",
            razorpay_payment_id=payment_id,
            amount_minor=amount,
            currency=currency,
            status="processed",
            created_at=when or datetime(2026, 3, 11, 9, i % 60, tzinfo=UTC),
        )
    )
    RefundRepository(session).upsert(row)


def seed_settlement(session, *, i=1, amount=900, currency="INR", when=None):
    from app.schemas import NormalizedSettlement

    row = settlement_from_normalized(
        NormalizedSettlement(
            provider="razorpay",
            external_id=f"setl_{i:014d}",
            amount_minor=amount,
            fees_minor=25,
            tax_minor=4,
            utr=f"UTR{i:09d}",
            currency=currency,
            status="processed",
            created_at=when or datetime(2026, 3, 11, 5, i % 60, tzinfo=UTC),
        )
    )
    from app.db.repositories import SettlementRepository

    SettlementRepository(session).upsert(row)


def seed_order(session, *, i=1, amount=1000, currency="INR", when=None):
    row = order_from_normalized(
        NormalizedOrder(
            provider="razorpay",
            external_id=f"order_{i:014d}",
            amount_minor=amount,
            amount_paid_minor=amount,
            amount_due_minor=0,
            currency=currency,
            status="paid",
            receipt=f"rcpt_{i}",
            created_at=when or datetime(2026, 3, 10, 8, i % 60, tzinfo=UTC),
        )
    )
    OrderRepository(session).upsert(row)


class TestUtcDayBounds:
    def test_end_covers_full_day(self):
        start, end = _utc_day_bounds(DAY1, DAY1)
        assert (start.hour, start.minute) == (0, 0)
        assert end == datetime(2026, 3, 10, 23, 59, 59, 999999, tzinfo=UTC)

    def test_none_passthrough(self):
        assert _utc_day_bounds(None, None) == (None, None)


class TestSummary:
    def test_metric_definitions_exact(self, session_factory):
        session = session_factory()
        seed_payment(session, i=1, amount=1000, status="captured")
        seed_payment(session, i=2, amount=2500, status="captured")
        seed_payment(session, i=3, amount=700, status="failed")
        seed_refund(session, i=1, amount=500)  # against pay_1
        seed_settlement(session, i=1, amount=3000)

        summary = FinanceService.summary(session)
        assert len(summary.currencies) == 1
        item = summary.currencies[0]
        assert item.currency == "INR"
        assert item.gross_amount_minor == 4200
        assert item.transaction_count == 3
        assert item.successful_amount_minor == 3500
        assert item.successful_count == 2
        assert item.failed_count == 1
        assert item.refunded_amount_minor == 500
        assert item.refund_count == 1
        assert item.net_amount_minor == 3000  # 3500 captured - 500 refunded
        assert item.settlement_amount_minor == 3000

    def test_grouped_per_currency_with_zero_fills(self, session_factory):
        session = session_factory()
        seed_payment(session, i=1, amount=1000, currency="INR")
        seed_settlement(session, i=2, amount=700, currency="USD", when=datetime(2026, 3, 10, 6, tzinfo=UTC))
        seed_refund(session, i=3, amount=200, currency="USD",
                    payment_id="pay_missing")

        summary = FinanceService.summary(session)
        by_currency = {item.currency: item for item in summary.currencies}
        assert set(by_currency) == {"INR", "USD"}

        usd = by_currency["USD"]
        assert usd.gross_amount_minor == 0  # no payments in USD
        assert usd.refunded_amount_minor == 200
        assert usd.net_amount_minor == -200  # refunds without captured money
        assert usd.settlement_amount_minor == 700

        assert by_currency["INR"].transaction_count == 1
        assert by_currency["INR"].refund_count == 0

    def test_date_window_filters_every_resource(self, session_factory):
        session = session_factory()
        seed_payment(session, i=1, when=datetime(2026, 3, 10, 12, tzinfo=UTC))
        seed_payment(session, i=4, when=datetime(2026, 3, 11, 12, tzinfo=UTC))
        seed_refund(session, i=2, when=datetime(2026, 3, 11, 9, tzinfo=UTC))
        seed_settlement(session, i=3, when=datetime(2026, 3, 10, 5, tzinfo=UTC))

        day1 = FinanceService.summary(session, start_date=DAY1, end_date=DAY1)
        item = day1.currencies[0]
        assert item.transaction_count == 1
        assert item.refund_count == 0
        assert item.settlement_amount_minor == 900

        both = FinanceService.summary(
            session, start_date=DAY1, end_date=date(2026, 3, 11)
        )
        item = both.currencies[0]
        assert item.transaction_count == 2
        assert item.refund_count == 1

    def test_status_and_method_filters_apply_to_payments(self, session_factory):
        session = session_factory()
        seed_payment(session, i=1, amount=1001, status="captured", method="upi")
        seed_payment(session, i=2, amount=1002, status="failed", method="card")

        failed_only = FinanceService.summary(session, status="failed")
        item = failed_only.currencies[0]
        assert item.gross_amount_minor == 1002
        assert item.failed_count == 1 and item.successful_count == 0

        upi_only = FinanceService.summary(session, method="upi")
        assert upi_only.currencies[0].gross_amount_minor == 1001

    def test_currency_filter_narrows_groups(self, session_factory):
        session = session_factory()
        seed_payment(session, i=1, currency="INR")
        seed_payment(session, i=5, amount=999, currency="USD")
        summary = FinanceService.summary(session, currency="USD")
        assert [item.currency for item in summary.currencies] == ["USD"]
        assert summary.currencies[0].gross_amount_minor == 999

    def test_empty_store_yields_empty_summary(self, session_factory):
        session = session_factory()
        summary = FinanceService.summary(session)
        assert summary.currencies == []
        assert summary.start_date is None


class TestPagedReads:
    def test_payment_page_roundtrip_and_filters(self, session_factory):
        session = session_factory()
        seed_payment(session, i=1, status="captured")
        seed_payment(session, i=2, status="failed")

        page = FinanceService.list_payments(session)
        assert page.total == 2 and page.limit == 50 and page.offset == 0
        assert {row.status for row in page.items} == {"captured", "failed"}
        first = page.items[0]
        assert first.razorpay_payment_id == "pay_00000000000001"
        assert first.amount_minor == 1000
        assert first.provider_created_at is not None

        only_failed = FinanceService.list_payments(session, status="failed")
        assert only_failed.total == 1
        assert only_failed.items[0].status == "failed"

    def test_refund_page_filters_by_parent(self, session_factory):
        session = session_factory()
        seed_refund(session, i=1, payment_id="pay_A")
        seed_refund(session, i=2, payment_id="pay_B")

        page = FinanceService.list_refunds(session, payment_id="pay_B")
        assert page.total == 1
        assert page.items[0].razorpay_refund_id == "rfnd_00000000000002"

    def test_settlement_order_pages(self, session_factory):
        session = session_factory()
        seed_settlement(session, i=1)
        seed_order(session, i=1)

        settlements = FinanceService.list_settlements(session)
        assert settlements.total == 1
        assert settlements.items[0].amount_minor == 900
        assert settlements.items[0].fees_minor == 25

        orders = FinanceService.list_orders(session, receipt="rcpt_1")
        assert orders.total == 1
        assert orders.items[0].amount_paid_minor == 1000

    def test_pagination_slices(self, session_factory):
        session = session_factory()
        for i in range(1, 6):
            seed_payment(session, i=i, when=datetime(2026, 3, 10, 12, i, tzinfo=UTC))
        page1 = FinanceService.list_payments(session, limit=3, offset=0)
        page2 = FinanceService.list_payments(session, limit=3, offset=3)
        ids = [r.id for r in page1.items] + [r.id for r in page2.items]
        assert len(ids) == 5 and len(set(ids)) == 5
