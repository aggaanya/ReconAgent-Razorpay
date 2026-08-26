"""Shared pytest fixtures."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db.base import Base
import app.db.models  # noqa: F401  (register models on the metadata)
from app.main import app as fastapi_app


@pytest.fixture
def client(monkeypatch):
    """TestClient with lifespan executed and an isolated settings cache."""
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("HF_TOKEN", "")
    monkeypatch.setenv("HF_MODEL", "")
    monkeypatch.setenv("HF_BASE_URL", "")
    get_settings.cache_clear()
    with TestClient(fastapi_app) as test_client:
        yield test_client
    get_settings.cache_clear()


@pytest.fixture
def session_factory():
    """Session factory over a fresh in-memory SQLite database.

    The ORM models are written portable (JSON/timestamps variants), so the
    full repository + service stack is exercisable without PostgreSQL.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()
