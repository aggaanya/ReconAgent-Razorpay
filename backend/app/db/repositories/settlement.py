"""Data access for :class:`app.db.models.Settlement`.

Mirrors :mod:`app.db.repositories.payment`; see it for the upsert and
transaction-ownership conventions.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Settlement
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

ENTITY = "Settlement"
UNIQUE_FIELD = "razorpay_settlement_id"
MAX_PAGE_LIMIT = 500


class SettlementRepository:
    """CRUD + idempotent upserts for settlements."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- write path ------------------------------------------------------

    def insert(self, settlement: Settlement) -> Settlement:
        """Insert a new settlement; refuse duplicates of the provider id."""
        if (
            self.get_by_razorpay_id(settlement.razorpay_settlement_id) is not None
        ):
            raise DuplicateRecordError(
                ENTITY, UNIQUE_FIELD, settlement.razorpay_settlement_id
            )
        self._session.add(settlement)
        try:
            self._session.flush()
        except IntegrityError as exc:  # concurrent-writer race
            raise DuplicateRecordError(
                ENTITY, UNIQUE_FIELD, settlement.razorpay_settlement_id
            ) from exc
        return settlement

    def upsert(self, settlement: Settlement) -> tuple[Settlement, bool]:
        """Insert or refresh by ``razorpay_settlement_id``."""
        values = self._column_values(settlement)
        return upsert_instance(
            self._session,
            model_cls=Settlement,
            values=values,
            unique_field=UNIQUE_FIELD,
            entity_name=ENTITY,
        )

    @staticmethod
    def _column_values(settlement: Settlement) -> dict:
        """Mapped columns minus PK/lifecycle timestamps, for upserts."""
        return model_values(settlement)

    def upsert_detailed(
        self, settlement: Settlement
    ) -> tuple[Settlement, UpsertOutcome]:
        """Insert / update-on-change / skip when identical."""
        values = self._column_values(settlement)
        return upsert_instance_detailed(
            self._session,
            model_cls=Settlement,
            values=values,
            unique_field=UNIQUE_FIELD,
            entity_name=ENTITY,
        )

    def upsert_many_detailed(
        self, settlements: Sequence[Settlement]
    ) -> DetailedUpsertResult:
        """Batch variant of :meth:`upsert_detailed`."""
        result = DetailedUpsertResult()
        for settlement in settlements:
            _, outcome = self.upsert_detailed(settlement)
            setattr(result, outcome, getattr(result, outcome) + 1)
        return result

    def upsert_many(self, settlements: Sequence[Settlement]) -> UpsertResult:
        result = UpsertResult()
        for settlement in settlements:
            _, inserted = self.upsert(settlement)
            if inserted:
                result.inserted += 1
            else:
                result.updated += 1
        return result

    def get_by_razorpay_id(self, razorpay_settlement_id: str) -> Settlement | None:
        statement = select(Settlement).where(
            Settlement.razorpay_settlement_id == razorpay_settlement_id
        )
        return self._session.execute(statement).scalar_one_or_none()

    def get_by_utr(self, utr: str) -> list[Settlement]:
        """All settlements sharing a bank UTR (reconciliation join key)."""
        statement = select(Settlement).where(Settlement.utr == utr).order_by(Settlement.id)
        return list(self._session.execute(statement).scalars())

    def list_paginated(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[Settlement], int]:
        return paginate(
            self._session,
            statement_factory=lambda limit, offset: self._session.execute(
                select(Settlement)
                .order_by(Settlement.id)
                .limit(limit)
                .offset(offset)
            ).scalars(),
            count_statement_factory=lambda: select(func.count()).select_from(
                Settlement
            ),
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
    ) -> tuple[list[Settlement], int]:
        """Bounded page of settlements under finance filters.

        ``method`` is accepted for a uniform finance-router signature;
        settlements have no payment method, so it is ignored here.
        """
        filters = self._finance_filters(
            start=start, end=end, currency=currency, status=status
        )

        base_where = select(Settlement).where(*filters).order_by(Settlement.id)
        return paginate(
            self._session,
            statement_factory=lambda limit, offset: self._session.execute(
                base_where.limit(limit).offset(offset)
            ).scalars(),
            count_statement_factory=lambda: (
                select(func.count()).select_from(Settlement).where(*filters)
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
    ) -> list:
        """Shared WHERE clauses for finance scans (date bounds inclusive)."""
        filters = []
        if start is not None:
            filters.append(Settlement.provider_created_at >= start)
        if end is not None:
            filters.append(Settlement.provider_created_at <= end)
        if currency is not None:
            filters.append(Settlement.currency == currency)
        if status is not None:
            filters.append(Settlement.status == status)
        return filters

    def aggregate_by_currency(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        currency: str | None = None,
        status: str | None = None,
    ) -> dict[str | None, CountSumAggregate]:
        """Settlement amount aggregates per currency (exact minor units)."""
        filters = self._finance_filters(
            start=start, end=end, currency=currency, status=status
        )
        rows = self._session.execute(
            select(
                Settlement.currency,
                func.count(),
                func.coalesce(func.sum(Settlement.amount_minor), 0),
            )
            .where(*filters)
            .group_by(Settlement.currency)
        ).all()
        return {
            row_currency: CountSumAggregate(count=int(row_count), amount_minor=int(total))
            for row_currency, row_count, total in rows
        }
