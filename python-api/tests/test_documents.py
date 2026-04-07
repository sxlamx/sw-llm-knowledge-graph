"""Tests for the documents router — list documents, document detail.

Regression tests ensure:
- GET /documents does NOT use the stub pattern range(offset, min(offset+limit, 0))
  which always returns [] (documented in LESSONS.md 2026-03-20)
- Document listing aggregates chunks by doc_id to return unique documents
- Ownership is verified: user can only list docs in their own collections
"""

import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.routers.documents import router, _chunks_to_documents, _get_chunks_for_collection
from app.auth.middleware import get_current_user


FAKE_USER = {"id": "test-user-id", "email": "test@example.com", "name": "Test User"}


def make_fake_app():
    app = FastAPI()
    app.include_router(router, prefix="/documents")
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    return app


# ---------------------------------------------------------------------------
# _chunks_to_documents helper
# ---------------------------------------------------------------------------

class TestChunksToDocuments:
    """_chunks_to_documents aggregates chunk rows by doc_id."""

    def test_single_chunk_becomes_single_document(self):
        chunks = [
            {
                "doc_id": "doc-1",
                "path": "/data/papers/intro.pdf",
                "created_at": 1700000000000000,
                "doc_summary": "An intro paper",
            }
        ]
        docs = _chunks_to_documents(chunks)
        assert len(docs) == 1
        assert docs[0].id == "doc-1"

    def test_multiple_chunks_same_doc_id_returns_one_document(self):
        chunks = [
            {"doc_id": "doc-1", "path": "/a.pdf", "created_at": 1},
            {"doc_id": "doc-1", "path": "/a.pdf", "created_at": 1},
            {"doc_id": "doc-1", "path": "/a.pdf", "created_at": 1},
        ]
        docs = _chunks_to_documents(chunks)
        assert len(docs) == 1, "Multiple chunks must collapse to one document"

    def test_different_doc_ids_return_different_documents(self):
        chunks = [
            {"doc_id": "doc-1", "path": "/a.pdf", "created_at": 1},
            {"doc_id": "doc-2", "path": "/b.pdf", "created_at": 1},
        ]
        docs = _chunks_to_documents(chunks)
        assert len(docs) == 2

    def test_empty_doc_id_skipped(self):
        chunks = [
            {"doc_id": "", "path": "/a.pdf", "created_at": 1},
            {"doc_id": "doc-1", "path": "/b.pdf", "created_at": 1},
        ]
        docs = _chunks_to_documents(chunks)
        assert len(docs) == 1
        assert docs[0].id == "doc-1"

    def test_uses_path_for_title(self):
        chunks = [{"doc_id": "doc-1", "path": "/papers/attention.pdf", "created_at": 1}]
        docs = _chunks_to_documents(chunks)
        assert docs[0].title == "attention.pdf"

    def test_no_path_uses_doc_id_prefix(self):
        chunks = [{"doc_id": "abc-123-def", "created_at": 1}]
        docs = _chunks_to_documents(chunks)
        assert "abc-123" in docs[0].title

    def test_file_type_from_extension(self):
        chunks = [{"doc_id": "doc-1", "path": "/paper.docx", "created_at": 1}]
        docs = _chunks_to_documents(chunks)
        assert docs[0].file_type == "docx"

    def test_unknown_extension_for_no_ext(self):
        chunks = [{"doc_id": "doc-1", "path": "/README", "created_at": 1}]
        docs = _chunks_to_documents(chunks)
        assert docs[0].file_type == "unknown"


# ---------------------------------------------------------------------------
# GET /documents
# ---------------------------------------------------------------------------

class TestListDocuments:
    @pytest.fixture
    def app(self):
        return make_fake_app()

    async def test_returns_200_with_valid_collection(self, app):
        with patch(
            "app.routers.documents.get_collection",
            new_callable=AsyncMock,
            return_value={"id": "col-1", "user_id": FAKE_USER["id"]},
        ), patch(
            "app.routers.documents._get_chunks_for_collection",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get("/documents?collection_id=col-1")
            assert response.status_code == 200

    async def test_collection_not_found_returns_404(self, app):
        with patch(
            "app.routers.documents.get_collection",
            new_callable=AsyncMock,
            return_value=None,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get("/documents?collection_id=nonexistent")
            assert response.status_code == 404

    async def test_other_user_collection_returns_403(self, app):
        with patch(
            "app.routers.documents.get_collection",
            new_callable=AsyncMock,
            return_value={"id": "col-1", "user_id": "other-user-id"},
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get("/documents?collection_id=col-1")
            assert response.status_code == 403

    async def test_returns_documents_from_lancedb(self, app):
        fake_chunks = [
            {
                "doc_id": "doc-1",
                "path": "/papers/transformer.pdf",
                "created_at": 1700000000000000,
                "doc_summary": "About transformers",
            }
        ]
        with patch(
            "app.routers.documents.get_collection",
            new_callable=AsyncMock,
            return_value={"id": "col-1", "user_id": FAKE_USER["id"]},
        ), patch(
            "app.routers.documents._get_chunks_for_collection",
            new_callable=AsyncMock,
            return_value=fake_chunks,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get("/documents?collection_id=col-1")
            assert response.status_code == 200
            data = response.json()
            assert len(data["documents"]) == 1
            assert data["documents"][0]["title"] == "transformer.pdf"

    async def test_pagination_params_are_respected(self, app):
        """Regression: offset/limit must NOT use range(offset, min(offset+limit, 0))."""
        # Create 5 fake chunks with unique doc_ids
        fake_chunks = [
            {"doc_id": f"doc-{i}", "path": f"/p{i}.pdf", "created_at": 1}
            for i in range(5)
        ]
        with patch(
            "app.routers.documents.get_collection",
            new_callable=AsyncMock,
            return_value={"id": "col-1", "user_id": FAKE_USER["id"]},
        ), patch(
            "app.routers.documents._get_chunks_for_collection",
            new_callable=AsyncMock,
            return_value=fake_chunks,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                # Request offset=2, limit=2 — should return 2 documents
                response = await ac.get(
                    "/documents?collection_id=col-1&offset=2&limit=2"
                )
            data = response.json()
            assert data["total"] == 5, "total must be the full count, not affected by offset"
            assert len(data["documents"]) == 2, (
                "Must return exactly 2 documents (offset=2, limit=2 out of 5)"
            )

    async def test_offset_beyond_total_returns_empty_list(self, app):
        fake_chunks = [
            {"doc_id": f"doc-{i}", "path": f"/p{i}.pdf", "created_at": 1}
            for i in range(3)
        ]
        with patch(
            "app.routers.documents.get_collection",
            new_callable=AsyncMock,
            return_value={"id": "col-1", "user_id": FAKE_USER["id"]},
        ), patch(
            "app.routers.documents._get_chunks_for_collection",
            new_callable=AsyncMock,
            return_value=fake_chunks,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get(
                    "/documents?collection_id=col-1&offset=100&limit=10"
                )
            data = response.json()
            assert data["documents"] == []

    async def test_documents_response_schema(self, app):
        fake_chunks = [
            {
                "doc_id": "doc-1",
                "path": "/papers/random.pdf",
                "created_at": 1700000000000000,
                "doc_summary": "A summary",
            }
        ]
        with patch(
            "app.routers.documents.get_collection",
            new_callable=AsyncMock,
            return_value={"id": "col-1", "user_id": FAKE_USER["id"]},
        ), patch(
            "app.routers.documents._get_chunks_for_collection",
            new_callable=AsyncMock,
            return_value=fake_chunks,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get("/documents?collection_id=col-1")
            data = response.json()
            assert "documents" in data
            assert "total" in data
            assert isinstance(data["documents"], list)

    async def test_no_documents_returns_empty_list(self, app):
        with patch(
            "app.routers.documents.get_collection",
            new_callable=AsyncMock,
            return_value={"id": "col-1", "user_id": FAKE_USER["id"]},
        ), patch(
            "app.routers.documents._get_chunks_for_collection",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get("/documents?collection_id=col-1")
            assert response.status_code == 200
            assert response.json()["documents"] == []
            assert response.json()["total"] == 0


# ---------------------------------------------------------------------------
# GET /documents/{doc_id}?collection_id=xxx
# ---------------------------------------------------------------------------

class TestGetDocumentDetail:
    @pytest.fixture
    def app(self):
        return make_fake_app()

    async def test_returns_document_and_chunks(self, app):
        doc_chunks = [
            {
                "id": "chunk-1",
                "doc_id": "doc-1",
                "path": "/papers/test.pdf",
                "text": "Page one text.",
                "position": 0,
                "page": 1,
                "created_at": 1700000000000000,
            },
            {
                "id": "chunk-2",
                "doc_id": "doc-1",
                "path": "/papers/test.pdf",
                "text": "Page two text.",
                "position": 1,
                "page": 2,
                "created_at": 1700000000000000,
            },
        ]
        with patch(
            "app.routers.documents.get_collection",
            new_callable=AsyncMock,
            return_value={"id": "col-1", "user_id": FAKE_USER["id"]},
        ), patch(
            "app.routers.documents._get_chunks_for_doc",
            new_callable=AsyncMock,
            return_value=doc_chunks,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get(
                    "/documents/doc-1?collection_id=col-1"
                )
            assert response.status_code == 200
            data = response.json()
            assert data["document"]["id"] == "doc-1"
            assert data["chunk_count"] == 2
            assert len(data["chunks"]) == 2

    async def test_not_found_returns_404(self, app):
        with patch(
            "app.routers.documents.get_collection",
            new_callable=AsyncMock,
            return_value={"id": "col-1", "user_id": FAKE_USER["id"]},
        ), patch(
            "app.routers.documents._get_chunks_for_doc",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get("/documents/nonexistent?collection_id=col-1")
            assert response.status_code == 404

    async def test_other_user_collection_returns_403(self, app):
        with patch(
            "app.routers.documents.get_collection",
            new_callable=AsyncMock,
            return_value={"id": "col-1", "user_id": "other-user"},
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get("/documents/doc-1?collection_id=col-1")
            assert response.status_code == 403


# ---------------------------------------------------------------------------
# Regression: no stub bug in document listing
# ---------------------------------------------------------------------------

class TestNoStubBug:
    """
    Regression test for the bug documented in LESSONS.md 2026-03-20.

    The documents router previously used:
        page = documents[offset:min(offset + limit, 0)]

    which always returns [] because min(x, 0) == 0 for any x >= 0.
    This test verifies the correct implementation using slicing on the
    already-fetched document list.
    """

    async def test_documents_pagination_is_not_empty_due_to_stub(self):
        """Verify documents list is NOT always empty due to range(min(offset+limit, 0))."""
        app = make_fake_app()
        fake_chunks = [
            {"doc_id": f"doc-{i}", "path": f"/p{i}.pdf", "created_at": 1}
            for i in range(20)
        ]
        with patch(
            "app.routers.documents.get_collection",
            new_callable=AsyncMock,
            return_value={"id": "col-1", "user_id": FAKE_USER["id"]},
        ), patch(
            "app.routers.documents._get_chunks_for_collection",
            new_callable=AsyncMock,
            return_value=fake_chunks,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get(
                    "/documents?collection_id=col-1&limit=10&offset=0"
                )
        data = response.json()
        # If stub bug existed: len(documents) == 0
        # Correct implementation: len(documents) == 10
        assert len(data["documents"]) == 10, (
            f"Expected 10 documents (limit=10), got {len(data['documents'])}. "
            "This suggests the range(offset, min(offset+limit, 0)) stub bug is present."
        )
        assert data["total"] == 20
