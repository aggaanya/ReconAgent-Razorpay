"""Data access for :class:`app.db.models.Refund`.

Mirrors :mod:`app.db.repositories.payment`; see it for the upsert and
transaction-ownership conventions.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Refund
from .base import (
    model_values,
    paginate,
    upsert_instance,
    upsert_instance_detailed,
    CountSumAggregate,
    DetailedUpsertResult,
    UpsertOutcome,
    UpsertResult,
)
from .errors import DuplicateRecordError

ENTITY = "Refund"
UNIQUE_FIELD = "razorpay_refund_id"
MAX_PAGE_LIMIT = 500


class RefundRepository:
    """CRUD + idempotent upserts for refunds."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- write path ------------------------------------------------------

    def insert(self, refund: Refund) -> Refund:
        """Insert a new refund; refuse duplicates of the provider id."""
        if self.get_by_razorpay_id(refund.razorpay_refund_id) is not None:
            raise DuplicateRecordError(
                ENTITY, UNIQUE_FIELD, refund.razorpay_refund_id
            )
        self._session.add(refund)
        try:
            self._session.flush()
        except IntegrityError as exc:  # concurrent-writer race
            raise DuplicateRecordError(
                ENTITY, UNIQUE_FIELD, refund.razorpay_refund_id
            ) from exc
        return refund

    @staticmethod
    def _column_values(refund: Refund) -> dict:
        """Mapped columns minus PK/lifecycle timestamps, for upserts."""
        return model_values(refund)

    def upsert(self, refund: Refund) -> tuple[Refund, bool]:
        """Insert or refresh by ``razorpay_refund_id``.

        Returns ``(row, inserted)`` so callers can report accurate counts.
        """
        values = self._column_values(refund)
        return upsert_instance(
            self._session,
            model_cls=Refund,
            values=values,
            unique_field=UNIQUE_FIELD,
            entity_name=ENTITY,
        )

    def upsert_detailed(self, refund: Refund) -> tuple[Refund, UpsertOutcome]:
        """Insert / update-on-change / skip when identical."""
        values = self._column_values(refund)
        return upsert_instance_detailed(
            self._session,
            model_cls=Refund,
            values=values,
            unique_field=UNIQUE_FIELD,
            entity_name=ENTITY,
        )

    def upsert_many_detailed(
        self, refunds: Sequence[Refund]
    ) -> DetailedUpsertResult:
        """Batch variant of :meth:`upsert_detailed`."""
        result = DetailedUpsertResult()
        for refund in refunds:
            _, outcome = self.upsert_detailed(refund)
            setattr(result, outcome, getattr(result, outcome) + 1)
        return result

    def upsert_many(self, refunds: Sequence[Refund]) -> UpsertResult:
        """Two-state batch upsert (inserted/updated), matching payments."""
        result = UpsertResult()
        for refund in refunds:
            _, inserted = self.upsert(refund)
            if inserted:
                result.inserted += 1
            else:
                result.updated += 1
        return result

    # --- read path -------------------------------------------------------

    def get_by_razorpay_id(self, razorpay_refund_id: str) -> Refund | None:
        statement = select(Refund).where(
            Refund.razorpay_refund_id == razorpay_refund_id
        )
        return self._session.execute(statement).scalar_one_or_none()

    def get_by_payment_id(self, razorpay_payment_id: str) -> list[Refund]:
        """All refunds against one payment (net-amount math)."""
        statement = (
            select(Refund)
            .where(Refund.razorpay_payment_id == razorpay_payment_id)
            .order_by(Refund.id)
        )
        return list(self._session.execute(statement).scalars())

    def list_paginated(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[Refund], int]:
        """Stable page by primary key plus total row count."""
        return paginate(
            self._session,
            statement_factory=lambda limit, offset: self._session.execute(
                select(Refund).order_by(Refund.id).limit(limit).offset(offset)
            ).scalars(),
            count_statement_factory=lambda: select(func.count()).select_from(Refund),
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
        payment_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Refund], int]:
        """Bounded page of refunds under finance filters.

        ``method`` is accepted for a uniform finance-router signature;
        Razorpay refunds carry no payment method, so it is ignored here.
        """
        filters = []
        if start is not None:
            filters.append(Refund.provider_created_at >= start)
        if end is not None:
            filters.append(Refund.provider_created_at <= end)
        if currency is not None:
            filters.append(Refund.currency == currency)
        if status is not None:
            filters.append(Refund.status == status)
        if payment_id is not None:
            filters.append(Refund.razorpay_payment_id == payment_id)

        base_where = select(Refund).where(*filters).order_by(Refund.id)
        return paginate(
            self._session,
            statement_factory=lambda limit, offset: self._session.execute(
                base_where.limit(limit).offset(offset)
            ).scalars(),
            count_statement_factory=lambda: (
                select(func.count()).select_from(Refund).where(*filters)
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
        payment_id: str | None,
    ) -> list:
        """Shared WHERE clauses for finance scans (date bounds inclusive)."""
        filters = []
        if start is not None:
            filters.append(Refund.provider_created_at >= start)
        if end is not None:
            filters.append(Refund.provider_created_at <= end)
        if currency is not None:
            filters.append(Refund.currency == currency)
        if status is not None:
            filters.append(Refund.status == status)
        if payment_id is not None:
            filters.append(Refund.razorpay_payment_id == payment_id)
        return filters

    def aggregate_by_currency(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        currency: str | None = None,
        status: str | None = None,
        payment_id: str | None = None,
    ) -> dict[str | None, CountSumAggregate]:
        """Refund count/amount aggregates per currency (exact minor units)."""
        filters = self._finance_filters(
            start=start, end=end, currency=currency, status=status,
            payment_id=payment_id,
        )
        rows = self._session.execute(
            select(
                Refund.currency,
                func.count(),
                func.coalesce(func.sum(Refund.amount_minor), 0),
            )
            .where(*filters)
            .group_by(Refund.currency)
        ).all()
        return {
            row_currency: CountSumAggregate(count=int(row_count), amount_minor=int(total))
            for row_currency, row_count, total in rows
        }
