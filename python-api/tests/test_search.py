"""Tests for the search router and hybrid_search service."""

import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.routers.search import router
from app.auth.middleware import get_current_user
from app.db.lancedb_client import get_collection

FAKE_USER = {"id": "test-user-id", "email": "test@example.com", "name": "Test User"}

FAKE_COLLECTION = {
    "id": "col-1",
    "user_id": "test-user-id",
    "name": "Test Collection",
    "created_at": "2024-01-01T00:00:00Z",
}

FAKE_RESULTS = [
    {
        "chunk_id": str(uuid.uuid4()),
        "doc_id": str(uuid.uuid4()),
        "doc_title": "Test Document",
        "text": "Machine learning is a subset of artificial intelligence.",
        "page": 1,
        "vector_score": 0.92,
        "keyword_score": 0.78,
        "graph_proximity_score": 0.10,
        "final_score": 0.85,
        "topics": ["AI", "Machine Learning"],
        "highlights": ["machine learning", "artificial intelligence"],
    }
]

# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    _app = FastAPI()
    _app.include_router(router, prefix="/search")
    _app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    return _app


# ---------------------------------------------------------------------------
# POST /search
# ---------------------------------------------------------------------------

class TestSearch:
    async def test_basic_search_returns_results(self, app):
        with patch(
            "app.routers.search.get_collection_by_id",
            return_value=FAKE_COLLECTION,
        ), patch(
            "app.routers.search.hybrid_search",
            new_callable=AsyncMock,
            return_value=FAKE_RESULTS,
        ), patch(
            "app.routers.search.get_collection_by_id",
            return_value=FAKE_COLLECTION,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    "/search",
                    json={
                        "query": "machine learning",
                        "collection_ids": ["col-1"],
                    },
                )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["results"]) == 1
        r = data["results"][0]
        assert r["text"] == "Machine learning is a subset of artificial intelligence."
        assert r["final_score"] == pytest.approx(0.85)
        assert "AI" in r["topics"]

    async def test_empty_results_returns_zero_total(self, app):
        with patch(
            "app.routers.search.get_collection_by_id",
            return_value=FAKE_COLLECTION,
        ), patch(
            "app.routers.search.hybrid_search",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.routers.search.get_collection_by_id",
            return_value=FAKE_COLLECTION,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    "/search",
                    json={"query": "nothing here", "collection_ids": ["col-1"]},
                )

        assert response.status_code == 200
        assert response.json()["total"] == 0
        assert response.json()["results"] == []

    async def test_search_mode_propagated(self, app):
        captured_kwargs = {}

        async def mock_search(**kwargs):
            captured_kwargs.update(kwargs)
            return []

        with patch(
            "app.routers.search.get_collection_by_id",
            return_value=FAKE_COLLECTION,
        ), patch("app.routers.search.hybrid_search", side_effect=mock_search):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    "/search",
                    json={
                        "query": "test",
                        "collection_ids": ["col-1"],
                        "mode": "keyword",
                    },
                )

        assert response.status_code == 200
        assert captured_kwargs.get("mode") == "keyword"

    async def test_search_error_returns_empty_results(self, app):
        """On backend error, search should return an empty list, not 500."""
        with patch(
            "app.routers.search.get_collection_by_id",
            return_value=FAKE_COLLECTION,
        ), patch(
            "app.routers.search.hybrid_search",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Rust core unavailable"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    "/search",
                    json={"query": "test", "collection_ids": ["col-1"]},
                )

        assert response.status_code == 200
        assert response.json()["total"] == 0

    async def test_search_response_includes_latency_ms(self, app):
        with patch(
            "app.routers.search.get_collection_by_id",
            return_value=FAKE_COLLECTION,
        ), patch(
            "app.routers.search.hybrid_search",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    "/search",
                    json={"query": "test", "collection_ids": ["col-1"]},
                )

        data = response.json()
        assert "latency_ms" in data
        assert isinstance(data["latency_ms"], int)
        assert data["latency_ms"] >= 0

    async def test_pagination_params_propagated(self, app):
        captured = {}

        async def mock_search(**kwargs):
            captured.update(kwargs)
            return []

        with patch(
            "app.routers.search.get_collection_by_id",
            return_value=FAKE_COLLECTION,
        ), patch("app.routers.search.hybrid_search", side_effect=mock_search):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    "/search",
                    json={
                        "query": "test",
                        "collection_ids": ["col-1"],
                        "limit": 5,
                        "offset": 10,
                    },
                )

        assert response.status_code == 200
        assert captured.get("limit") == 5
        assert captured.get("offset") == 10

    async def test_topics_filter_propagated(self, app):
        captured = {}

        async def mock_search(**kwargs):
            captured.update(kwargs)
            return []

        with patch(
            "app.routers.search.get_collection_by_id",
            return_value=FAKE_COLLECTION,
        ), patch("app.routers.search.hybrid_search", side_effect=mock_search):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    "/search",
                    json={
                        "query": "AI",
                        "collection_ids": ["col-1"],
                        "topics": ["machine_learning", "nlp"],
                    },
                )

        assert response.status_code == 200
        assert captured.get("topics") == ["machine_learning", "nlp"]

    async def test_multiple_results_sorted_by_final_score(self, app):
        results = [
            {**FAKE_RESULTS[0], "final_score": 0.5, "chunk_id": "a"},
            {**FAKE_RESULTS[0], "final_score": 0.9, "chunk_id": "b"},
            {**FAKE_RESULTS[0], "final_score": 0.7, "chunk_id": "c"},
        ]
        with patch(
            "app.routers.search.get_collection_by_id",
            return_value=FAKE_COLLECTION,
        ), patch(
            "app.routers.search.hybrid_search",
            new_callable=AsyncMock,
            return_value=results,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    "/search",
                    json={"query": "test", "collection_ids": ["col-1"]},
                )

        data = response.json()
        assert data["total"] == 3

    # ---------------------------------------------------------------------------
    # Phase 4 — Topic filter propagation and post-filter
    # ---------------------------------------------------------------------------

    async def test_topic_filter_on_hybrid_search(self, app):
        """Verify topics are passed through to hybrid_search for post-filtering."""
        captured = {}

        async def mock_search(**kwargs):
            captured.update(kwargs)
            return []

        with patch(
            "app.routers.search.get_collection_by_id",
            return_value=FAKE_COLLECTION,
        ), patch("app.routers.search.hybrid_search", side_effect=mock_search):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    "/search",
                    json={
                        "query": "contract law",
                        "collection_ids": ["col-1"],
                        "mode": "hybrid",
                        "topics": ["contracts", "legal"],
                    },
                )

        assert response.status_code == 200
        assert captured.get("topics") == ["contracts", "legal"]

    async def test_search_returns_highlights_field(self, app):
        """Verify highlights from BM25 are propagated through fusion to the API response."""
        results_with_highlights = [
            {
                **FAKE_RESULTS[0],
                "highlights": ["machine learning", "artificial intelligence"],
            }
        ]
        with patch(
            "app.routers.search.get_collection_by_id",
            return_value=FAKE_COLLECTION,
        ), patch(
            "app.routers.search.hybrid_search",
            new_callable=AsyncMock,
            return_value=results_with_highlights,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    "/search",
                    json={"query": "machine learning", "collection_ids": ["col-1"]},
                )

        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) >= 1
        r = data["results"][0]
        assert "highlights" in r
        assert len(r["highlights"]) >= 1

    async def test_search_graph_proximity_score_field(self, app):
        """Verify graph_proximity_score is returned in API response."""
        graph_result = {
            **FAKE_RESULTS[0],
            "graph_proximity_score": 0.45,
            "final_score": 0.85,
        }
        with patch(
            "app.routers.search.get_collection_by_id",
            return_value=FAKE_COLLECTION,
        ), patch(
            "app.routers.search.hybrid_search",
            new_callable=AsyncMock,
            return_value=[graph_result],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    "/search",
                    json={"query": "entity graph", "collection_ids": ["col-1"]},
                )

        assert response.status_code == 200
        r = response.json()["results"][0]
        assert "graph_proximity_score" in r
        assert r["graph_proximity_score"] == pytest.approx(0.45)
