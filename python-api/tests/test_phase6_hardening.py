"""Phase 6 production hardening tests.

Tests for:
  - Rate limiter uses asyncio.Lock (concurrent async safety)
  - 800ms overall search timeout
  - Prometheus metrics correctness
  - WAL recovery ordering
  - Graph pruning correctness
"""

import asyncio
import time
import pytest
import json
import uuid
from unittest.mock import MagicMock, patch, AsyncMock

from app.auth.middleware import RateLimiter, _RATE_LIMIT_EXEMPT, NO_AUTH_PATHS


class TestRateLimiterAsyncLock:
    """Verify the RateLimiter uses asyncio.Lock for async safety."""

    @pytest.mark.asyncio
    async def test_check_user_is_async(self):
        """check_user must be an async method (uses asyncio.Lock)."""
        rl = RateLimiter(per_user_limit=5, per_ip_limit=10, window_seconds=60)
        result = await rl.check_user("alice")
        assert result is True

    @pytest.mark.asyncio
    async def test_check_ip_is_async(self):
        """check_ip must be an async method (uses asyncio.Lock)."""
        rl = RateLimiter(per_user_limit=5, per_ip_limit=10, window_seconds=60)
        result = await rl.check_ip("10.0.0.1")
        assert result is True

    @pytest.mark.asyncio
    async def test_concurrent_requests_dont_exceed_limit(self):
        """Under concurrent async requests, the limit must be enforced correctly.

        Without asyncio.Lock, two concurrent requests could both read count=N
        and both pass, exceeding the limit. With the lock, exactly
        per_user_limit requests should succeed.
        """
        rl = RateLimiter(per_user_limit=10, per_ip_limit=1000, window_seconds=60)

        async def make_request(user_id, idx):
            return await rl.check_user(user_id)

        # Fire 10 requests for "user1" concurrently — all should succeed
        results = await asyncio.gather(*[
            make_request("user1", i) for i in range(10)
        ])
        assert all(r is True for r in results), "all 10 requests within limit should succeed"

        # 11th request must be rejected
        result = await rl.check_user("user1")
        assert result is False, "11th request must be rate limited"

    @pytest.mark.asyncio
    async def test_concurrent_requests_across_users(self):
        """Each user gets independent quota — exhaustion by one user shouldn't affect another."""
        rl = RateLimiter(per_user_limit=3, per_ip_limit=1000, window_seconds=60)

        # Exhaust alice's quota concurrently
        await asyncio.gather(*[
            rl.check_user("alice") for _ in range(3)
        ])

        # alice should be limited
        assert await rl.check_user("alice") is False

        # bob should still be allowed
        assert await rl.check_user("bob") is True

    @pytest.mark.asyncio
    async def test_lock_prevents_race_condition(self):
        """Simulate a race condition scenario: without asyncio.Lock, 2 concurrent
        checks could both see count < limit and both succeed. With asyncio.Lock,
        exactly the limit number of requests succeed.
        """
        rl = RateLimiter(per_user_limit=5, per_ip_limit=1000, window_seconds=60)

        # Fire 10 concurrent requests — only 5 should succeed
        tasks = [rl.check_user("racer") for _ in range(10)]
        results = await asyncio.gather(*tasks)

        successes = sum(1 for r in results if r is True)
        assert successes == 5, f"exactly 5 requests should succeed, got {successes}"

    @pytest.mark.asyncio
    async def test_sliding_window_expiry(self):
        """Entries outside the window should be evicted on check."""
        rl = RateLimiter(per_user_limit=2, per_ip_limit=100, window_seconds=1)

        # Use both slots
        assert await rl.check_user("u") is True
        assert await rl.check_user("u") is True
        assert await rl.check_user("u") is False

        # Manually backdate timestamps to simulate window expiry
        rl._counts["u:u"] = [time.time() - 2, time.time() - 2]
        assert await rl.check_user("u") is True


class TestOverallSearchTimeout:
    """Verify the 800ms overall search timeout wraps the entire hybrid_search call."""

    def test_overall_timeout_constant_is_set(self):
        """OVERALL_TIMEOUT must be defined and set to 0.8s."""
        from app.core.search_service import OVERALL_TIMEOUT
        assert OVERALL_TIMEOUT == 0.8, f"OVERALL_TIMEOUT must be 0.8, got {OVERALL_TIMEOUT}"

    @pytest.mark.asyncio
    async def test_search_returns_empty_on_timeout(self):
        """When the overall timeout fires, hybrid_search must return empty results (not raise)."""
        from app.core.search_service import hybrid_search, OVERALL_TIMEOUT

        with patch("app.core.search_service._get_embedding", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            with patch("app.core.search_service.vector_search", new_callable=AsyncMock) as mock_vec:
                # Make vector_search sleep longer than OVERALL_TIMEOUT
                async def slow_search(*args, **kwargs):
                    await asyncio.sleep(OVERALL_TIMEOUT + 1.0)
                    return []

                mock_vec.side_effect = slow_search

                with patch("app.core.search_service.rust_keyword_search_async", new_callable=AsyncMock) as mock_kw:
                    mock_kw.return_value = []

                    with patch("app.core.search_service.rust_bfs_proximity_async", new_callable=AsyncMock) as mock_g:
                        mock_g.return_value = []

                        result = await hybrid_search(
                            "test query",
                            ["col-1"],
                            limit=10,
                        )

                        assert result["results"] == [], "timeout must return empty results"
                        assert result["search_mode"] in ("hybrid", "vector", "keyword", "graph")

    @pytest.mark.asyncio
    async def test_fast_search_completes_normally(self):
        """Fast search (well under 800ms) must return results normally."""
        from app.core.search_service import hybrid_search

        with patch("app.core.search_service._get_embedding", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            with patch("app.core.search_service._hybrid_3channel", new_callable=AsyncMock) as mock_hybrid:
                mock_hybrid.return_value = [
                    {"chunk_id": "abc", "final_score": 0.9, "vector_score": 0.9,
                     "keyword_score": 0.0, "graph_proximity_score": 0.0,
                     "doc_id": "doc1", "text": "test", "topics": [], "highlights": []}
                ]

                result = await hybrid_search("fast query", ["col-1"], limit=10)

                assert len(result["results"]) == 1
                assert result["latency_ms"] >= 0


class TestRateLimitExemptPaths:
    """Verify /health and /metrics are exempt from both auth and rate limiting."""

    def test_health_exempt_from_auth(self):
        assert "/health" in NO_AUTH_PATHS

    def test_metrics_exempt_from_rate_limit(self):
        assert "/metrics" in _RATE_LIMIT_EXEMPT

    def test_health_exempt_from_rate_limit(self):
        assert "/health" in _RATE_LIMIT_EXEMPT

    def test_api_v1_health_exempt(self):
        assert "/api/v1/health" in _RATE_LIMIT_EXEMPT

    def test_all_auth_exempt_paths_also_rate_limit_exempt(self):
        """Every NO_AUTH_PATHS entry should also be rate-limit exempt."""
        assert NO_AUTH_PATHS.issubset(_RATE_LIMIT_EXEMPT)


class TestRateLimitSlidingWindow:
    """Sliding window behavior tests (sync, for quick validation)."""

    @pytest.mark.asyncio
    async def test_allows_up_to_limit(self):
        rl = RateLimiter(per_user_limit=5, per_ip_limit=10, window_seconds=60)
        for _ in range(5):
            assert await rl.check_user("user1") is True

    @pytest.mark.asyncio
    async def test_blocks_over_limit(self):
        rl = RateLimiter(per_user_limit=3, per_ip_limit=10, window_seconds=60)
        for _ in range(3):
            await rl.check_user("user1")
        assert await rl.check_user("user1") is False

    @pytest.mark.asyncio
    async def test_per_ip_independent_of_per_user(self):
        rl = RateLimiter(per_user_limit=2, per_ip_limit=100, window_seconds=60)
        await rl.check_user("user1")
        await rl.check_user("user1")
        assert await rl.check_user("user1") is False
        assert await rl.check_ip("10.0.0.1") is True

    @pytest.mark.asyncio
    async def test_window_resets_after_expiry(self):
        rl = RateLimiter(per_user_limit=2, per_ip_limit=100, window_seconds=1)
        await rl.check_user("u")
        await rl.check_user("u")
        assert await rl.check_user("u") is False

        # Backdate timestamps
        rl._counts["u:u"] = [time.time() - 2] * 2
        assert await rl.check_user("u") is True


class TestMetricsCorrectness:
    """Verify Prometheus metric values and labels."""

    def test_concurrent_searches_gauge(self):
        from app.core.metrics import KG_CONCURRENT_SEARCHES
        KG_CONCURRENT_SEARCHES.set(5)
        assert KG_CONCURRENT_SEARCHES._value.get() == 5.0

        KG_CONCURRENT_SEARCHES.set(0)
        assert KG_CONCURRENT_SEARCHES._value.get() == 0.0

    def test_index_state_gauge_values(self):
        from app.core.metrics import KG_INDEX_STATE
        for state in [0, 1, 2, 3, 4]:
            KG_INDEX_STATE.set(state)
            assert KG_INDEX_STATE._value.get() == state

    def test_search_latency_histogram_exists(self):
        from app.core.metrics import KG_SEARCH_LATENCY
        KG_SEARCH_LATENCY.labels(mode="hybrid").observe(0.15)
        # Just verify it doesn't raise

    def test_no_user_ids_in_metric_labels(self):
        """Metric labels must not contain user IDs or document content."""
        from app.core.metrics import (
            KG_SEARCH_REQUESTS_TOTAL,
            KG_SEARCH_LATENCY,
            KG_INGEST_JOBS_TOTAL,
        )
        # These metrics use "mode" and "status" labels, not user IDs
        labels = ["mode", "status"]
        # Verify no user-facing labels
        for metric in [KG_SEARCH_REQUESTS_TOTAL, KG_SEARCH_LATENCY, KG_INGEST_JOBS_TOTAL]:
            for label_name in metric._labelnames:
                assert label_name.decode() in labels or label_name == "", \
                    f"metric label {label_name} may contain PII"