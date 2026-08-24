"""FinanceService tests: aggregates + filtered reads over seeded SQLite.

No provider calls — the finance layer reads only local persistence. Money
assertions use exact minor-unit integers throughout.
"""

from datetime import date, datetime, timedelta, timezone

from app.core.periods import REPORTING_TIMEZONE as IST
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
from app.services.metrics import (
    FinanceService,
    _utc_day_bounds,
    percentage_change,
    safe_rate,
)

UTC = timezone.utc

# Window used by the seeds below; filters target slices of it.
DAY1 = date(2026, 3, 10)
DAY2 = date(2026, 3, 11)


def seed_payment(session, *, i=1, amount=1000, status="captured",
                 currency="INR", method="upi", when=None,
                 fee_minor=None, tax_minor=None):
    row = payment_from_normalized(
        NormalizedPayment(
            provider="razorpay",
            external_id=f"pay_{i:014d}",
            amount_minor=amount,
            currency=currency,
            status=status,
            order_id=f"order_{i:014d}",
            method=method,
            fee_minor=fee_minor,
            tax_minor=tax_minor,
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
        seed_refund(session, i=1, amount=500)  # against pay_1, processed
        seed_settlement(session, i=1, amount=3000)

        summary = FinanceService.summary(session)
        assert len(summary.currencies) == 1
        item = summary.currencies[0]
        assert item.currency == "INR"
        # Spec §3.1: gross = success population only (failed pay_3 excluded).
        assert item.gross_amount_minor == 3500
        assert item.transaction_count == 3
        assert item.successful_amount_minor == 3500
        assert item.successful_count == 2
        assert item.failed_count == 1
        # No created/authorized/unknown seeds -> nothing in progress.
        assert item.in_progress_count == 0
        # Spec §3.5: fee/tax sums over the successful population (none seeded).
        assert item.fee_minor == 0
        assert item.tax_minor == 0
        assert item.refunded_amount_minor == 500
        assert item.refund_count == 1
        # Spec §3.4: three separately labeled net variants.
        assert item.net_of_refunds_minor == 3000  # 3500 - 500
        assert item.net_of_fees_minor == 3500
        assert item.net_of_refunds_and_fees_minor == 3000
        assert item.settlement_amount_minor == 3000
        assert item.settlement_fees_minor == 25
        assert item.settlement_tax_minor == 4

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
        assert usd.refund_count == 1
        # Spec §3.4: three separately labeled net variants — no ambiguous
        # single "net" field. Refunds without captured money go negative.
        assert usd.net_of_refunds_minor == -200
        assert usd.net_of_fees_minor == 0
        assert usd.net_of_refunds_and_fees_minor == -200
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
        # Spec §3.1: gross = SUM(amount WHERE captured) — a failed-only
        # population contributes zero gross despite its 1002 minor amount.
        assert item.gross_amount_minor == 0
        assert item.successful_amount_minor == 0
        # The attempt itself is still counted in volume/failure metrics.
        assert item.transaction_count == 1
        assert item.failed_count == 1 and item.successful_count == 0

        upi_only = FinanceService.summary(session, method="upi")
        item = upi_only.currencies[0]
        assert item.gross_amount_minor == 1001
        assert item.transaction_count == 1

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


def ist_day_window(day: date):
    """Half-open ``[00:00, 24:00)`` IST calendar-day window (spec §6.1/§6.2)."""
    start = datetime(day.year, day.month, day.day, tzinfo=IST)
    return start, start + timedelta(days=1)


class TestRateHelpers:
    """Pure ratio/change helpers — spec §2.4-§2.5 and Part 6.4 semantics."""

    def test_safe_rate_normal_and_zero_denominator(self):
        assert safe_rate(3, 4) == 75.0
        assert safe_rate(50_000, 600_000) == 8.33
        assert safe_rate(0, 7) == 0.0
        # Zero denominator: undefined -> None, never 0, never an exception.
        assert safe_rate(5, 0) is None
        assert safe_rate(0, 0) is None

    def test_percentage_change_semantics(self):
        assert percentage_change(110, 100) == (10, 10.0, "normal")
        assert percentage_change(50, 100) == (-50, -50.0, "normal")
        # Previous zero: mathematically undefined percentage (spec Part 6.4).
        assert percentage_change(100, 0) == (100, None, "new_activity")
        assert percentage_change(0, 0) == (0, None, "no_activity")


class TestPaymentPerformance:
    """FinanceService.payment_performance — specification Part 2."""

    def test_mixed_statuses_classified_per_spec(self, session_factory):
        session = session_factory()
        seed_payment(session, i=1, amount=100_000, status="captured")
        seed_payment(session, i=2, amount=250_000, status="captured")
        seed_payment(session, i=3, amount=50_000, status="failed")
        seed_payment(session, i=4, amount=70_000, status="created")
        seed_payment(session, i=5, amount=80_000, status="authorized")

        start, end = ist_day_window(DAY1)
        report = FinanceService.payment_performance(
            session, start=start, end=end, currency="INR"
        )
        assert report.transaction_volume == 5  # every attempt (§2.1)
        assert report.successful_transactions == 2  # captured only (§2.2)
        assert report.failed_transactions == 1  # §2.3
        assert report.in_progress_transactions == 2  # created+authorized (§2.0)
        assert report.success_rate.value == 40.0  # 2/5, §2.4 recommended basis
        assert report.success_rate.basis == "transaction_volume"
        assert report.failure_rate.value == 20.0
        assert report.other_currency_transactions_excluded == 0
        assert report.period.timezone == "Asia/Kolkata"
        assert report.period.start.tzinfo is not None

    def test_refunded_status_counts_as_successful(self, session_factory):
        session = session_factory()
        seed_payment(session, i=6, amount=300_000, status="refunded")
        start, end = ist_day_window(DAY1)
        report = FinanceService.payment_performance(session, start=start, end=end)
        assert report.successful_transactions == 1
        assert report.in_progress_transactions == 0

    def test_empty_period_yields_null_rates_not_zeros(self, session_factory):
        session = session_factory()
        start, end = ist_day_window(date(2030, 1, 1))
        report = FinanceService.payment_performance(session, start=start, end=end)
        assert report.transaction_volume == 0
        assert report.successful_transactions == 0
        assert report.failed_transactions == 0
        assert report.in_progress_transactions == 0
        assert report.success_rate.value is None
        assert report.failure_rate.value is None

    def test_other_currency_attempts_excluded_never_merged(self, session_factory):
        session = session_factory()
        seed_payment(session, i=7, amount=100_000, status="captured")
        seed_payment(session, i=8, amount=999, status="captured", currency="USD")
        start, end = ist_day_window(DAY1)
        report = FinanceService.payment_performance(
            session, start=start, end=end, currency="INR"
        )
        assert report.transaction_volume == 1
        assert report.other_currency_transactions_excluded == 1


class TestRevenueMetrics:
    """FinanceService.revenue — specification Parts 3-4.

    The reference case is the worked example in spec Part 10.
    """

    def test_spec_part_10_worked_example_exact(self, session_factory):
        session = session_factory()
        seed_payment(session, i=1, amount=100_000, status="captured",
                     fee_minor=2_000, tax_minor=360)
        seed_payment(session, i=2, amount=200_000, status="captured",
                     fee_minor=4_000, tax_minor=720)
        seed_payment(session, i=3, amount=50_000, status="failed")  # C
        seed_payment(session, i=4, amount=300_000, status="captured",
                     fee_minor=6_000, tax_minor=1_080)
        # D's partial refund, stamped inside the same IST day window.
        seed_refund(session, i=1, amount=50_000,
                    payment_id="pay_00000000000001",
                    when=datetime(2026, 3, 10, 6, tzinfo=UTC))

        start, end = ist_day_window(DAY1)
        report = FinanceService.revenue(session, start=start, end=end)
        assert report.currency == "INR"
        # C excluded from gross (failed): ₹1000+₹2000+₹3000 in paise.
        assert report.gross_revenue_minor == 600_000
        assert report.razorpay_fee_minor == 12_000
        assert report.razorpay_tax_minor == 2_160
        assert report.refunds.refund_count == 1
        assert report.refunds.refund_amount_minor == 50_000
        assert report.net_revenue.net_of_refunds_minor == 550_000
        assert report.net_revenue.net_of_fees_minor == 585_840
        assert report.net_revenue.net_of_refunds_and_fees_minor == 535_840
        # Definition A: refund amount / gross revenue x 100 = 500/6000.
        assert report.refunds.refund_rate.value == 8.33
        assert (
            report.refunds.refund_rate.basis
            == "definition_A_refund_amount_over_gross_revenue"
        )

    def test_failed_payment_fee_tax_excluded_from_sums(self, session_factory):
        session = session_factory()
        seed_payment(session, i=1, amount=100_000, status="failed",
                     fee_minor=9_999, tax_minor=888)
        start, end = ist_day_window(DAY1)
        report = FinanceService.revenue(session, start=start, end=end)
        assert report.gross_revenue_minor == 0
        assert report.razorpay_fee_minor == 0  # fee/tax sum over success only
        assert report.razorpay_tax_minor == 0

    def test_empty_period_zeroes_with_null_refund_rate(self, session_factory):
        session = session_factory()
        start, end = ist_day_window(date(2030, 1, 1))
        report = FinanceService.revenue(session, start=start, end=end)
        assert report.gross_revenue_minor == 0
        assert report.refunds.refund_count == 0
        assert report.refunds.refund_amount_minor == 0
        assert report.refunds.refund_rate.value is None
        assert report.net_revenue.net_of_refunds_minor == 0


class TestTrends:
    """FinanceService.trend — specification Part 6 over a fixed clock."""

    NOW = datetime(2026, 3, 11, 15, 30, tzinfo=IST)  # a Wednesday afternoon

    def test_day_trend_normal_negative_change(self, session_factory):
        session = session_factory()
        seed_payment(session, i=1, amount=100_000)  # DAY1 = previous IST day
        seed_payment(session, i=2, amount=250_000)

        trend = FinanceService.trend(
            session, metric="gross_revenue", granularity="day", now=self.NOW
        )
        assert trend.metric == "gross_revenue"
        assert trend.granularity == "day"
        assert trend.timezone == "Asia/Kolkata"
        assert trend.current_period.value == 0  # nothing seeded "today"
        assert trend.previous_period.value == 350_000
        assert trend.absolute_change == -350_000
        assert trend.percentage_change == -100.0
        assert trend.change_type == "normal"

    def test_new_activity_has_null_percentage(self, session_factory):
        session = session_factory()
        seed_payment(session, i=3, amount=100_000,
                     when=datetime(2026, 3, 11, 5, tzinfo=UTC))  # today IST

        trend = FinanceService.trend(
            session, metric="gross_revenue", granularity="day", now=self.NOW
        )
        assert trend.current_period.value == 100_000
        assert trend.previous_period.value == 0
        assert trend.percentage_change is None  # never a fabricated %
        assert trend.change_type == "new_activity"

    def test_no_activity_empty_store(self, session_factory):
        session = session_factory()
        trend = FinanceService.trend(
            session, metric="transaction_volume", granularity="week", now=self.NOW
        )
        assert trend.current_period.value == 0
        assert trend.previous_period.value == 0
        assert trend.absolute_change == 0
        assert trend.percentage_change is None
        assert trend.change_type == "no_activity"
