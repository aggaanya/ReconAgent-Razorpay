"""Alembic migration tests.

Two tiers:

1. Offline SQL generation (no database required): the migration script must
   emit DDL for every table/index/constraint and a symmetric downgrade.
2. Live round-trip against real PostgreSQL, skipped unless
   ``RECONAGENT_TEST_DATABASE_URL`` is set (same gate as test_db.py).
"""

import io
import os
from contextlib import redirect_stdout

import pytest
from alembic import command
from alembic.config import Config

from app.core.config import get_settings

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALEMBIC_INI = os.path.join(BACKEND_DIR, "alembic.ini")
TEST_DATABASE_URL = os.environ.get("RECONAGENT_TEST_DATABASE_URL")

requires_postgres = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="RECONAGENT_TEST_DATABASE_URL not set; requires a running PostgreSQL",
)


def _alembic_config(database_url: str) -> Config:
    config = Config(ALEMBIC_INI)
    config.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    # env.py reads DATABASE_URL from settings; point it at our target.
    # Settings are lru_cached, so the cache must be dropped after changing
    # the environment (other tests may have populated it already).
    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    return config


def _generate_sql(config: Config, target: str) -> str:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        command.upgrade(config, target, sql=True)
    return buffer.getvalue()


class TestOfflineSqlGeneration:
    @pytest.fixture(autouse=True)
    def _restore_env(self):
        original = os.environ.get("DATABASE_URL")
        yield
        if original is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original
        get_settings.cache_clear()

    def test_upgrade_sql_creates_all_tables(self):
        config = _alembic_config(
            "postgresql+psycopg2://u:p@localhost:5432/offline_check"
        )
        sql = _generate_sql(config, "head").lower()
        for table in ("payments", "settlements", "sync_runs"):
            assert f"create table {table}" in sql

    def test_upgrade_sql_contains_uniqueness_and_indexes(self):
        config = _alembic_config(
            "postgresql+psycopg2://u:p@localhost:5432/offline_check"
        )
        sql = _generate_sql(config, "head").lower()
        # Provider-id uniqueness (idempotency foundation).
        assert "unique" in sql
        assert "razorpay_payment_id" in sql
        assert "razorpay_settlement_id" in sql
        # Deliberate reconciliation indexes.
        for index in (
            "ix_payments_order_id",
            "ix_payments_invoice_id",
            "ix_payments_provider_created_at",
            "ix_settlements_utr",
            "ix_settlements_provider_created_at",
            "ix_sync_runs_started_at",
        ):
            assert index in sql

    def test_money_columns_are_bigint_never_float(self):
        config = _alembic_config(
            "postgresql+psycopg2://u:p@localhost:5432/offline_check"
        )
        sql = _generate_sql(config, "head").lower()
        assert "bigint" in sql
        assert "float" not in sql
        assert "double precision" not in sql
        assert "numeric" not in sql  # minor-unit integers by design

    def test_downgrade_sql_drops_all_tables(self):
        config = _alembic_config(
            "postgresql+psycopg2://u:p@localhost:5432/offline_check"
        )
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            command.downgrade(config, "head:base", sql=True)
        sql = buffer.getvalue().lower()
        for table in ("payments", "settlements", "sync_runs"):
            assert f"drop table {table}" in sql


@requires_postgres
class TestLiveMigrationRoundTrip:
    @pytest.fixture(autouse=True)
    def _restore_env(self):
        original = os.environ.get("DATABASE_URL")
        yield
        if original is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original
        get_settings.cache_clear()

    def test_upgrade_head_is_current_and_repeatable(self):
        config = _alembic_config(TEST_DATABASE_URL)
        command.upgrade(config, "head")  # idempotent when already applied
        from alembic.script import ScriptDirectory
        from alembic.runtime.migration import MigrationContext

        # Verify the version table points at head via a fresh connection.
        from sqlalchemy import create_engine

        engine = create_engine(TEST_DATABASE_URL)
        try:
            with engine.connect() as connection:
                context = MigrationContext.configure(connection)
                current = context.get_current_revision()
        finally:
            engine.dispose()
        script = ScriptDirectory.from_config(config)
        assert current == script.get_current_head()

    def test_downgrade_then_upgrade_round_trip(self):
        """Full teardown + rebuild proves the migration is self-contained."""
        config = _alembic_config(TEST_DATABASE_URL)
        command.downgrade(config, "base")
        command.upgrade(config, "head")

        from sqlalchemy import create_engine, inspect, text

        engine = create_engine(TEST_DATABASE_URL)
        try:
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            assert {"payments", "settlements", "sync_runs"} <= tables
            with engine.connect() as connection:
                connection.execute(
                    text(
                        "INSERT INTO payments (razorpay_payment_id, amount_minor,"
                        " notes) VALUES ('pay_smoketest', 12345, '{}')"
                    )
                )
                amount = connection.execute(
                    text(
                        "SELECT amount_minor FROM payments "
                        "WHERE razorpay_payment_id = 'pay_smoketest'"
                    )
                ).scalar_one()
        finally:
            engine.dispose()
        assert amount == 12345
