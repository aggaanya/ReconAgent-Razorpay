"""Database infrastructure.

- :mod:`app.db.base` — declarative base
- :mod:`app.db.session` — engine/session management
- :mod:`app.db.models` — ORM schema (register on import)
- :mod:`app.db.mappers` — domain schema -> ORM mapping
- :mod:`app.db.repositories` — all data access
- Alembic migrations live at ``backend/alembic``
"""

from .base import Base

__all__ = ["Base"]
