"""Shared pytest fixtures."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app as fastapi_app


@pytest.fixture
def client():
    """TestClient with lifespan executed and an isolated settings cache."""
    get_settings.cache_clear()
    with TestClient(fastapi_app) as test_client:
        yield test_client
    get_settings.cache_clear()
