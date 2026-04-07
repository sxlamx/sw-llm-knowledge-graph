"""Integration tests for rate limiting middleware.

Tests:
  - 429 returned after limit exceeded (per-user and per-IP)
  - /health and /metrics never rate-limited
  - Rate limit resets after window expires
  - Per-user quota is independent (not global)
  - Rate limiter uses sliding window (not token bucket)
"""

import time
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth.middleware import (
    rate_limit_middleware,
    RateLimiter,
    _RATE_LIMIT_EXEMPT,
    NO_AUTH_PATHS,
)


class TestRateLimiterSlidingWindow:
    """Tests for sliding window behavior."""

    def test_sliding_window_not_token_bucket(self):
        """Verify sliding window: requests at start of window don't affect later requests."""
        rl = RateLimiter(per_user_limit=3, per_ip_limit=100, window_seconds=1)

        # Exhaust limit at time=0
        assert rl.check_user("u") is True
        assert rl.check_user("u") is True
        assert rl.check_user("u") is True
        assert rl.check_user("u") is False

        # Advance to middle of window — still blocked
        rl._counts["u:u"] = [time.time() - 0.5] * 3
        assert rl.check_user("u") is False

        # After window expires, allow again
        rl._counts["u:u"] = [time.time() - 1.1]
        assert rl.check_user("u") is True

    def test_expired_entries_evicted_from_counts(self):
        """Entries outside the window must be evicted on each check."""
        rl = RateLimiter(per_user_limit=2, per_ip_limit=100, window_seconds=1)

        rl._counts["u:u"] = [time.time() - 2, time.time() - 0.5, time.time() - 0.5]
        result = rl.check_user("u")

        assert result is True
        # The 2 expired entries should be gone, leaving 2 (one new + one remaining)
        assert len(rl._counts["u:u"]) == 2

    def test_empty_counts_key_returns_true(self):
        """Unknown user/IP key should start fresh with an empty list."""
        rl = RateLimiter(per_user_limit=5, per_ip_limit=100, window_seconds=60)
        assert rl.check_user("brand-new-user") is True
        assert rl._counts["u:brand-new-user"] == [time.time()]


class TestRateLimitMiddlewareIntegration:
    """Tests for the middleware wired into a FastAPI app."""

    @pytest.fixture
    def rate_limited_app(self):
        """FastAPI app with rate_limit_middleware and a protected route."""
        app = FastAPI()

        app.add_middleware(BaseHTTPMiddleware, dispatch=rate_limit_middleware)

        @app.get("/api/v1/collections")
        async def collections():
            return {"collections": []}

        @app.get("/health")
        async def health():
            return {"status": "ok"}

        @app.get("/metrics")
        async def metrics():
            return "kg_concurrent_searches 0"

        return app

    @pytest.fixture
    def client(self, rate_limited_app):
        return TestClient(rate_limited_app)

    def test_exempt_paths_not_rate_limited(self, client):
        """Paths in NO_AUTH_PATHS or _RATE_LIMIT_EXEMPT must never be rate-limited."""
        for path in ["/health", "/metrics"]:
            for _ in range(200):
                resp = client.get(path)
                assert resp.status_code != 429, f"{path} should never be rate-limited"

    def test_61st_request_returns_429(self, client):
        """61st request within the 60s window must return 429."""
        headers = {"Authorization": "Bearer test-token"}

        for i in range(60):
            resp = client.get("/api/v1/collections", headers=headers)
            assert resp.status_code != 429, f"request {i+1} should not yet be rate-limited"

        resp = client.get("/api/v1/collections", headers=headers)
        assert resp.status_code == 429, "61st request must be rate-limited"
        assert "Retry-After" in resp.headers
        assert "X-RateLimit-Limit" in resp.headers

    def test_rate_limit_headers_present(self, client):
        """429 response must include Retry-After and X-RateLimit-Limit."""
        headers = {"Authorization": "Bearer test-token"}

        # Exhaust limit
        for _ in range(60):
            client.get("/api/v1/collections", headers=headers)

        resp = client.get("/api/v1/collections", headers=headers)
        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "60"
        assert resp.headers["X-RateLimit-Limit"] == "60"

    def test_health_endpoint_never_rate_limited(self, client):
        """Health endpoint must never return 429 regardless of request count."""
        for _ in range(500):
            resp = client.get("/health")
            assert resp.status_code != 429, "health endpoint must never be rate-limited"

    def test_metrics_endpoint_never_rate_limited(self, client):
        """Metrics endpoint must never return 429 — Prometheus must be able to scrape freely."""
        for _ in range(500):
            resp = client.get("/metrics")
            assert resp.status_code != 429, "metrics endpoint must never be rate-limited"

    def test_different_users_independent_quota(self, client):
        """User A exhausting their quota must not affect User B."""
        headers_alice = {"Authorization": "Bearer alice-token"}
        headers_bob = {"Authorization": "Bearer bob-token"}

        # Alice exhausts her limit
        for _ in range(60):
            client.get("/api/v1/collections", headers=headers_alice)

        # Bob's requests must still succeed
        resp = client.get("/api/v1/collections", headers=headers_bob)
        assert resp.status_code != 429, "Bob must not be affected by Alice's exhausted quota"

    def test_rate_limit_window_resets(self, client):
        """After the window expires, requests should succeed again."""
        headers = {"Authorization": "Bearer test-token"}

        # Exhaust limit
        for _ in range(60):
            client.get("/api/v1/collections", headers=headers)

        resp = client.get("/api/v1/collections", headers=headers)
        assert resp.status_code == 429

        # Simulate window expiry by clearing the counts
        from app.auth import middleware
        middleware.rate_limiter._counts.clear()

        # Now requests should succeed again
        resp = client.get("/api/v1/collections", headers=headers)
        assert resp.status_code != 429, "requests must succeed after counter reset"

    def test_ip_rate_limit_enforced(self, client):
        """IP-level rate limit should be per-IP, not global."""
        # Use a fresh app that has no prior IP history
        from app.auth import middleware
        middleware.rate_limiter._counts.clear()

        # Make IP-limit requests (IP is 192.168.0.1 for test client by default)
        # The per_ip_limit is 3x per_user_limit = 180 by default
        # We test that different IPs have independent quotas
        rl = RateLimiter(per_user_limit=60, per_ip_limit=3, window_seconds=60)

        # Simulate 3 requests from same IP
        assert rl.check_ip("1.2.3.4") is True
        assert rl.check_ip("1.2.3.4") is True
        assert rl.check_ip("1.2.3.4") is True
        assert rl.check_ip("1.2.3.4") is False

        # Different IP still allowed
        assert rl.check_ip("5.6.7.8") is True


class TestRateLimitExemptPaths:
    """Verify exact paths that are exempt from rate limiting."""

    def test_known_exempt_paths(self):
        """All paths in _RATE_LIMIT_EXEMPT must be exempt."""
        expected = {
            "/health",
            "/api/v1/health",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/api/v1/auth/google",
            "/api/v1/auth/google/redirect",
            "/api/v1/auth/google/exchange",
            "/api/v1/auth/refresh",
            "/metrics",
        }
        assert expected.issubset(_RATE_LIMIT_EXEMPT)

    def test_metrics_is_exempt(self):
        """/metrics must be in the exempt set."""
        assert "/metrics" in _RATE_LIMIT_EXEMPT

    def test_health_is_exempt(self):
        """Both /health and /api/v1/health must be exempt."""
        assert "/health" in _RATE_LIMIT_EXEMPT
        assert "/api/v1/health" in _RATE_LIMIT_EXEMPT

    def test_ws_paths_not_rate_limited(self):
        """WebSocket upgrade requests are exempt (checked by path.startswith('/ws'))."""
        app = FastAPI()

        async def dummy_ws_handler(request: Request):
            return {"ws": True}

        app.add_middleware(BaseHTTPMiddleware, dispatch=rate_limit_middleware)
        app.add_api_route("/ws/graph", dummy_ws_handler)

        client = TestClient(app)
        for _ in range(1000):
            resp = client.get("/ws/graph")
            assert resp.status_code != 429, "WebSocket paths must not be rate-limited"
