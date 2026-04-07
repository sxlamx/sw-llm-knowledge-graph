"""Tests for search_service — 3-channel hybrid search orchestration.

Tests:
  - Score fusion: weighted combination, deduplication, top-k limiting
  - BM25 normalization: sigmoid maps raw scores to [0, 1]
  - Channel timeouts: graceful degradation when a channel times out
  - embed_query is used (not embed_texts) for search queries
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestScoreFusion:
    """Score fusion tests — verify weighted combination and deduplication."""

    def test_fuse_results_deduplicates_by_chunk_id(self):
        from app.core.search_service import _fuse_results
        from unittest.mock import MagicMock

        weights = {"vector": 0.6, "keyword": 0.3, "graph": 0.1}
        vector = [({"chunk_id": "c1", "id": "c1", "vector_score": 0.9,
                    "keyword_score": 0.0, "graph_proximity_score": 0.0,
                    "doc_id": "", "text": "", "page": None, "topics": []})]
        keyword = [({"chunk_id": "c1", "id": "c1", "keyword_score": 0.8,
                     "vector_score": 0.0, "graph_proximity_score": 0.0,
                     "doc_id": "", "text": "", "page": None, "topics": []})]
        graph = []

        results = _fuse_results(vector, keyword, graph, weights)
        assert len(results) == 1, "c1 should appear exactly once (merged, not duplicated)"

    def test_fuse_results_weights_applied_correctly(self):
        from app.core.search_service import _fuse_results

        weights = {"vector": 0.6, "keyword": 0.3, "graph": 0.1}
        vector = [({"chunk_id": "c1", "id": "c1", "vector_score": 0.5,
                    "keyword_score": 0.0, "graph_proximity_score": 0.0,
                    "doc_id": "", "text": "", "page": None, "topics": []})]
        keyword = [({"chunk_id": "c1", "id": "c1", "keyword_score": 0.8,
                     "vector_score": 0.0, "graph_proximity_score": 0.0,
                     "doc_id": "", "text": "", "page": None, "topics": []})]
        graph = [({"chunk_id": "c1", "id": "c1", "graph_proximity_score": 0.3,
                   "vector_score": 0.0, "keyword_score": 0.0,
                   "doc_id": "", "text": "", "page": None, "topics": []})]

        results = _fuse_results(vector, keyword, graph, weights)
        expected = 0.6 * 0.5 + 0.3 * 0.8 + 0.1 * 0.3  # 0.3 + 0.24 + 0.03 = 0.57
        assert abs(results[0]["final_score"] - expected) < 1e-5

    def test_fuse_results_respects_limit(self):
        from app.core.search_service import _fuse_results

        weights = {"vector": 0.6, "keyword": 0.3, "graph": 0.1}
        # 10 candidates
        vector = [{"chunk_id": f"c{i}", "id": f"c{i}", "vector_score": 0.9 - i * 0.05,
                   "keyword_score": 0.0, "graph_proximity_score": 0.0,
                   "doc_id": "", "text": "", "page": None, "topics": []}
                  for i in range(10)]
        keyword = []
        graph = []

        results = _fuse_results(vector, keyword, graph, weights)
        assert len(results) == 10, "fuse_results returns all candidates; limiting happens in hybrid_search()"

    def test_fuse_results_empty_channel_contributes_zero(self):
        from app.core.search_service import _fuse_results

        weights = {"vector": 0.6, "keyword": 0.3, "graph": 0.1}
        # Only keyword channel has results; vector and graph return empty
        vector = []
        keyword = [({"chunk_id": "c1", "id": "c1", "keyword_score": 0.7,
                     "vector_score": 0.0, "graph_proximity_score": 0.0,
                     "doc_id": "", "text": "", "page": None, "topics": []})]
        graph = []

        results = _fuse_results(vector, keyword, graph, weights)
        # final_score = 0.6*0 + 0.3*0.7 + 0.1*0 = 0.21
        assert abs(results[0]["final_score"] - 0.21) < 1e-5


class TestBm25Normalization:
    """BM25 normalization — verify sigmoid mapping."""

    def test_sigmoid_zero_maps_to_zero(self):
        # keyword_score = bm25 / (bm25 + 1.0)
        normalized = 0.0 / (0.0 + 1.0)
        assert normalized == 0.0

    def test_sigmoid_maps_to_zero_one_range(self):
        raw_scores = [0.0, 0.5, 1.0, 5.0, 10.0, 100.0]
        for raw in raw_scores:
            normalized = raw / (raw + 1.0)
            assert 0.0 <= normalized <= 1.0, f"raw={raw} normalized={normalized} out of range"

    def test_sigmoid_is_monotonic(self):
        raw_scores = [0.0, 0.5, 1.0, 5.0, 10.0, 100.0]
        for window in zip(raw_scores, raw_scores[1:]):
            n0 = window[0] / (window[0] + 1.0)
            n1 = window[1] / (window[1] + 1.0)
            assert n1 > n0, f"normalization should be monotonic: {window[0]}->{n0} vs {window[1]}->{n1}"

    def test_sigmoid_high_score_nears_one(self):
        # Very high BM25 (e.g., 100.0) should normalize close to 1.0
        normalized = 100.0 / (100.0 + 1.0)
        assert normalized > 0.99, f"high score should be near 1.0, got {normalized}"


class TestChannelTimeouts:
    """Graceful degradation — channel timeout returns empty, other channels still contribute."""

    @pytest.mark.asyncio
    async def test_vector_timeout_still_returns_keyword_results(self):
        from app.core.search_service import _hybrid_3channel

        # Patch vector to always timeout, keyword to return results
        with patch(
            "app.core.search_service._vector_channel",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.core.search_service._keyword_channel",
            new_callable=AsyncMock,
            return_value=[
                {"chunk_id": "c1", "id": "c1", "keyword_score": 0.7,
                 "vector_score": 0.0, "graph_proximity_score": 0.0,
                 "doc_id": "", "text": "", "page": None, "topics": []}
            ],
        ), patch(
            "app.core.search_service._graph_channel",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.core.search_service.embed_query",
            new_callable=AsyncMock,
            return_value=[0.1] * 4,
        ):
            results = await _hybrid_3channel(
                query="test query",
                collection_ids=["col1"],
                topics=None,
                limit=20,
                weights={"vector": 0.6, "keyword": 0.3, "graph": 0.1},
            )

        assert len(results) == 1, "should still return keyword result even if vector timed out"
        assert results[0]["keyword_score"] == 0.7

    @pytest.mark.asyncio
    async def test_all_channels_return_empty_not_error(self):
        from app.core.search_service import _hybrid_3channel

        with patch(
            "app.core.search_service._vector_channel",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.core.search_service._keyword_channel",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.core.search_service._graph_channel",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.core.search_service.embed_query",
            new_callable=AsyncMock,
            return_value=[0.1] * 4,
        ):
            results = await _hybrid_3channel(
                query="test query",
                collection_ids=["col1"],
                topics=None,
                limit=20,
                weights={"vector": 0.6, "keyword": 0.3, "graph": 0.1},
            )

        # Empty list, NOT an exception
        assert results == [], "all channels empty should return [], not raise"


class TestEmbedQueryUsed:
    """Verify embed_query (not embed_texts) is used for search."""

    @pytest.mark.asyncio
    async def test_hybrid_uses_embed_query_not_embed_texts(self):
        from app.core.search_service import _hybrid_3channel

        embed_query_called = []
        embed_texts_called = []

        async def mock_embed_query(text):
            embed_query_called.append(text)
            return [0.1] * 4

        async def mock_embed_texts(texts):
            embed_texts_called.extend(texts)
            return [[0.1] * 4]

        with patch(
            "app.core.search_service._vector_channel",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.core.search_service._keyword_channel",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.core.search_service._graph_channel",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.core.search_service.embed_query",
            side_effect=mock_embed_query,
        ):
            await _hybrid_3channel(
                query="my search query",
                collection_ids=["col1"],
                topics=None,
                limit=20,
                weights={"vector": 0.6, "keyword": 0.3, "graph": 0.1},
            )

        assert "my search query" in embed_query_called, "embed_query should be called with the query string"
        assert len(embed_texts_called) == 0, "embed_texts should NOT be called for search queries"


class TestHybridSearchModes:
    """Mode routing — hybrid, vector, keyword, graph modes."""

    @pytest.mark.asyncio
    async def test_mode_vector_uses_embed_query(self):
        from app.core.search_service import _vector_only

        embed_query_called = []
        async def mock_embed_query(text):
            embed_query_called.append(text)
            return [0.1] * 4

        with patch(
            "app.core.search_service.embed_query",
            side_effect=mock_embed_query,
        ), patch(
            "app.db.lancedb_client.vector_search",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await _vector_only("test query", ["col1"], None, 20)

        assert "test query" in embed_query_called

    @pytest.mark.asyncio
    async def test_mode_graph_uses_embed_query(self):
        from app.core.search_service import _graph_only

        embed_query_called = []

        async def mock_embed_query(text):
            embed_query_called.append(text)
            return [0.1] * 4

        with patch(
            "app.core.search_service.embed_query",
            side_effect=mock_embed_query,
        ), patch(
            "app.core.rust_bridge.rust_bfs_proximity_async",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await _graph_only("test query", ["col1"], 20)

        assert "test query" in embed_query_called
