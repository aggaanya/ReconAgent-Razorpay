"""Schema-level assertions for the ORM models (no database required).

Verifies the persistence contract: exact table/column names, decimal-safe
money types (integer minor units, never float), provider-id uniqueness,
deliberate indexes, and timestamp bookkeeping.
"""

import pytest
from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from app.db.models import Payment, Settlement


def columns_of(model):
    return model.__table__.columns


class TestPaymentsTable:
    def test_table_name(self):
        assert Payment.__tablename__ == "payments"

    def test_money_columns_are_exact_integer_types(self):
        for name in ("amount_minor",):
            column = columns_of(Payment)[name]
            assert isinstance(column.type, BigInteger)
            assert not column.nullable

    def test_no_floating_point_columns_anywhere(self):
        for column in columns_of(Payment):
            assert "FLOAT" not in str(column.type).upper()
            assert "REAL" not in str(column.type).upper()

    def test_razorpay_payment_id_is_unique_and_required(self):
        column = columns_of(Payment)["razorpay_payment_id"]
        assert not column.nullable
        uniques = {
            constraint.columns.keys()[0]: constraint
            for constraint in Payment.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert "razorpay_payment_id" in uniques
        assert (
            uniques["razorpay_payment_id"].name == "uq_payments_razorpay_payment_id"
        )

    def test_deliberate_indexes_only(self):
        index_names = {index.name for index in Payment.__table__.indexes}
        # Lookup paths for reconciliation: order/invoice correlation and
        # incremental sync windows. Nothing else is indexed blindly.
        assert index_names == {
            "ix_payments_order_id",
            "ix_payments_invoice_id",
            "ix_payments_provider_created_at",
        }

    def test_provider_created_at_distinct_from_bookkeeping(self):
        columns = columns_of(Payment)
        assert isinstance(columns["provider_created_at"].type, DateTime)
        assert isinstance(columns["created_at"].type, DateTime)
        assert isinstance(columns["updated_at"].type, DateTime)

    def test_notes_render_jsonb_on_postgresql(self):
        notes = columns_of(Payment)["notes"]
        dialect = pytest.importorskip("sqlalchemy")  # keeps lint quiet
        del dialect
        pg_type = notes.type.dialect_impl(_pg_dialect())
        assert isinstance(pg_type, JSONB)


class TestSettlementsTable:
    def test_table_name(self):
        assert Settlement.__tablename__ == "settlements"

    def test_money_columns_are_exact_integer_types(self):
        for name in ("amount_minor", "fees_minor", "tax_minor"):
            column = columns_of(Settlement)[name]
            assert isinstance(column.type, BigInteger)
            assert not column.nullable

    def test_no_floating_point_columns_anywhere(self):
        for column in columns_of(Settlement):
            assert "FLOAT" not in str(column.type).upper()

    def test_razorpay_settlement_id_unique(self):
        names = {
            c.name
            for c in Settlement.__table__.constraints
            if c.__class__.__name__ == "UniqueConstraint"
        }
        assert "uq_settlements_razorpay_settlement_id" in names or any(
            getattr(c, "name", None) == "uq_settlements_razorpay_settlement_id"
            for c in Settlement.__table__.constraints
        )

    def test_utr_indexed_for_reconciliation_join(self):
        index_names = {index.name for index in Settlement.__table__.indexes}
        assert index_names == {
            "ix_settlements_utr",
            "ix_settlements_provider_created_at",
        }


class TestTimestampMixin:
    def test_created_and_updated_present_on_all_models(self):
        for model in (Payment, Settlement):
            columns = columns_of(model)
            assert {"created_at", "updated_at"} <= set(columns.keys())


# --- helpers -----------------------------------------------------------


def _pg_dialect():
    from sqlalchemy.dialects import postgresql

    return postgresql.dialect()
