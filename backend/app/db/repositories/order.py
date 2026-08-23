"""Data access for :class:`app.db.models.Order`.

Mirrors :mod:`app.db.repositories.payment`; see it for the upsert and
transaction-ownership conventions.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Order
from .base import (
    model_values,
    paginate,
    upsert_instance,
    upsert_instance_detailed,
    DetailedUpsertResult,
    UpsertOutcome,
    UpsertResult,
)
from .errors import DuplicateRecordError

ENTITY = "Order"
UNIQUE_FIELD = "razorpay_order_id"
MAX_PAGE_LIMIT = 500


class OrderRepository:
    """CRUD + idempotent upserts for orders."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- write path ------------------------------------------------------

    def insert(self, order: Order) -> Order:
        """Insert a new order; refuse duplicates of the provider id."""
        if self.get_by_razorpay_id(order.razorpay_order_id) is not None:
            raise DuplicateRecordError(
                ENTITY, UNIQUE_FIELD, order.razorpay_order_id
            )
        self._session.add(order)
        try:
            self._session.flush()
        except IntegrityError as exc:  # concurrent-writer race
            raise DuplicateRecordError(
                ENTITY, UNIQUE_FIELD, order.razorpay_order_id
            ) from exc
        return order

    @staticmethod
    def _column_values(order: Order) -> dict:
        """Mapped columns minus PK/lifecycle timestamps, for upserts."""
        return model_values(order)

    def upsert(self, order: Order) -> tuple[Order, bool]:
        """Insert or refresh by ``razorpay_order_id``.

        Returns ``(row, inserted)`` so callers can report accurate counts.
        """
        values = self._column_values(order)
        return upsert_instance(
            self._session,
            model_cls=Order,
            values=values,
            unique_field=UNIQUE_FIELD,
            entity_name=ENTITY,
        )

    def upsert_detailed(self, order: Order) -> tuple[Order, UpsertOutcome]:
        """Insert / update-on-change / skip when identical."""
        values = self._column_values(order)
        return upsert_instance_detailed(
            self._session,
            model_cls=Order,
            values=values,
            unique_field=UNIQUE_FIELD,
            entity_name=ENTITY,
        )

    def upsert_many_detailed(self, orders: Sequence[Order]) -> DetailedUpsertResult:
        """Batch variant of :meth:`upsert_detailed`."""
        result = DetailedUpsertResult()
        for order in orders:
            _, outcome = self.upsert_detailed(order)
            setattr(result, outcome, getattr(result, outcome) + 1)
        return result

    def upsert_many(self, orders: Sequence[Order]) -> UpsertResult:
        """Two-state batch upsert (inserted/updated), matching payments."""
        result = UpsertResult()
        for order in orders:
            _, inserted = self.upsert(order)
            if inserted:
                result.inserted += 1
            else:
                result.updated += 1
        return result

    # --- read path -------------------------------------------------------

    def get_by_razorpay_id(self, razorpay_order_id: str) -> Order | None:
        statement = select(Order).where(
            Order.razorpay_order_id == razorpay_order_id
        )
        return self._session.execute(statement).scalar_one_or_none()

    def list_paginated(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[Order], int]:
        """Stable page by primary key plus total row count."""
        return paginate(
            self._session,
            statement_factory=lambda limit, offset: self._session.execute(
                select(Order).order_by(Order.id).limit(limit).offset(offset)
            ).scalars(),
            count_statement_factory=lambda: select(func.count()).select_from(Order),
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
        receipt: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Order], int]:
        """Bounded page of orders under finance filters.

        ``method`` is accepted for a uniform finance-router signature;
        Razorpay orders carry no payment method, so it is ignored here.
        """
        filters = []
        if start is not None:
            filters.append(Order.provider_created_at >= start)
        if end is not None:
            filters.append(Order.provider_created_at <= end)
        if currency is not None:
            filters.append(Order.currency == currency)
        if status is not None:
            filters.append(Order.status == status)
        if receipt is not None:
            filters.append(Order.receipt == receipt)

        base_where = select(Order).where(*filters).order_by(Order.id)
        return paginate(
            self._session,
            statement_factory=lambda limit, offset: self._session.execute(
                base_where.limit(limit).offset(offset)
            ).scalars(),
            count_statement_factory=lambda: (
                select(func.count()).select_from(Order).where(*filters)
            ),
            limit=limit,
            offset=offset,
        )
