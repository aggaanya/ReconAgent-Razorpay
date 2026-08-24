"""Repository behavior tests over in-memory SQLite (portable upsert path).

PostgreSQL-specific verification (JSONB, BIGINT identity, constraint DDL)
is covered by the migration integration tests and the live schema check;
the repository contract itself is dialect-independent by design.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal  # noqa: F401

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import Order, Payment, Refund, Settlement, SyncRun, SyncStatus
from app.db.repositories import (
    CountSumAggregate,
    DuplicateRecordError,
    OrderRepository,
    PaymentAggregate,
    PaymentRepository,
    RefundRepository,
    SettlementAggregate,
    SettlementRepository,
    SyncRunRepository,
    UpsertResult,
)

UTC = timezone.utc


def make_payment(i: int, **overrides) -> Payment:
    values = dict(
        razorpay_payment_id=f"pay_{i:014d}",
        amount_minor=1000 + i,
        currency="INR",
        status="captured",
        order_id=f"order_{i:014d}",
        provider_created_at=datetime(2026, 3, 1, 12, i % 60, tzinfo=UTC),
    )
    values.update(overrides)
    return Payment(**values)


def make_settlement(i: int, **overrides) -> Settlement:
    values = dict(
        razorpay_settlement_id=f"setl_{i:014d}",
        amount_minor=5000 + i,
        fees_minor=25,
        tax_minor=4,
        utr=f"UTR{i:09d}",
        status="processed",
        provider_created_at=datetime(2026, 3, 2, 8, i % 60, tzinfo=UTC),
    )
    values.update(overrides)
    return Settlement(**values)


def make_order(i: int, **overrides) -> Order:
    values = dict(
        razorpay_order_id=f"order_{i:014d}",
        amount_minor=10000 + i,
        amount_paid_minor=10000 + i,
        amount_due_minor=0,
        currency="INR",
        status="paid",
        receipt=f"rcpt_{i}",
        notes={"n": str(i)},
        provider_created_at=datetime(2026, 3, 1, 6, i % 60, tzinfo=UTC),
    )
    values.update(overrides)
    return Order(**values)


def make_refund(i: int, **overrides) -> Refund:
    values = dict(
        razorpay_refund_id=f"rfnd_{i:014d}",
        razorpay_payment_id=f"pay_{i:014d}",
        amount_minor=500 + i,
        currency="INR",
        status="processed",
        speed="normal",
        provider_created_at=datetime(2026, 3, 3, 7, i % 60, tzinfo=UTC),
    )
    values.update(overrides)
    return Refund(**values)


@pytest.fixture
def payments(session_factory):
    return PaymentRepository(session_factory())


@pytest.fixture
def settlements(session_factory):
    return SettlementRepository(session_factory())


@pytest.fixture
def orders(session_factory):
    return OrderRepository(session_factory())


@pytest.fixture
def refunds(session_factory):
    return RefundRepository(session_factory())


class TestPaymentInsert:
    def test_insert_persists_and_flushes_id(self, session_factory, payments):
        stored = payments.insert(make_payment(1))
        assert stored.id is not None
        session = session_factory()
        fetched = session.get(Payment, stored.id)
        assert fetched.razorpay_payment_id == "pay_00000000000001"
        assert fetched.amount_minor == 1001

    def test_insert_duplicate_provider_id_rejected(self, payments):
        payments.insert(make_payment(1))
        with pytest.raises(DuplicateRecordError):
            payments.insert(make_payment(1))

    def test_notes_default_empty_dict(self, payments):
        stored = payments.insert(
            Payment(razorpay_payment_id="pay_x", amount_minor=1)
        )
        assert stored.notes == {}


class TestPaymentUpsert:
    def test_upsert_inserts_then_updates_without_duplicates(
        self, session_factory, payments
    ):
        first, inserted = payments.upsert(make_payment(1, status="authorized"))
        assert inserted is True
        second, inserted_again = payments.upsert(make_payment(1, status="captured"))
        assert inserted_again is False
        assert first.id == second.id
        session = session_factory()
        rows = session.query(Payment).all()
        assert len(rows) == 1
        assert rows[0].status == "captured"
        assert rows[0].amount_minor == 1001

    def test_upsert_many_reports_counts(self, payments):
        payments.insert(make_payment(1))
        result = payments.upsert_many(
            [make_payment(1), make_payment(2), make_payment(3)]
        )
        assert result == UpsertResult(inserted=2, updated=1)
        assert result.total == 3


class TestPaymentLookup:
    def test_get_by_razorpay_id(self, payments):
        payments.insert(make_payment(7))
        found = payments.get_by_razorpay_id("pay_00000000000007")
        assert found is not None and found.amount_minor == 1007
        assert payments.get_by_razorpay_id("pay_missing") is None


class TestPaymentPagination:
    def test_list_paginated_returns_pages_and_total(self, payments):
        payments.upsert_many([make_payment(i) for i in range(1, 8)])  # 7 rows
        page1, total = payments.list_paginated(limit=3, offset=0)
        page2, total2 = payments.list_paginated(limit=3, offset=3)
        page3, total3 = payments.list_paginated(limit=3, offset=6)
        assert total == total2 == total3 == 7
        ids = [p.razorpay_payment_id for p in page1 + page2 + page3]
        assert len(ids) == 7 and len(set(ids)) == 7  # stable, no overlap

    def test_pagination_bounds_validated(self, payments):
        with pytest.raises(ValueError):
            payments.list_paginated(limit=0)
        with pytest.raises(ValueError):
            payments.list_paginated(limit=501)
        with pytest.raises(ValueError):
            payments.list_paginated(offset=-1)


class TestSettlementRepository:
    def test_insert_update_lookup_roundtrip(self, settlements):
        settlements.insert(make_settlement(1))
        duplicate = settlements.get_by_razorpay_id("setl_00000000000001")
        assert duplicate is not None
        refreshed, inserted = settlements.upsert(make_settlement(1, status="partial"))
        assert inserted is False
        assert refreshed.status == "partial"

    def test_get_by_utr(self, settlements):
        settlements.upsert_many([make_settlement(1), make_settlement(2)])
        matches = settlements.get_by_utr("UTR000000001")
        assert len(matches) == 1
        assert matches[0].razorpay_settlement_id == "setl_00000000000001"

    def test_unique_constraint_enforced_at_database_level(
        self, session_factory, settlements
    ):
        settlements.insert(make_settlement(1))
        session = session_factory()
        session.add(make_settlement(1))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_pagination(self, settlements):
        settlements.upsert_many([make_settlement(i) for i in range(5)])
        items, total = settlements.list_paginated(limit=2, offset=4)
        assert total == 5 and len(items) == 1


class TestSyncRunRepository:
    def test_lifecycle_running_to_success(self, session_factory):
        session = session_factory()
        runs = SyncRunRepository(session)
        run = runs.create(triggered_by="test")
        assert run.status == SyncStatus.RUNNING.value
        assert run.completed_at is None
        session.commit()

        run.payments_fetched = 3
        run.payments_inserted = 2
        run.payments_updated = 1
        run.payments_watermark_epoch = 1768000000
        runs.mark_success(run)
        session.commit()

        reloaded = SyncRunRepository(session).get(run.id)
        assert reloaded.status == "success"
        assert reloaded.completed_at is not None
        assert reloaded.payments_watermark_epoch == 1768000000

    def test_failure_states_record_sanitized_error(self, session_factory):
        session = session_factory()
        runs = SyncRunRepository(session)
        run = runs.create()
        runs.mark_partial_failure(run, "Razorpay API error (HTTP 500)")
        session.commit()
        reloaded = SyncRunRepository(session).get(run.id)
        assert reloaded.status == "partial_failure"
        assert reloaded.error == "Razorpay API error (HTTP 500)"

    def test_latest_watermark_reads_only_successful_runs(self, session_factory):
        session = session_factory()
        runs = SyncRunRepository(session)
        assert runs.latest_watermark("payments_watermark_epoch") is None

        failed = runs.create()
        failed.payments_watermark_epoch = 111
        runs.mark_failed(failed, "boom")
        partial = runs.create()
        partial.settlements_watermark_epoch = 222
        runs.mark_partial_failure(partial, "settlements failed")
        ok = runs.create()
        ok.payments_watermark_epoch = 333
        ok.settlements_watermark_epoch = 444
        runs.mark_success(ok)
        session.commit()

        assert runs.latest_watermark("payments_watermark_epoch") == 333
        assert runs.latest_watermark("settlements_watermark_epoch") == 444

    def test_invalid_status_rejected_by_check_constraint(self, session_factory):
        session = session_factory()
        session.add(SyncRun(status="bogus"))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_list_most_recent_first(self, session_factory):
        session = session_factory()
        runs = SyncRunRepository(session)
        first = runs.create()
        runs.mark_success(first)
        second = runs.create()
        runs.mark_success(second)
        session.commit()
        items, total = runs.list_paginated(limit=10, offset=0)
        assert total == 2
        assert [r.id for r in items] == sorted((first.id, second.id), reverse=True)


class TestMoneyPrecisionContract:
    def test_large_minor_amounts_survive_roundtrip(self, session_factory, payments):
        big = 999_999_999_999  # ~Rs 10 crore in paise; beyond float32 exactness
        stored = payments.insert(make_payment(1, amount_minor=big))
        session = session_factory()
        assert session.get(Payment, stored.id).amount_minor == big

    def test_fee_tax_minor_units_survive_roundtrip(
        self, session_factory, payments
    ):
        stored = payments.insert(make_payment(1, fee_minor=588, tax_minor=89))
        session = session_factory()
        fetched = session.get(Payment, stored.id)
        assert fetched.fee_minor == 588 and fetched.tax_minor == 89


class TestTransactionSemantics:
    """Repositories flush; callers own commit/rollback. These tests pin the
    transaction contract the sync service relies on (per-page commits,
    failed-page rollback leaves no partial state)."""

    def test_rollback_discards_uncommitted_inserts(self, session_factory):
        session = session_factory()
        payments = PaymentRepository(session)
        payments.insert(make_payment(1))
        assert session.query(Payment).count() == 1  # visible pre-commit
        session.rollback()
        assert session_factory().query(Payment).count() == 0

    def test_commit_persists_across_sessions(self, session_factory):
        session = session_factory()
        PaymentRepository(session).insert(make_payment(1))
        session.commit()
        assert session_factory().query(Payment).count() == 1

    def test_failed_flush_leaves_no_partial_state_after_rollback(
        self, session_factory
    ):
        session = session_factory()
        session.add(make_payment(1))
        # Second row violates the NOT NULL amount_minor constraint.
        session.add(Payment(razorpay_payment_id="pay_broken", notes={}))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
        fresh = session_factory()
        assert fresh.query(Payment).count() == 0
        # Session is usable again after the rollback.
        PaymentRepository(fresh).insert(make_payment(2))
        fresh.commit()
        assert fresh.query(Payment).count() == 1

    def test_committed_page_survives_later_page_failure(
        self, session_factory
    ):
        """Mirrors the sync guarantee: page 1 committed stays committed even
        when a later page fails and is rolled back."""
        session = session_factory()
        payments = PaymentRepository(session)
        payments.upsert_many([make_payment(1), make_payment(2)])
        session.commit()  # end of "page 1"

        session.begin_nested()
        try:
            payments.upsert_many([make_payment(3)])
            session.add(Payment(razorpay_payment_id="pay_broken", notes={}))
            session.flush()
            pytest.fail("flush should have raised")
        except IntegrityError:
            session.rollback()  # discard only the failing page

        final = session_factory().query(Payment).all()
        ids = {p.razorpay_payment_id for p in final}
        assert ids == {"pay_00000000000001", "pay_00000000000002"}

    def test_rolled_back_update_preserves_committed_state(
        self, session_factory, payments
    ):
        payments.upsert(make_payment(1, status="authorized"))
        payments._session.commit()

        payments.upsert(make_payment(1, status="captured"))  # uncommitted
        payments._session.rollback()
        assert (
            session_factory().query(Payment).one().status == "authorized"
        )


class TestPaymentDatabaseConstraints:
    def test_unique_constraint_enforced_at_database_level(
        self, session_factory, payments
    ):
        payments.insert(make_payment(1))
        session = session_factory()
        session.add(make_payment(1))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_amount_minor_rejects_null(self, session_factory):
        session = session_factory()
        session.add(Payment(razorpay_payment_id="pay_noamount", amount_minor=None))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_provider_id_rejects_null(self, session_factory):
        session = session_factory()
        session.add(Payment(razorpay_payment_id=None, amount_minor=100))
        with pytest.raises(IntegrityError):
            session.commit()


class TestSettlementDatabaseConstraints:
    def test_amount_minor_rejects_null(self, session_factory):
        session = session_factory()
        session.add(Settlement(razorpay_settlement_id="setl_x", amount_minor=None))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_fees_tax_default_to_zero_when_omitted(self, session_factory):
        session = session_factory()
        stored = SettlementRepository(session).insert(
            make_settlement(1, fees_minor=0, tax_minor=0)
        )
        session.commit()
        reloaded = session_factory().get(Settlement, stored.id)
        assert reloaded.fees_minor == 0 and reloaded.tax_minor == 0


class TestOrderRepository:
    def test_insert_update_lookup_roundtrip(self, orders):
        orders.insert(make_order(1))
        found = orders.get_by_razorpay_id("order_00000000000001")
        assert found is not None and found.amount_paid_minor == 10001
        refreshed, inserted = orders.upsert(make_order(1, status="cancelled"))
        assert inserted is False
        assert refreshed.status == "cancelled"

    def test_insert_duplicate_provider_id_rejected(self, orders):
        orders.insert(make_order(1))
        with pytest.raises(DuplicateRecordError):
            orders.insert(make_order(1))

    def test_unique_constraint_enforced_at_database_level(
        self, session_factory, orders
    ):
        orders.insert(make_order(1))
        session = session_factory()
        session.add(make_order(1))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_pagination_and_filters(self, orders):
        orders.upsert_many([make_order(i) for i in range(5)])
        items, total = orders.list_paginated(limit=3, offset=3)
        assert total == 5 and len(items) == 2

        filtered, total = orders.list_filtered(status="paid")
        assert total == 5
        filtered, total = orders.list_filtered(receipt="rcpt_2")
        assert total == 1 and filtered[0].razorpay_order_id == "order_00000000000002"
        _, total = orders.list_filtered(start=datetime(2026, 4, 1, tzinfo=UTC))
        assert total == 0

    def test_amount_defaults_zero_when_omitted(self, session_factory):
        session = session_factory()
        stored = OrderRepository(session).insert(
            Order(
                razorpay_order_id="order_x",
                amount_minor=10,
                amount_paid_minor=0,
                amount_due_minor=0,
            )
        )
        session.commit()
        reloaded = session_factory().get(Order, stored.id)
        assert reloaded.amount_paid_minor == 0 and reloaded.amount_due_minor == 0


class TestRefundRepository:
    def test_insert_update_lookup_roundtrip(self, refunds):
        refunds.insert(make_refund(1))
        found = refunds.get_by_razorpay_id("rfnd_00000000000001")
        assert found is not None and found.amount_minor == 501
        refreshed, inserted = refunds.upsert(make_refund(1, status="failed"))
        assert inserted is False
        assert refreshed.status == "failed"

    def test_insert_duplicate_provider_id_rejected(self, refunds):
        refunds.insert(make_refund(1))
        with pytest.raises(DuplicateRecordError):
            refunds.insert(make_refund(1))

    def test_get_by_payment_id(self, refunds):
        refunds.upsert_many([make_refund(1), make_refund(2), make_refund(3)])
        matches = refunds.get_by_payment_id("pay_00000000000002")
        assert len(matches) == 1
        assert matches[0].razorpay_refund_id == "rfnd_00000000000002"

    def test_pagination_and_filters(self, refunds):
        refunds.upsert_many([make_refund(i) for i in range(5)])
        items, total = refunds.list_paginated(limit=2, offset=4)
        assert total == 5 and len(items) == 1

        filtered, total = refunds.list_filtered(payment_id="pay_00000000000003")
        assert total == 1
        _, total = refunds.list_filtered(currency="USD")
        assert total == 0

    def test_payment_link_is_plain_column_not_fk(self, session_factory):
        """A refund may be stored before its parent payment is synced."""
        session = session_factory()
        RefundRepository(session).insert(make_refund(1))
        session.commit()
        # No payment row exists; the refund persists regardless.
        assert session_factory().query(Payment).count() == 0
        assert session_factory().query(Refund).count() == 1


class TestDetailedUpsert:
    """Change detection: identical rows are skipped without an UPDATE."""

    def test_outcomes_inserted_updated_skipped(self, payments):
        row, outcome = payments.upsert_detailed(make_payment(1, status="created"))
        assert outcome == "inserted"
        row, outcome = payments.upsert_detailed(make_payment(1, status="captured"))
        assert outcome == "updated" and row.status == "captured"
        row, outcome = payments.upsert_detailed(make_payment(1, status="captured"))
        assert outcome == "skipped"

    def test_batch_counts_all_three_buckets(self, payments):
        payments.insert(make_payment(1))
        result = payments.upsert_many_detailed(
            [make_payment(1), make_payment(2), make_payment(3)]
        )
        assert (result.inserted, result.updated, result.skipped) == (2, 0, 1)
        assert result.total == 3

    def test_datetime_naive_vs_aware_compares_equal(self, session_factory, payments):
        """SQLite returns naive datetimes; a re-fetch must not look changed."""
        aware = datetime(2026, 3, 1, 12, 30, tzinfo=UTC)
        payments.insert(make_payment(1, provider_created_at=aware))
        payments._session.commit()
        naive = datetime(2026, 3, 1, 12, 30)  # same instant, no tzinfo
        _, outcome = payments.upsert_detailed(
            make_payment(1, provider_created_at=naive)
        )
        assert outcome == "skipped"

    def test_notes_change_is_detected(self, payments):
        payments.insert(make_payment(1, notes={"a": "1"}))
        _, outcome = payments.upsert_detailed(make_payment(1, notes={"a": "2"}))
        assert outcome == "updated"
        _, outcome = payments.upsert_detailed(make_payment(1, notes={"a": "2"}))
        assert outcome == "skipped"

    def test_legacy_two_state_upsert_still_works(self, payments):
        """The original upsert contract stays intact for existing callers."""
        _, inserted = payments.upsert(make_payment(9))
        assert inserted is True
        _, inserted_again = payments.upsert(make_payment(9))
        assert inserted_again is False

    def test_settlements_orders_refunds_share_the_contract(
        self, settlements, orders, refunds
    ):
        for repo, factory, key in (
            (settlements, make_settlement, "setl_00000000000001"),
            (orders, make_order, "order_00000000000001"),
            (refunds, make_refund, "rfnd_00000000000001"),
        ):
            _, outcome = repo.upsert_detailed(factory(1))
            assert outcome == "inserted"
            _, outcome = repo.upsert_detailed(factory(1))
            assert outcome == "skipped"


class TestFinanceAggregates:
    def test_payment_aggregate_by_currency(self, session_factory, payments):
        payments.upsert_many(
            [
                make_payment(1, status="captured", amount_minor=1000),
                make_payment(2, status="captured", amount_minor=2500),
                make_payment(3, status="failed", amount_minor=700),
                make_payment(4, status="created", amount_minor=300),
            ]
        )
        aggregates = payments.aggregate_by_currency()
        assert set(aggregates) == {"INR"}
        agg = aggregates["INR"]
        assert isinstance(agg, PaymentAggregate)
        assert agg.count == 4
        assert agg.amount_minor == 4500
        # Spec §2.0/§3.1: "successful" = captured (+ refunded-status).
        assert (agg.successful_amount_minor, agg.successful_count) == (3500, 2)
        assert agg.failed_count == 1

    def test_refunded_status_joins_successful_population(self, payments):
        """Spec §2.0/§3.6: refunded payments were captured -> successful."""
        payments.upsert_many(
            [
                make_payment(1, status="refunded", amount_minor=1000),
                make_payment(2, status="failed", amount_minor=700),
            ]
        )
        agg = payments.aggregate_by_currency()["INR"]
        assert (agg.successful_amount_minor, agg.successful_count) == (1000, 1)
        assert agg.refunded_count == 1
        assert agg.failed_count == 1

    def test_payment_aggregate_respects_filters(self, payments):
        payments.upsert_many(
            [
                make_payment(1, method="upi"),
                make_payment(2, method="card"),
                make_payment(3, method="upi"),
            ]
        )
        aggregates = payments.aggregate_by_currency(method="upi")
        assert aggregates["INR"].count == 2

    def test_refund_settlement_aggregates_group_by_currency(
        self, session_factory, refunds, settlements
    ):
        refunds.upsert_many(
            [
                make_refund(1, currency="INR"),
                make_refund(2, currency="INR"),
                make_refund(3, currency="USD"),
            ]
        )
        aggregates = refunds.aggregate_by_currency()
        assert aggregates["INR"] == CountSumAggregate(count=2, amount_minor=1003)
        assert aggregates["USD"].amount_minor == 503

        settlements.upsert_many([make_settlement(1), make_settlement(2)])
        settlement_totals = settlements.aggregate_by_currency()
        # make_settlement omits currency; rows group under the None key.
        # Settlement aggregates also carry direct fee/tax field sums (§5.2).
        assert settlement_totals[None] == SettlementAggregate(
            count=2, amount_minor=10003, fees_minor_sum=50, tax_minor_sum=8
        )

    def test_aggregates_over_empty_window_are_empty(self, payments):
        assert payments.aggregate_by_currency() == {}

