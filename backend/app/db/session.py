"""Database engine, session factory, and FastAPI session dependency.

Design:

- The engine is created lazily from ``Settings.database_url`` and reused for
  the lifetime of the process (never per request).
- ``get_db`` opens one session per request, rolls back on error, and always
  closes. Transaction ownership (commit) stays with services/repositories.
- ``reset_engine`` disposes the pool on application shutdown and in tests.
- Connection failures are logged without the URL or credentials; callers get
  a boolean only.
"""

import logging
from collections.abc import Iterator
from typing import cast

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


class DatabaseNotConfiguredError(RuntimeError):
    """Raised when the engine is requested but DATABASE_URL is not set."""


def get_engine() -> Engine:
    """Return the shared engine, creating it on first use.

    Raises :class:`DatabaseNotConfiguredError` when DATABASE_URL is absent so
    Phase-1-style operation without a database keeps working.
    """
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        if not settings.database_url:
            raise DatabaseNotConfiguredError(
                "DATABASE_URL is not configured; database features are disabled"
            )
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 3},
        )
        _session_factory = sessionmaker(bind=_engine)
        logger.info("Database engine initialized")
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the session factory bound to the shared engine."""
    get_engine()
    return cast(sessionmaker[Session], _session_factory)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session.

    Commits are not issued here; the caller (service/repository) owns the
    transaction boundary. Any exception triggers a rollback before the
    session is closed.
    """
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database_connection() -> bool:
    """Run a lightweight connectivity probe (``SELECT 1``).

    Never raises: failures are logged server-side and reported as ``False``
    so HTTP responses stay free of driver/credential details.
    """
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except (SQLAlchemyError, OSError):
        logger.warning("Database connectivity check failed", exc_info=True)
        return False


def reset_engine() -> None:
    """Dispose the pooled engine and drop cached factories."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
        logger.info("Database engine disposed")
    _engine = None
    _session_factory = None
