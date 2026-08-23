"""Shared repository plumbing: upsert strategy and result types.

Upsert strategy — portable and race-aware-enough:

1. SELECT the row by the provider-unique column.
2. Missing  -> INSERT (an ``IntegrityError`` from a concurrent writer is
   translated to :class:`DuplicateRecordError`; callers that hit it while
   another transaction commits should retry as an update).
3. Present  -> copy every mapped column onto the existing row (UPDATE).

This keeps behavior identical across PostgreSQL (production) and SQLite
(unit tests) without dialect-specific ``ON CONFLICT`` statements, and makes
re-running a sync over already-stored data naturally idempotent.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .errors import DuplicateRecordError

# Outcome of a change-detecting upsert.
UpsertOutcome = Literal["inserted", "updated", "skipped"]


@dataclass
class UpsertResult:
    """Aggregate outcome of a batch upsert."""

    inserted: int = 0
    updated: int = 0

    @property
    def total(self) -> int:
        return self.inserted + self.updated


@dataclass
class DetailedUpsertResult:
    """Aggregate outcome of a batch change-detecting upsert."""

    inserted: int = 0
    updated: int = 0
    skipped: int = 0

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.skipped


@dataclass(frozen=True)
class CountSumAggregate:
    """Row count + summed ``amount_minor`` for one slice of records."""

    count: int = 0
    amount_minor: int = 0


@dataclass(frozen=True)
class PaymentAggregate(CountSumAggregate):
    """Payment-specific finance aggregates.

    ``captured_*`` uses Razorpay's literal ``captured`` status (the only
    state meaning money actually moved in); ``failed_count`` its literal
    ``failed`` status.
    """

    captured_amount_minor: int = 0
    captured_count: int = 0
    failed_count: int = 0


def upsert_instance(
    session: Session,
    *,
    model_cls: type,
    values: dict[str, Any],
    unique_field: str,
    entity_name: str,
) -> tuple[Any, bool]:
    """Insert or update one row keyed on ``unique_field``.

    Returns ``(instance, inserted)``. Flushes so defaults/ids are loaded;
    transaction commit stays with the caller.
    """
    unique_value = values[unique_field]
    lookup = getattr(model_cls, unique_field)
    existing = session.execute(
        select(model_cls).where(lookup == unique_value)
    ).scalar_one_or_none()

    if existing is None:
        instance = model_cls(**values)
        session.add(instance)
        try:
            session.flush()
        except IntegrityError as exc:
            # Concurrent writer won the race; surface as a typed error.
            raise DuplicateRecordError(
                entity_name, unique_field, str(unique_value)
            ) from exc
        return instance, True

    for field, value in values.items():
        setattr(existing, field, value)
    session.flush()
    return existing, False


def _as_aware_utc(value: datetime) -> datetime:
    """Interpret naive datetimes as UTC so storage dialects compare equal."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# Columns that describe row lifecycle rather than synced data; never copied
# between rows by upserts.
LIFECYCLE_COLUMNS = frozenset({"id", "created_at", "updated_at"})


def model_values(model: Any) -> dict[str, Any]:
    """Mapped columns of ``model`` prepared for an upsert.

    Lifecycle columns are excluded. A subtle but important detail: unset
    non-nullable columns with Python-side defaults (e.g. ``notes`` built as
    ``{}`` only at flush time) read as ``None`` on a transient instance.
    Writing that ``None`` through would clobber the stored default and fake
    a change on every re-sync, so declared defaults are materialized here.
    """
    values: dict[str, Any] = {}
    for column in model.__table__.columns:
        key = column.key
        if key in LIFECYCLE_COLUMNS:
            continue
        value = getattr(model, key)
        if (
            value is None
            and not column.nullable
            and column.default is not None
        ):
            # Scalar defaults are plain values; callable defaults are
            # invoked through SQLAlchemy's context-accepting wrapper.
            if column.default.is_scalar:
                value = column.default.arg
            else:
                value = column.default.arg(None)
        values[key] = value
    return values


def _values_equal(current: Any, incoming: Any) -> bool:
    """Column-value equality tolerant of driver round-trip quirks.

    Datetimes may come back naive from SQLite while incoming normalized
    values are tz-aware; both sides are compared in aware UTC. JSON columns
    round-trip as plain dicts and compare structurally.
    """
    if current is None or incoming is None:
        return current is None and incoming is None
    if isinstance(current, datetime) and isinstance(incoming, datetime):
        return _as_aware_utc(current) == _as_aware_utc(incoming)
    return current == incoming


def upsert_instance_detailed(
    session: Session,
    *,
    model_cls: type,
    values: dict[str, Any],
    unique_field: str,
    entity_name: str,
) -> tuple[Any, UpsertOutcome]:
    """Change-detecting variant of :func:`upsert_instance`.

    Returns ``(instance, outcome)`` where outcome distinguishes a fresh
    insert, an update with at least one changed column, and a no-op
    (``skipped``) when every mapped column already matches. Unchanged rows
    are left untouched — no dirty attributes, no UPDATE statement.
    """
    unique_value = values[unique_field]
    lookup = getattr(model_cls, unique_field)
    existing = session.execute(
        select(model_cls).where(lookup == unique_value)
    ).scalar_one_or_none()

    if existing is None:
        instance = model_cls(**values)
        session.add(instance)
        try:
            session.flush()
        except IntegrityError as exc:
            raise DuplicateRecordError(
                entity_name, unique_field, str(unique_value)
            ) from exc
        return instance, "inserted"

    changed = False
    for field, value in values.items():
        if not _values_equal(getattr(existing, field), value):
            setattr(existing, field, value)
            changed = True
    if changed:
        session.flush()
    return existing, "updated" if changed else "skipped"


def paginate(
    session: Session,
    *,
    statement_factory: Callable[[int, int], Sequence[Any]],
    count_statement_factory: Callable[[], Any],
    limit: int,
    offset: int,
    max_limit: int = 500,
) -> tuple[list[Any], int]:
    """Apply bounded pagination shared by list endpoints."""
    if not 1 <= limit <= max_limit:
        raise ValueError(f"limit must be between 1 and {max_limit}")
    if offset < 0:
        raise ValueError("offset must be non-negative")
    items = list(statement_factory(limit, offset))
    total = session.execute(count_statement_factory()).scalar_one()
    return items, int(total)
