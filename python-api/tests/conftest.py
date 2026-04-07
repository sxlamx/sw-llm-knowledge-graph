"""Shared pytest fixtures."""

import asyncio
import pytest
import tempfile
from pathlib import Path
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
        hf_embed_model="sentence-transformers/all-MiniLM-L6-v2",
        hf_token="",
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
        sentry_dsn="",
        log_dir="/tmp/test-logs",
    )
    monkeypatch.setattr("app.config.get_settings", lambda: test_settings)
    # Also patch module-level settings captured at import time
    monkeypatch.setattr("app.db.lancedb_client.settings", test_settings)
    monkeypatch.setattr("app.auth.jwt.settings", test_settings)
    monkeypatch.setattr("app.core.path_sanitizer.settings", test_settings)
    monkeypatch.setattr("app.routers.auth.settings", test_settings)
    monkeypatch.setattr("app.llm.embedder.settings", test_settings)
    monkeypatch.setattr("app.pipeline.ingest_worker.settings", test_settings)
    return test_settings


# ---------------------------------------------------------------------------
# Fake authenticated user for dependency injection
# ---------------------------------------------------------------------------

FAKE_USER = {"id": "test-user-id", "email": "test@example.com", "name": "Test User"}


@pytest.fixture
def fake_user():
    return FAKE_USER


# ---------------------------------------------------------------------------
# Authenticated test client (with valid JWT)
# ---------------------------------------------------------------------------

@pytest.fixture
def authed_client(mock_settings):
    """FastAPI TestClient with a valid JWT auth header for the fake user.

    Uses dependency_overrides to inject the fake user directly, bypassing
    the need for real JWT signing/verification in route-level tests.
    """
    from fastapi import FastAPI
    from app.auth.middleware import get_current_user

    # We import routers lazily to avoid creating the app before patches are applied
    from app.routers.collections import router as collections_router
    from app.routers.ingest import router as ingest_router
    from app.routers.documents import router as documents_router
    from app.routers.search import router as search_router

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER

    app.include_router(collections_router, prefix="/api/v1/collections")
    app.include_router(ingest_router, prefix="/api/v1/ingest")
    app.include_router(documents_router, prefix="/api/v1/documents")
    app.include_router(search_router, prefix="/api/v1/search")

    return app


# ---------------------------------------------------------------------------
# Temporary PDF folder for ingest testing
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_pdf_folder(tmp_path):
    """Create a folder with 2 sample PDF files for ingest testing.

    Creates:
        tmp_path/
          sample1.pdf  (minimal valid PDF)
          sample2.pdf  (minimal valid PDF)
          subfolder/
            sample3.pdf

    Returns the Path to the folder.
    """
    folder = tmp_path / "pdfs"
    folder.mkdir()
    subfolder = folder / "subfolder"
    subfolder.mkdir()

    # Minimal PDF content (header + one page)
    minimal_pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj\n"
        b"<< /Type /Catalog /Pages 2 0 R >>\n"
        b"endobj\n"
        b"2 0 obj\n"
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>\n"
        b"endobj\n"
        b"3 0 obj\n"
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\n"
        b"endobj\n"
        b"4 0 obj\n"
        b"<< /Length 44 >>\n"
        b"stream\n"
        b"BT /F1 12 Tf 100 700 Td (Test PDF) Tj ET\n"
        b"endstream\n"
        b"endobj\n"
        b"5 0 obj\n"
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\n"
        b"endobj\n"
        b"xref\n"
        b"0 6\n"
        b"0000000000 65535 f\n"
        b"0000000009 00000 n\n"
        b"0000000058 00000 n\n"
        b"0000000115 00000 n\n"
        b"0000000266 00000 n\n"
        b"0000000360 00000 n\n"
        b"trailer\n"
        b"<< /Size 6 /Root 1 0 R >>\n"
        b"startxref\n"
        b"445\n"
        b"%%EOF\n"
    )

    (folder / "sample1.pdf").write_bytes(minimal_pdf)
    (folder / "sample2.pdf").write_bytes(minimal_pdf)
    (subfolder / "sample3.pdf").write_bytes(minimal_pdf)

    return folder


# ---------------------------------------------------------------------------
# Mock LanceDB for isolated testing
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_lancedb(monkeypatch):
    """Patch LanceDB client to use an in-memory temporary directory."""
    import tempfile
    tmpdir = tempfile.mkdtemp()

    # Patch get_lancedb to return a fresh temp DB per test
    mock_dbs = {}

    async def mock_get_lancedb():
        import lancedb
        if tmpdir not in mock_dbs:
            mock_dbs[tmpdir] = lancedb.connect(tmpdir)
        return mock_dbs[tmpdir]

    monkeypatch.setattr("app.db.lancedb_client.get_lancedb", mock_get_lancedb)
    return tmpdir
