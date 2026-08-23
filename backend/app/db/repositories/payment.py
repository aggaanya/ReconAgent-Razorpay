"""Data access for :class:`app.db.models.Payment`.

All queries live here; services never write SQLAlchemy inline. Sessions are
injected, flushed but not committed — transaction boundaries belong to the
caller (sync service / request scope).
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Payment
from .base import (
    model_values,
    paginate,
    upsert_instance,
    upsert_instance_detailed,
    DetailedUpsertResult,
    PaymentAggregate,
    UpsertOutcome,
    UpsertResult,
)
from .errors import DuplicateRecordError

ENTITY = "Payment"
UNIQUE_FIELD = "razorpay_payment_id"
MAX_PAGE_LIMIT = 500


class PaymentRepository:
    """CRUD + idempotent upserts for payments."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- write path ------------------------------------------------------

    def insert(self, payment: Payment) -> Payment:
        """Insert a new payment; refuse duplicates of the provider id."""
        if self.get_by_razorpay_id(payment.razorpay_payment_id) is not None:
            raise DuplicateRecordError(
                ENTITY, UNIQUE_FIELD, payment.razorpay_payment_id
            )
        self._session.add(payment)
        try:
            self._session.flush()
        except IntegrityError as exc:  # concurrent-writer race
            raise DuplicateRecordError(
                ENTITY, UNIQUE_FIELD, payment.razorpay_payment_id
            ) from exc
        return payment

    def upsert(self, payment: Payment) -> tuple[Payment, bool]:
        """Insert or refresh by ``razorpay_payment_id``.

        Returns ``(row, inserted)`` so callers can report accurate counts.
        """
        values = self._column_values(payment)
        return upsert_instance(
            self._session,
            model_cls=Payment,
            values=values,
            unique_field=UNIQUE_FIELD,
            entity_name=ENTITY,
        )

    @staticmethod
    def _column_values(payment: Payment) -> dict:
        """Mapped columns minus PK/lifecycle timestamps, for upserts."""
        return model_values(payment)

    def upsert_many(self, payments: Sequence[Payment]) -> UpsertResult:
        """Upsert a batch, returning inserted/updated counts."""
        result = UpsertResult()
        for payment in payments:
            _, inserted = self.upsert(payment)
            if inserted:
                result.inserted += 1
            else:
                result.updated += 1
        return result

    def upsert_detailed(self, payment: Payment) -> tuple[Payment, UpsertOutcome]:
        """Insert / update-on-change / skip when identical.

        Returns ``(row, outcome)`` with ``outcome`` one of
        ``"inserted" | "updated" | "skipped"``.
        """
        values = self._column_values(payment)
        return upsert_instance_detailed(
            self._session,
            model_cls=Payment,
            values=values,
            unique_field=UNIQUE_FIELD,
            entity_name=ENTITY,
        )

    def upsert_many_detailed(
        self, payments: Sequence[Payment]
    ) -> DetailedUpsertResult:
        """Batch variant of :meth:`upsert_detailed`."""
        result = DetailedUpsertResult()
        for payment in payments:
            _, outcome = self.upsert_detailed(payment)
            setattr(result, outcome, getattr(result, outcome) + 1)
        return result

    # --- read path -------------------------------------------------------

    def get_by_razorpay_id(self, razorpay_payment_id: str) -> Payment | None:
        statement = select(Payment).where(
            Payment.razorpay_payment_id == razorpay_payment_id
        )
        return self._session.execute(statement).scalar_one_or_none()

    def list_paginated(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[Payment], int]:
        """Stable page by primary key plus total row count."""
        return paginate(
            self._session,
            statement_factory=lambda limit, offset: self._session.execute(
                select(Payment).order_by(Payment.id).limit(limit).offset(offset)
            ).scalars(),
            count_statement_factory=lambda: select(func.count()).select_from(Payment),
            limit=limit,
            offset=offset,
        )

    def list_filtered(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        currency: str | None = None,
        status: str | None = None,
        method: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Payment], int]:
        """Bounded page of payments under finance filters.

        ``start``/``end`` bound ``provider_created_at`` (inclusive); the rest
        are exact matches when supplied.
        """
        filters = self._finance_filters(
            start=start, end=end, currency=currency, status=status, method=method
        )

        base_where = select(Payment).where(*filters).order_by(Payment.id)
        return paginate(
            self._session,
            statement_factory=lambda limit, offset: self._session.execute(
                base_where.limit(limit).offset(offset)
            ).scalars(),
            count_statement_factory=lambda: (
                select(func.count()).select_from(Payment).where(*filters)
            ),
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def _finance_filters(
        *,
        start: datetime | None,
        end: datetime | None,
        currency: str | None,
        status: str | None,
        method: str | None,
    ) -> list:
        """Shared WHERE clauses for finance scans (date bounds inclusive)."""
        filters = []
        if start is not None:
            filters.append(Payment.provider_created_at >= start)
        if end is not None:
            filters.append(Payment.provider_created_at <= end)
        if currency is not None:
            filters.append(Payment.currency == currency)
        if status is not None:
            filters.append(Payment.status == status)
        if method is not None:
            filters.append(Payment.method == method)
        return filters

    def aggregate_by_currency(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        currency: str | None = None,
        status: str | None = None,
        method: str | None = None,
    ) -> dict[str | None, PaymentAggregate]:
        """Finance aggregates per currency over the filtered window.

        Keys are the stored ``currency`` values (``None`` included when the
        provider omitted it). All sums are exact minor-unit integers.
        """
        filters = self._finance_filters(
            start=start, end=end, currency=currency, status=status, method=method
        )
        captured = Payment.status == "captured"
        failed = Payment.status == "failed"
        rows = self._session.execute(
            select(
                Payment.currency,
                func.count(),
                func.coalesce(func.sum(Payment.amount_minor), 0),
                func.coalesce(
                    func.sum(case((captured, Payment.amount_minor), else_=0)), 0
                ),
                func.coalesce(func.sum(case((captured, 1), else_=0)), 0),
                func.coalesce(func.sum(case((failed, 1), else_=0)), 0),
            )
            .where(*filters)
            .group_by(Payment.currency)
        ).all()
        return {
            row_currency: PaymentAggregate(
                count=int(row_count),
                amount_minor=int(gross),
                captured_amount_minor=int(captured_amount),
                captured_count=int(captured_count),
                failed_count=int(failed_count),
            )
            for row_currency, row_count, gross, captured_amount, captured_count, failed_count in rows
        }
