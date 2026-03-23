"""Shared pytest fixtures."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Event loop (required for async tests in pytest-asyncio)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


# ---------------------------------------------------------------------------
# Mock settings so tests don't read real .env or filesystem
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_settings(monkeypatch):
    """Patch get_settings() to return a lightweight test config."""
    from app.config import Settings

    test_settings = Settings(
        ollama_cloud_base_url="http://test-llm",
        ollama_cloud_api_key="test-key",
        ollama_cloud_model="test-model",
        ollama_embed_base_url="http://test-embed",
        ollama_embed_model="test-embed-model",
        ollama_embed_dimensions=4,
        embedding_dimension=4,
        lancedb_path="/tmp/test-lancedb",
        documents_path="/tmp/test-docs",
        google_client_id="",
        google_client_secret="",
        jwt_private_key_path="/nonexistent",
        jwt_public_key_path="/nonexistent",
        jwt_expiry_minutes=10,
        jwt_refresh_expiry_days=7,
        frontend_origin="http://localhost:3000",
        allowed_folder_roots="/tmp",
        max_file_size_mb=10,
        rate_limit_per_user=60,
        rate_limit_window_seconds=60,
        rust_log="error",
    )
    monkeypatch.setattr("app.config.get_settings", lambda: test_settings)
    # Also patch module-level settings captured at import time
    monkeypatch.setattr("app.db.lancedb_client.settings", test_settings)
    monkeypatch.setattr("app.auth.jwt.settings", test_settings)
    monkeypatch.setattr("app.core.path_sanitizer.settings", test_settings)
    monkeypatch.setattr("app.routers.auth.settings", test_settings)
    return test_settings


# ---------------------------------------------------------------------------
# Fake authenticated user for dependency injection
# ---------------------------------------------------------------------------

FAKE_USER = {"id": "test-user-id", "email": "test@example.com", "name": "Test User"}


@pytest.fixture
def fake_user():
    return FAKE_USER
