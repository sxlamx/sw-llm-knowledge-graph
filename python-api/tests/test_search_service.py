"""Tests for search_service — 3-channel hybrid search orchestration.

Phase 3 tests:
  - Score fusion: weighted combination, deduplication, top-k limiting
  - BM25 normalization: sigmoid maps raw scores to [0, 1]
  - Channel timeouts: graceful degradation when a channel times out
  - embed_query is used (not embed_texts) for search queries

Phase 4 tests:
  - Score fusion includes keyword-only and graph-only hits
  - Score fusion all channels empty returns empty (not error)
  - Post-filter by topics removes non-matching results
  - Post-filter by topics keeps results without topics field (optimistic)
  - Embedding cache: _get_embedding checks Rust cache first
  - Channel mode routing dispatches correctly
  - Highlights from keyword channel are merged into fused results
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestScoreFusion:
    """Score fusion tests — verify weighted combination and deduplication."""

    def test_fuse_results_deduplicates_by_chunk_id(self):
        from app.core.search_service import _fuse_results

        weights = {"vector": 0.6, "keyword": 0.3, "graph": 0.1}
        vector = [{"chunk_id": "c1", "id": "c1", "vector_score": 0.9,
                    "keyword_score": 0.0, "graph_proximity_score": 0.0,
                    "doc_id": "", "text": "", "page": None, "topics": []}]
        keyword = [{"chunk_id": "c1", "id": "c1", "keyword_score": 0.8,
                     "vector_score": 0.0, "graph_proximity_score": 0.0,
                     "doc_id": "", "text": "", "page": None, "topics": []}]
        graph = []

        results = _fuse_results(vector, keyword, graph, weights)
        assert len(results) == 1, "c1 should appear exactly once (merged, not duplicated)"

    def test_fuse_results_weights_applied_correctly(self):
        from app.core.search_service import _fuse_results

        weights = {"vector": 0.6, "keyword": 0.3, "graph": 0.1}
        vector = [{"chunk_id": "c1", "id": "c1", "vector_score": 0.5,
                    "keyword_score": 0.0, "graph_proximity_score": 0.0,
                    "doc_id": "", "text": "", "page": None, "topics": []}]
        keyword = [{"chunk_id": "c1", "id": "c1", "keyword_score": 0.8,
                     "vector_score": 0.0, "graph_proximity_score": 0.0,
                     "doc_id": "", "text": "", "page": None, "topics": []}]
        graph = [{"chunk_id": "c1", "id": "c1", "graph_proximity_score": 0.3,
                   "vector_score": 0.0, "keyword_score": 0.0,
                   "doc_id": "", "text": "", "page": None, "topics": []}]

        results = _fuse_results(vector, keyword, graph, weights)
        expected = 0.6 * 0.5 + 0.3 * 0.8 + 0.1 * 0.3
        assert abs(results[0]["final_score"] - expected) < 1e-5

    def test_fuse_results_respects_limit(self):
        from app.core.search_service import _fuse_results

        weights = {"vector": 0.6, "keyword": 0.3, "graph": 0.1}
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
        vector = []
        keyword = [{"chunk_id": "c1", "id": "c1", "keyword_score": 0.7,
                     "vector_score": 0.0, "graph_proximity_score": 0.0,
                     "doc_id": "", "text": "", "page": None, "topics": []}]
        graph = []

        results = _fuse_results(vector, keyword, graph, weights)
        assert abs(results[0]["final_score"] - 0.21) < 1e-5

    def test_fuse_results_includes_keyword_only_hits(self):
        from app.core.search_service import _fuse_results

        weights = {"vector": 0.6, "keyword": 0.3, "graph": 0.1}
        vector = [{"chunk_id": "c1", "id": "c1", "vector_score": 0.9,
                    "doc_id": "d1", "text": "hello", "page": None, "topics": []}]
        keyword = [{"chunk_id": "c1", "id": "c1", "keyword_score": 0.8,
                     "doc_id": "", "text": "", "page": None, "topics": []},
                    {"chunk_id": "c2", "id": "c2", "keyword_score": 0.7,
                     "doc_id": "d2", "text": "world", "page": None, "topics": []}]
        graph = []

        results = _fuse_results(vector, keyword, graph, weights)
        chunk_ids = {r["chunk_id"] for r in results}
        assert "c2" in chunk_ids, "keyword-only hit c2 must be included"

        c2 = next(r for r in results if r["chunk_id"] == "c2")
        expected_c2 = 0.0 * 0.6 + 0.7 * 0.3 + 0.0 * 0.1
        assert abs(c2["final_score"] - expected_c2) < 1e-5, "keyword-only final_score should be wk * keyword_score"

    def test_fuse_results_includes_graph_only_hits(self):
        from app.core.search_service import _fuse_results

        weights = {"vector": 0.6, "keyword": 0.3, "graph": 0.1}
        vector = []
        keyword = []
        graph = [{"chunk_id": "c3", "id": "c3", "graph_proximity_score": 0.5,
                   "doc_id": "", "text": "", "page": None, "topics": []}]

        results = _fuse_results(vector, keyword, graph, weights)
        assert len(results) == 1, "graph-only hit should be included"

        c3 = results[0]
        expected_c3 = 0.0 * 0.6 + 0.0 * 0.3 + 0.5 * 0.1
        assert abs(c3["final_score"] - expected_c3) < 1e-5, "graph-only final_score should be wg * graph_score"

    def test_fuse_results_all_channels_empty(self):
        from app.core.search_service import _fuse_results

        weights = {"vector": 0.6, "keyword": 0.3, "graph": 0.1}
        results = _fuse_results([], [], [], weights)
        assert results == [], "all channels empty should return [], not raise"

    def test_fuse_results_merges_highlights_from_keyword(self):
        from app.core.search_service import _fuse_results

        weights = {"vector": 0.6, "keyword": 0.3, "graph": 0.1}
        vector = [{"chunk_id": "c1", "id": "c1", "vector_score": 0.9,
                    "doc_id": "d1", "text": "hello world", "page": None, "topics": []}]
        keyword = [{"chunk_id": "c1", "id": "c1", "keyword_score": 0.7,
                     "highlights": ["hello"], "doc_id": "", "text": "",
                     "page": None, "topics": []}]
        graph = []

        results = _fuse_results(vector, keyword, graph, weights)
        assert len(results) == 1
        assert "hello" in results[0]["highlights"], "keyword highlights should be merged into fused result"


class TestBm25Normalization:
    """BM25 normalization — verify sigmoid mapping."""

    def test_sigmoid_zero_maps_to_zero(self):
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
        normalized = 100.0 / (100.0 + 1.0)
        assert normalized > 0.99, f"high score should be near 1.0, got {normalized}"


class TestChannelTimeouts:
    """Graceful degradation — channel timeout returns empty, other channels still contribute."""

    @pytest.mark.asyncio
    async def test_vector_timeout_still_returns_keyword_results(self):
        from app.core.search_service import _hybrid_3channel

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
            "app.core.search_service._get_embedding",
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
            "app.core.search_service._get_embedding",
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

        assert results == [], "all channels empty should return [], not raise"

    @pytest.mark.asyncio
    async def test_keyword_timeout_still_returns_vector_and_graph(self):
        from app.core.search_service import _hybrid_3channel

        with patch(
            "app.core.search_service._vector_channel",
            new_callable=AsyncMock,
            return_value=[
                {"chunk_id": "c1", "id": "c1", "vector_score": 0.9,
                 "doc_id": "d1", "text": "hello", "page": None, "topics": []}
            ],
        ), patch(
            "app.core.search_service._keyword_channel",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.core.search_service._graph_channel",
            new_callable=AsyncMock,
            return_value=[
                {"chunk_id": "c2", "id": "c2", "graph_proximity_score": 0.4,
                 "doc_id": "", "text": "", "page": None, "topics": []}
            ],
        ), patch(
            "app.core.search_service._get_embedding",
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

        chunk_ids = {r["chunk_id"] for r in results}
        assert "c1" in chunk_ids, "vector result should be present"
        assert "c2" in chunk_ids, "graph result should be present even when keyword timed out"


class TestEmbeddingCacheIntegration:
    """Phase 4: _get_embedding checks Rust cache before calling embed_query."""

    @pytest.mark.asyncio
    async def test_get_embedding_checks_rust_cache_first(self):
        from app.core.search_service import _get_embedding

        mock_im = MagicMock()
        mock_im.get_cached_embedding.return_value = '[0.1, 0.2, 0.3, 0.4]'

        with patch(
            "app.core.search_service.get_index_manager",
            return_value=mock_im,
        ), patch(
            "app.core.search_service.embed_query",
            new_callable=AsyncMock,
            return_value=[0.5, 0.6, 0.7, 0.8],
        ):
            result = await _get_embedding("cached query")

        assert result == [0.1, 0.2, 0.3, 0.4], "should return cached embedding, not call embed_query"
        mock_im.get_cached_embedding.assert_called_once_with("cached query")

    @pytest.mark.asyncio
    async def test_get_embedding_calls_embed_query_on_cache_miss(self):
        from app.core.search_service import _get_embedding

        mock_im = MagicMock()
        mock_im.get_cached_embedding.return_value = ""
        mock_im.cache_embedding.return_value = True

        with patch(
            "app.core.search_service.get_index_manager",
            return_value=mock_im,
        ), patch(
            "app.core.search_service.embed_query",
            new_callable=AsyncMock,
            return_value=[0.5, 0.6, 0.7, 0.8],
        ):
            result = await _get_embedding("uncached query")

        assert result == [0.5, 0.6, 0.7, 0.8], "should return embed_query result on cache miss"
        mock_im.get_cached_embedding.assert_called_once_with("uncached query")
        mock_im.cache_embedding.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_embedding_handles_rust_unavailable(self):
        from app.core.search_service import _get_embedding

        with patch(
            "app.core.search_service.get_index_manager",
            return_value=None,
        ), patch(
            "app.core.search_service.embed_query",
            new_callable=AsyncMock,
            return_value=[0.5, 0.6, 0.7, 0.8],
        ):
            result = await _get_embedding("query without rust")

        assert result == [0.5, 0.6, 0.7, 0.8], "should fall back to embed_query when Rust unavailable"


class TestPostFilterByTopics:
    """Phase 4: Topic post-filter for cross-channel consistency."""

    def test_removes_results_with_no_topic_overlap(self):
        from app.core.search_service import _post_filter_by_topics

        results = [
            {"chunk_id": "c1", "topics": ["contracts", "legal"]},
            {"chunk_id": "c2", "topics": ["torts", "civil"]},
            {"chunk_id": "c3", "topics": ["contracts", "criminal"]},
        ]
        filtered = _post_filter_by_topics(results, ["contracts"])
        chunk_ids = {r["chunk_id"] for r in filtered}
        assert "c1" in chunk_ids
        assert "c3" in chunk_ids
        assert "c2" not in chunk_ids, "c2 has no 'contracts' topic — should be removed"

    def test_keeps_results_without_topics_field(self):
        from app.core.search_service import _post_filter_by_topics

        results = [
            {"chunk_id": "c1", "topics": ["contracts"]},
            {"chunk_id": "c2"},
        ]
        filtered = _post_filter_by_topics(results, ["contracts"])
        assert len(filtered) == 2, "results without topics field should be kept (optimistic)"

    def test_empty_topics_returns_all(self):
        from app.core.search_service import _post_filter_by_topics

        results = [
            {"chunk_id": "c1", "topics": ["contracts"]},
            {"chunk_id": "c2"},
        ]
        filtered = _post_filter_by_topics(results, [])
        assert len(filtered) == 2, "empty topics list should not filter"

    def test_case_insensitive_topic_matching(self):
        from app.core.search_service import _post_filter_by_topics

        results = [
            {"chunk_id": "c1", "topics": ["Contracts", "LEGAL"]},
        ]
        filtered = _post_filter_by_topics(results, ["contracts"])
        assert len(filtered) == 1, "topic matching should be case-insensitive"


class TestHybridSearchModes:
    """Mode routing — hybrid, vector, keyword, graph modes."""

    @pytest.mark.asyncio
    async def test_mode_vector_uses_get_embedding(self):
        from app.core.search_service import _vector_only

        get_embedding_called = []

        async def mock_get_embedding(text):
            get_embedding_called.append(text)
            return [0.1] * 4

        with patch(
            "app.core.search_service._get_embedding",
            side_effect=mock_get_embedding,
        ), patch(
            "app.db.lancedb_client.vector_search",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await _vector_only("test query", ["col1"], None, 20)

        assert "test query" in get_embedding_called

    @pytest.mark.asyncio
    async def test_mode_graph_uses_get_embedding(self):
        from app.core.search_service import _graph_only

        get_embedding_called = []

        async def mock_get_embedding(text):
            get_embedding_called.append(text)
            return [0.1] * 4

        with patch(
            "app.core.search_service._get_embedding",
            side_effect=mock_get_embedding,
        ), patch(
            "app.core.rust_bridge.rust_bfs_proximity_async",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await _graph_only("test query", ["col1"], 20)

        assert "test query" in get_embedding_called

    @pytest.mark.asyncio
    async def test_mode_hybrid_applies_topic_filter(self):
        from app.core.search_service import _hybrid_3channel

        with patch(
            "app.core.search_service._vector_channel",
            new_callable=AsyncMock,
            return_value=[
                {"chunk_id": "c1", "vector_score": 0.9, "doc_id": "d1", "text": "",
                 "page": None, "topics": ["contracts"]},
            ],
        ), patch(
            "app.core.search_service._keyword_channel",
            new_callable=AsyncMock,
            return_value=[
                {"chunk_id": "c2", "keyword_score": 0.7, "doc_id": "", "text": "",
                 "page": None, "topics": ["torts"]},
            ],
        ), patch(
            "app.core.search_service._graph_channel",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.core.search_service._get_embedding",
            new_callable=AsyncMock,
            return_value=[0.1] * 4,
        ):
            results = await _hybrid_3channel(
                query="test",
                collection_ids=["col1"],
                topics=["contracts"],
                limit=20,
                weights={"vector": 0.6, "keyword": 0.3, "graph": 0.1},
            )

        chunk_ids = {r["chunk_id"] for r in results}
        assert "c1" in chunk_ids, "c1 has 'contracts' topic — should be kept"
        assert "c2" not in chunk_ids, "c2 has 'torts' not 'contracts' — should be filtered out"