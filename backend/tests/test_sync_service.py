"""Synchronization workflow tests (service layer, no live Razorpay).

Covers the Chunk 7 scenario matrix: success, duplicate data, partial
failure, API timeout, rate limiting, empty responses, pagination,
incremental watermarks, page caps, pacing, and secret hygiene.
"""

import logging
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import Order, Payment, Refund, Settlement, SyncStatus
from app.db.repositories import SyncRunRepository
from app.integrations.razorpay.exceptions import (
    RazorpayConnectionError,
    RazorpayRateLimitError,
    RazorpayServerError,
)
from app.schemas import (
    NormalizedOrder,
    NormalizedPayment,
    NormalizedRefund,
    NormalizedSettlement,
)
from app.schemas.order import OrderListResponse
from app.schemas.payment import PaymentListResponse
from app.schemas.refund import RefundListResponse
from app.schemas.settlement import SettlementListResponse
from app.services.sync import RazorpaySyncService

UTC = timezone.utc
BASE_TS = datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)


def payment(n: int, created_at=None) -> NormalizedPayment:
    return NormalizedPayment(
        provider="razorpay",
        external_id=f"pay_{n:014d}",
        amount_minor=100 * n,
        currency="INR",
        status="captured",
        order_id=f"order_{n:014d}",
        created_at=created_at or (BASE_TS + timedelta(minutes=n)),
    )


def settlement(n: int, created_at=None) -> NormalizedSettlement:
    return NormalizedSettlement(
        provider="razorpay",
        external_id=f"setl_{n:014d}",
        amount_minor=90 * n,
        status="processed",
        utr=f"UTR{n:09d}",
        created_at=created_at or (BASE_TS + timedelta(hours=n)),
    )


def order(n: int, created_at=None) -> NormalizedOrder:
    return NormalizedOrder(
        provider="razorpay",
        external_id=f"order_{n:014d}",
        amount_minor=100 * n,
        amount_paid_minor=100 * n,
        amount_due_minor=0,
        currency="INR",
        status="paid",
        receipt=f"rcpt_{n}",
        created_at=created_at or (BASE_TS + timedelta(minutes=n)),
    )


def refund(n: int, created_at=None) -> NormalizedRefund:
    return NormalizedRefund(
        provider="razorpay",
        external_id=f"rfnd_{n:014d}",
        razorpay_payment_id=f"pay_{n:014d}",
        amount_minor=50 * n,
        currency="INR",
        status="processed",
        speed="normal",
        created_at=created_at or (BASE_TS + timedelta(minutes=n)),
    )


class FakeFetcher:
    """Scripted stand-in for RazorpayService.

    ``payment_pages`` / ``settlement_pages`` / ``order_pages`` /
    ``refund_pages`` are lists of pages; each call consumes the page
    matching the current skip. Errors are queued as
    ``(resource, on_call_index)`` -> exception.
    """

    def __init__(
        self,
        payment_pages=None,
        settlement_pages=None,
        order_pages=None,
        refund_pages=None,
        errors=(),
    ) -> None:
        self.payment_pages = list(payment_pages or [])
        self.settlement_pages = list(settlement_pages or [])
        self.order_pages = list(order_pages or [])
        self.refund_pages = list(refund_pages or [])
        self.errors = dict(errors)  # (resource, call#) -> exc
        self.calls = []
        self.closed = False

    _ENVELOPES = {
        "payments": PaymentListResponse,
        "settlements": SettlementListResponse,
        "orders": OrderListResponse,
        "refunds": RefundListResponse,
    }

    def _serve(self, resource, pages, *, count, skip, from_epoch=None):
        call_index = sum(1 for r, *_ in self.calls if r == resource)
        self.calls.append((resource, count, skip, from_epoch))
        error = self.errors.get((resource, call_index))
        if error is not None:
            raise error
        items = pages[skip // count] if skip // count < len(pages) else []
        envelope = self._ENVELOPES[resource]
        return envelope(items=list(items), count=len(items), limit=count, offset=skip)

    def list_payments(self, *, count, skip, from_epoch=None, to_epoch=None):
        return self._serve("payments", self.payment_pages, count=count, skip=skip,
                           from_epoch=from_epoch)

    def list_settlements(self, *, count, skip, from_epoch=None, to_epoch=None):
        return self._serve("settlements", self.settlement_pages, count=count,
                           skip=skip, from_epoch=from_epoch)

    def list_orders(self, *, count, skip):
        return self._serve("orders", self.order_pages, count=count, skip=skip)

    def list_refunds(self, *, count, skip):
        return self._serve("refunds", self.refund_pages, count=count, skip=skip)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def make_service(session_factory):
    def _make(fetcher, *, page_size=2, max_pages=50, delay=0.25):
        sleeps = []
        service = RazorpaySyncService(
            fetcher=fetcher,
            session_factory=session_factory,
            page_size=page_size,
            max_pages=max_pages,
            page_delay_seconds=delay,
            sleep=sleeps.append,
        )
        service.recorded_sleeps = sleeps  # type: ignore[attr-defined]
        return service

    return _make


class TestSuccessfulSync:
    def test_full_run_persists_and_records_metadata(self, make_service):
        fetcher = FakeFetcher(
            payment_pages=[[payment(1), payment(2)], [payment(3)]],
            settlement_pages=[[settlement(1)]],
        )
        outcome = make_service(fetcher).run_sync(triggered_by="test")
        assert outcome.status == "success"
        assert outcome.error is None
        assert (outcome.payments.fetched, outcome.payments.inserted) == (3, 3)
        assert (outcome.settlements.fetched, outcome.settlements.inserted) == (1, 1)

    def test_rows_written_with_exact_values(self, make_service,
                                            session_factory):
        fetcher = FakeFetcher(payment_pages=[[payment(1)]], settlement_pages=[[settlement(1)]])
        outcome = make_service(fetcher).run_sync()
        session = session_factory()
        row = session.query(Payment).one()
        assert row.razorpay_payment_id == "pay_00000000000001"
        assert row.amount_minor == 100  # exact minor units
        assert row.provider_created_at is not None
        srow = session.query(Settlement).one()
        assert srow.amount_minor == 90 and srow.utr == "UTR000000001"

        run = SyncRunRepository(session).get(outcome.run_id)
        assert run.status == "success"
        assert run.started_at is not None and run.completed_at is not None


class TestIdempotency:
    def test_running_twice_creates_no_duplicates(self, make_service,
                                                 session_factory):
        pages = [[payment(1), payment(2)]]
        service = make_service(FakeFetcher(payment_pages=pages,
                                           settlement_pages=[[settlement(1)]]))
        first = service.run_sync()
        second_outcome = make_service(FakeFetcher(payment_pages=pages,
                                                  settlement_pages=[[settlement(1)]])).run_sync()

        session = session_factory()
        assert session.query(Payment).count() == 2
        assert session.query(Settlement).count() == 1
        assert first.payments.inserted == 2
        # Identical rows are detected and skipped, not rewritten.
        assert second_outcome.payments.inserted == 0
        assert second_outcome.payments.updated == 0
        assert second_outcome.payments.skipped == 2
        assert second_outcome.status == "success"


class TestPartialFailure:
    def test_settlement_failure_keeps_committed_payments(self, make_service,
                                                         session_factory):
        fetcher = FakeFetcher(
            payment_pages=[[payment(1), payment(2)], [payment(3)]],
            settlement_pages=[],
            errors={("settlements", 0): RazorpayServerError(
                "Razorpay API error (HTTP 500): server exploded")},
        )
        outcome = make_service(fetcher).run_sync()
        assert outcome.status == "partial_failure"
        assert "HTTP 500" in outcome.error

        session = session_factory()
        payments_stored = session.query(Payment).all()
        assert len(payments_stored) == 3  # durable despite later failure
        assert session.query(Settlement).count() == 0

        run = SyncRunRepository(session).get(outcome.run_id)
        assert run.status == "partial_failure"
        assert run.payments_fetched == 3
        assert run.completed_at is not None

    def test_mid_phase_page_failure_keeps_earlier_pages(self, make_service,
                                                        session_factory):
        """Page 2 of settlements fails after page 1 committed."""
        fetcher = FakeFetcher(
            payment_pages=[[payment(1)]],
            settlement_pages=[[settlement(1), settlement(2)]],
            errors={("settlements", 1): RazorpayConnectionError("timed out")},
        )
        outcome = make_service(fetcher).run_sync()
        assert outcome.status == "partial_failure"
        session = session_factory()
        assert session.query(Payment).count() == 1
        assert session.query(Settlement).count() == 2  # page 1 survived


class TestFailureModes:
    def test_timeout_in_payments_phase_fails_cleanly(self, make_service,
                                                     session_factory):
        fetcher = FakeFetcher(
            errors={("payments", 0): RazorpayConnectionError(
                "Razorpay API request timed out after 10s (ReadTimeout)")},
        )
        outcome = make_service(fetcher).run_sync()
        assert outcome.status == "failed"
        assert "timed out" in outcome.error
        session = session_factory()
        assert session.query(Payment).count() == 0
        assert session.query(Settlement).count() == 0
        run = SyncRunRepository(session).get(outcome.run_id)
        assert run.status == "failed"

    def test_rate_limit_after_retries_recorded(self, make_service,
                                               session_factory):
        fetcher = FakeFetcher(
            payment_pages=[[payment(1)]],
            settlement_pages=[[settlement(1)]],
            errors={("settlements", 0): RazorpayRateLimitError(
                "Razorpay API error (HTTP 429)")},
        )
        outcome = make_service(fetcher).run_sync()
        assert outcome.status == "partial_failure"
        assert "429" in outcome.error
        session = session_factory()
        assert session.query(Payment).count() == 1
        assert session.query(Settlement).count() == 0


class TestEmptyResponses:
    def test_empty_pages_yield_success_with_zero_counts(self, make_service,
                                                        session_factory):
        outcome = make_service(FakeFetcher()).run_sync()
        assert outcome.status == "success"
        assert outcome.payments.fetched == 0
        assert outcome.settlements.fetched == 0
        session = session_factory()
        run = SyncRunRepository(session).get(outcome.run_id)
        assert run.payments_watermark_epoch is None
        assert run.status == "success"


class TestPagination:
    def test_walks_pages_until_short_page(self, make_service):
        fetcher = FakeFetcher(
            payment_pages=[
                [payment(1), payment(2)],
                [payment(3), payment(4)],
                [payment(5)],
            ],
            settlement_pages=[[settlement(1)]],
        )
        outcome = make_service(fetcher, page_size=2).run_sync()
        assert outcome.payments.fetched == 5
        payment_calls = [c for c in fetcher.calls if c[0] == "payments"]
        assert [(c[2], c[1]) for c in payment_calls] == [
            (0, 2), (2, 2), (4, 2)
        ]  # skip advances by page size; last short page stops the loop

    def test_paces_between_pages_only(self, make_service):
        fetcher = FakeFetcher(
            payment_pages=[[payment(1), payment(2)], [payment(3)]],
            settlement_pages=[[settlement(1), settlement(2)], [settlement(3)]],
        )
        service = make_service(fetcher, page_size=2, delay=0.25)
        service.run_sync()
        assert service.recorded_sleeps == [0.25, 0.25]

    def test_max_pages_cap_bounds_a_run(self, make_service,
                                        session_factory):
        fetcher = FakeFetcher(
            payment_pages=[[payment(i)] for i in range(1, 6)],  # 5 x 1-row pages
            settlement_pages=[],
        )
        outcome = make_service(fetcher, page_size=1, max_pages=2).run_sync()
        assert outcome.payments.fetched == 2  # capped early
        assert outcome.status == "success"


class TestIncrementalWatermark:
    def test_next_run_fetches_from_previous_watermark(self, make_service,
                                                      session_factory):
        late = BASE_TS + timedelta(hours=9)
        first_fetcher = FakeFetcher(
            payment_pages=[[payment(1), payment(2)], [payment(3, created_at=late)]],
            settlement_pages=[],
        )
        make_service(first_fetcher, page_size=2, delay=0).run_sync()

        second_fetcher = FakeFetcher(payment_pages=[], settlement_pages=[])
        make_service(second_fetcher, page_size=2, delay=0).run_sync()

        payment_calls = [c for c in second_fetcher.calls if c[0] == "payments"]
        expected_from = int(late.timestamp())
        assert all(c[3] == expected_from for c in payment_calls)


class TestSecretHygiene:
    def test_logs_and_metadata_contain_no_credential_material(
        self, make_service, session_factory, caplog
    ):
        fetcher = FakeFetcher(
            payment_pages=[[payment(1)]],
            settlement_pages=[],
            errors={("settlements", 0): RazorpayServerError(
                "Razorpay API error (HTTP 500)")},
        )
        with caplog.at_level(logging.DEBUG, logger="app.services.sync"):
            outcome = make_service(fetcher).run_sync()
        joined = caplog.text.lower()
        for forbidden in ("key_secret", "key_id=", "authorization", "basic "):
            assert forbidden not in joined
        session = session_factory()
        run = SyncRunRepository(session).get(outcome.run_id)
        assert "key" not in (run.error or "").lower()


class TestConstructionGuards:
    def test_invalid_configuration_rejected(self, session_factory):
        with pytest.raises(ValueError):
            RazorpaySyncService(
                fetcher=FakeFetcher(),
                session_factory=session_factory,
                page_size=101,
            )
        with pytest.raises(ValueError):
            RazorpaySyncService(
                fetcher=FakeFetcher(),
                session_factory=session_factory,
                max_pages=0,
            )


class TestFourResourceSync:
    """Orders/refunds phases: persistence, counters, and no-watermark
    semantics (Razorpay exposes no date filters for those endpoints)."""

    def test_all_four_resources_persist_and_count(self, make_service,
                                                  session_factory):
        fetcher = FakeFetcher(
            payment_pages=[[payment(1)]],
            refund_pages=[[refund(1), refund(2)]],
            order_pages=[[order(1), order(2)], [order(3)]],
            settlement_pages=[[settlement(1)]],
        )
        outcome = make_service(fetcher, page_size=2).run_sync()
        assert outcome.status == "success"
        assert (outcome.payments.fetched, outcome.payments.inserted) == (1, 1)
        assert (outcome.refunds.fetched, outcome.refunds.inserted) == (2, 2)
        assert (outcome.orders.fetched, outcome.orders.inserted) == (3, 3)
        assert (outcome.settlements.fetched, outcome.settlements.inserted) == (1, 1)

        # Phase order: refunds run before orders; both before settlements.
        order_of_phases = []
        seen = set()
        for resource, *_ in fetcher.calls:
            if resource not in seen:
                seen.add(resource)
                order_of_phases.append(resource)
        assert order_of_phases == ["payments", "refunds", "orders", "settlements"]

        session = session_factory()
        assert session.query(Refund).count() == 2
        assert session.query(Order).count() == 3

    def test_run_row_records_orders_refunds_counters(self, make_service,
                                                     session_factory):
        fetcher = FakeFetcher(
            payment_pages=[[payment(1)]],
            refund_pages=[[refund(1)]],
            order_pages=[[order(1), order(2)]],
            settlement_pages=[],
        )
        outcome = make_service(fetcher, page_size=2).run_sync()
        session = session_factory()
        run = SyncRunRepository(session).get(outcome.run_id)
        assert (run.refunds_fetched, run.refunds_inserted) == (1, 1)
        assert (run.orders_fetched, run.orders_inserted) == (2, 2)

    def test_orders_refunds_are_non_incremental(self, make_service):
        """skip always restarts at 0 for orders/refunds; only payments carry
        a from_epoch watermark."""
        first = FakeFetcher(
            payment_pages=[[payment(1), payment(2)], [payment(3)]],
            order_pages=[[order(1), order(2)], [order(3)]],
            refund_pages=[[refund(1), refund(2)], [refund(3)]],
        )
        make_service(first, page_size=2, delay=0).run_sync()

        second = FakeFetcher(order_pages=[[order(1)]], refund_pages=[[refund(1)]])
        make_service(second, page_size=2, delay=0).run_sync()

        for resource in ("orders", "refunds"):
            calls = [c for c in second.calls if c[0] == resource]
            assert calls, f"{resource} should be fetched every run"
            assert all(c[2] == 0 for c in calls)  # skip restarts at 0
            assert all(c[3] is None for c in calls)  # no date cursor

        payment_calls = [c for c in second.calls if c[0] == "payments"]
        assert all(c[3] is not None for c in payment_calls)

    def test_identical_refetch_counts_as_skipped(self, make_service,
                                                 session_factory):
        pages = dict(payment_pages=[[payment(1)]], refund_pages=[[refund(1)]],
                     order_pages=[[order(1)]], settlement_pages=[[settlement(1)]])
        make_service(FakeFetcher(**pages)).run_sync()
        outcome = make_service(FakeFetcher(**pages)).run_sync()

        assert outcome.status == "success"
        assert outcome.payments.skipped == 1 and outcome.payments.updated == 0
        assert outcome.refunds.skipped == 1 and outcome.refunds.inserted == 0
        assert outcome.orders.skipped == 1 and outcome.orders.updated == 0

        session = session_factory()
        assert session.query(Order).count() == 1
        assert session.query(Refund).count() == 1

    def test_refund_failure_is_partial_failure(self, make_service,
                                               session_factory):
        fetcher = FakeFetcher(
            payment_pages=[[payment(1)]],
            errors={("refunds", 0): RazorpayServerError(
                "Razorpay API error (HTTP 500)")},
        )
        outcome = make_service(fetcher).run_sync()
        assert outcome.status == "partial_failure"
        session = session_factory()
        assert session.query(Payment).count() == 1  # first phase survived
        assert session.query(Refund).count() == 0
        run = SyncRunRepository(session).get(outcome.run_id)
        assert run.status == "partial_failure"

    def test_order_failure_keeps_earlier_phases(self, make_service,
                                                session_factory):
        fetcher = FakeFetcher(
            payment_pages=[[payment(1)]],
            refund_pages=[[refund(1)]],
            errors={("orders", 0): RazorpayConnectionError("timed out")},
        )
        outcome = make_service(fetcher).run_sync()
        assert outcome.status == "partial_failure"
        session = session_factory()
        assert session.query(Payment).count() == 1
        assert session.query(Refund).count() == 1
        assert session.query(Order).count() == 0
        assert session.query(Settlement).count() == 0


