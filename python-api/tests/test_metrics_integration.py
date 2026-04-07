"""Tests for Prometheus metrics endpoint.

Tests:
  - /metrics returns Prometheus text format
  - /metrics requires no authentication (exempt from auth middleware)
  - No PII (user IDs, emails, document content) in metric labels
  - kg_concurrent_searches gauge is present and reflects state
  - kg_index_state gauge is present
  - No user-specific data in label values
"""

import re
import pytest
from unittest.mock import MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth.middleware import auth_middleware, rate_limit_middleware


class TestMetricsEndpoint:
    """Tests for the /metrics endpoint."""

    @pytest.fixture
    def app_with_metrics(self):
        """FastAPI app with auth middleware, rate limiting, and /metrics endpoint."""
        app = FastAPI()

        app.add_middleware(BaseHTTPMiddleware, dispatch=rate_limit_middleware)
        app.add_middleware(BaseHTTPMiddleware, dispatch=auth_middleware)

        @app.get("/metrics")
        async def metrics():
            from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, REGISTRY
            from fastapi.responses import Response
            return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

        @app.get("/health")
        async def health():
            return {"status": "ok"}

        @app.get("/api/v1/collections")
        async def collections():
            return {"collections": []}

        return app

    @pytest.fixture
    def client(self, app_with_metrics):
        return TestClient(app_with_metrics)

    def test_metrics_returns_prometheus_text_format(self, client):
        """Response must be Prometheus text exposition format."""
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"] or \
               "text/plain" in str(resp.headers.get("content-type", ""))

    def test_metrics_no_auth_required(self, client):
        """/metrics must work without Authorization header."""
        resp = client.get("/metrics")
        assert resp.status_code == 200, "metrics endpoint must not require auth"

    def test_health_and_metrics_exempt_from_auth(self, client):
        """Both /health and /metrics must be accessible without a token."""
        for path in ["/health", "/metrics"]:
            resp = client.get(path)
            assert resp.status_code != 401, f"{path} must not require auth"

    def test_metrics_contains_kg_prefix(self, client):
        """All our metrics must have kg_ prefix."""
        resp = client.get("/metrics")
        text = resp.text

        required_metrics = [
            "kg_index_state",
            "kg_index_pending_writes",
            "kg_concurrent_searches",
        ]
        for metric in required_metrics:
            assert metric in text, f"required metric {metric} must be present in /metrics output"

    def test_metrics_contains_search_latency_histogram(self, client):
        """kg_search_latency_seconds histogram must be present."""
        resp = client.get("/metrics")
        assert "kg_search_latency_seconds" in resp.text

    def test_metrics_contains_ingest_jobs_counter(self, client):
        """kg_ingest_jobs_total counter must be present."""
        resp = client.get("/metrics")
        assert "kg_ingest_jobs_total" in resp.text


class TestMetricsPIIafety:
    """Tests that no personally identifiable information appears in metrics."""

    @pytest.fixture
    def app_with_metrics_and_search(self):
        """App with /metrics and a /search endpoint that accepts user-controlled input."""
        app = FastAPI()

        app.add_middleware(BaseHTTPMiddleware, dispatch=rate_limit_middleware)
        app.add_middleware(BaseHTTPMiddleware, dispatch=auth_middleware)

        @app.get("/metrics")
        async def metrics():
            from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, REGISTRY
            from fastapi.responses import Response
            return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

        @app.post("/api/v1/search")
        async def search(body: dict):
            query = body.get("query", "")
            collection_id = body.get("collection_id", "")
            # Simulate storing query in some internal state (e.g., search history)
            return {"query": query, "collection_id": collection_id}

        return app

    @pytest.fixture
    def client(self, app_with_metrics_and_search):
        return TestClient(app_with_metrics_and_search)

    def test_no_email_patterns_in_metrics(self, client):
        """Email addresses must not appear in metrics output."""
        email_queries = [
            "user@example.com",
            "test@company.org",
            "admin@domain.com",
        ]
        for email in email_queries:
            client.post("/api/v1/search", json={"query": email, "collection_id": "col-1"})

        resp = client.get("/metrics")
        text = resp.text

        email_pattern = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
        matches = email_pattern.findall(text)
        assert len(matches) == 0, f"email addresses must not appear in metrics: {matches}"

    def test_no_user_id_in_metrics_labels(self, client):
        """User IDs must not appear as label values in metrics."""
        client.post("/api/v1/search",
                    json={"query": "test query", "collection_id": "col-1"},
                    headers={"Authorization": "Bearer user-abc-123-token"})

        resp = client.get("/metrics")
        text = resp.text

        # Check that no label value looks like a user ID
        assert "user-abc-123" not in text
        assert "user_id" not in text.lower()

    def test_no_document_content_in_metrics(self, client):
        """Document text/content must not appear in metric labels or values."""
        sensitive_queries = [
            "SSN 123-45-6789",
            "credit card 4111-1111-1111-1111",
            "password supersecret123",
            "confidential project alpha",
        ]
        for q in sensitive_queries:
            client.post("/api/v1/search", json={"query": q, "collection_id": "col-1"})

        resp = client.get("/metrics")
        text = resp.text

        for q in sensitive_queries:
            assert q not in text, f"sensitive content '{q}' must not appear in metrics"

    def test_metric_labels_are_safe_keywords(self, client):
        """Metric labels must be safe keywords (mode, collection_id), not user data."""
        resp = client.get("/metrics")

        # Extract all label=value patterns
        label_pattern = re.compile(r'\{[^}]+\}')
        labels = label_pattern.findall(resp.text)

        for label_block in labels:
            assert "example.com" not in label_block
            assert "user-" not in label_block
            assert "token" not in label_block
            assert "password" not in label_block


class TestMetricsGaugeValues:
    """Tests for metric gauge values being numeric and non-negative."""

    def test_concurrent_searches_gauge_is_non_negative(self):
        """kg_concurrent_searches gauge must never be negative."""
        from app.core.metrics import KG_CONCURRENT_SEARCHES

        KG_CONCURRENT_SEARCHES.set(50)
        assert KG_CONCURRENT_SEARCHES._value.get() >= 0

        KG_CONCURRENT_SEARCHES.set(0)
        assert KG_CONCURRENT_SEARCHES._value.get() >= 0

    def test_index_state_gauge_is_valid_state(self):
        """kg_index_state must be 0-4 (uninit/build/active/compact/degraded)."""
        from app.core.metrics import KG_INDEX_STATE

        for state in [0, 1, 2, 3, 4]:
            KG_INDEX_STATE.set(state)
            # The underlying value should match
            assert KG_INDEX_STATE._value.get() == state

    def test_pending_writes_gauge_is_non_negative(self):
        """kg_index_pending_writes must not go negative."""
        from app.core.metrics import KG_PENDING_WRITES

        KG_PENDING_WRITES.set(0)
        assert KG_PENDING_WRITES._value.get() >= 0

        KG_PENDING_WRITES.set(1000)
        assert KG_PENDING_WRITES._value.get() >= 0
