"""SQLAlchemy declarative base.

Domain models (Phase 2+) import ``Base`` from here so Alembic can discover
the full metadata via a single, stable location. No models live in this
module.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all ORM models."""
