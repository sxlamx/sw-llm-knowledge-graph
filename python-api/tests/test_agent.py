"""Tests for the agent router — ReAct-style Graph RAG streaming."""

import json
import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.routers.agent import router
from app.auth.middleware import get_current_user

FAKE_USER = {"id": "test-user-id", "email": "test@example.com", "name": "Test User"}
FAKE_COLLECTION_ID = str(uuid.uuid4())
FAKE_COLLECTION = {
    "id": FAKE_COLLECTION_ID,
    "user_id": "test-user-id",
    "name": "Test Collection",
    "status": "active",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _fake_agent_gen(collection_id, query, max_hops):
    yield {"type": "start", "query": query, "collection_id": collection_id}
    yield {"type": "thought", "hop": 0, "content": "Searching..."}
    yield {"type": "observation", "hop": 0, "content": "Found 3 chunks."}
    yield {"type": "token", "content": "The answer is 42."}
    yield {"type": "answer", "content": "The answer is 42.", "hops_taken": 1, "nodes_visited": []}


def _parse_sse(body: str) -> list[dict]:
    """Parse raw SSE body into a list of event dicts."""
    events = []
    for line in body.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            events.append(json.loads(line[6:]))
    return events


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    _app = FastAPI()
    _app.include_router(router, prefix="/agent")
    _app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    return _app


# ---------------------------------------------------------------------------
# POST /agent/query
# ---------------------------------------------------------------------------

class TestAgentQuery:
    async def test_streams_sse_events(self, app):
        with (
            patch("app.routers.agent.get_collection", new_callable=AsyncMock, return_value=FAKE_COLLECTION),
            patch("app.routers.agent.run_agent", side_effect=_fake_agent_gen),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/agent/query",
                    json={"collection_id": FAKE_COLLECTION_ID, "query": "What is the answer?", "max_hops": 3},
                )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        events = _parse_sse(response.text)
        types = [e["type"] for e in events]
        assert "start" in types
        assert "thought" in types
        assert "token" in types
        assert "answer" in types

    async def test_start_event_contains_query(self, app):
        with (
            patch("app.routers.agent.get_collection", new_callable=AsyncMock, return_value=FAKE_COLLECTION),
            patch("app.routers.agent.run_agent", side_effect=_fake_agent_gen),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/agent/query",
                    json={"collection_id": FAKE_COLLECTION_ID, "query": "hello", "max_hops": 2},
                )

        events = _parse_sse(response.text)
        start = next(e for e in events if e["type"] == "start")
        assert start["query"] == "hello"

    async def test_answer_event_has_hops_taken(self, app):
        with (
            patch("app.routers.agent.get_collection", new_callable=AsyncMock, return_value=FAKE_COLLECTION),
            patch("app.routers.agent.run_agent", side_effect=_fake_agent_gen),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/agent/query",
                    json={"collection_id": FAKE_COLLECTION_ID, "query": "q", "max_hops": 4},
                )

        events = _parse_sse(response.text)
        answer = next(e for e in events if e["type"] == "answer")
        assert "hops_taken" in answer
        assert "nodes_visited" in answer

    async def test_collection_not_found_returns_404(self, app):
        with patch("app.routers.agent.get_collection", new_callable=AsyncMock, return_value=None):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/agent/query",
                    json={"collection_id": FAKE_COLLECTION_ID, "query": "q"},
                )

        assert response.status_code == 404

    async def test_other_user_collection_returns_403(self, app):
        other_collection = {**FAKE_COLLECTION, "user_id": "other-user"}
        with patch("app.routers.agent.get_collection", new_callable=AsyncMock, return_value=other_collection):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/agent/query",
                    json={"collection_id": FAKE_COLLECTION_ID, "query": "q"},
                )

        assert response.status_code == 403

    async def test_agent_exception_yields_error_event(self, app):
        async def _error_gen(collection_id, query, max_hops):
            raise RuntimeError("agent failed")
            yield  # make it an async generator

        with (
            patch("app.routers.agent.get_collection", new_callable=AsyncMock, return_value=FAKE_COLLECTION),
            patch("app.routers.agent.run_agent", side_effect=_error_gen),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/agent/query",
                    json={"collection_id": FAKE_COLLECTION_ID, "query": "q"},
                )

        assert response.status_code == 200
        events = _parse_sse(response.text)
        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert "agent failed" in error_events[0]["content"]

    async def test_max_hops_above_limit_rejected(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/agent/query",
                json={"collection_id": FAKE_COLLECTION_ID, "query": "q", "max_hops": 99},
            )

        assert response.status_code == 422

    async def test_empty_query_rejected(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/agent/query",
                json={"collection_id": FAKE_COLLECTION_ID, "query": ""},
            )

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /agent/status
# ---------------------------------------------------------------------------

class TestAgentStatus:
    async def test_returns_ready_when_nodes_present(self, app):
        nodes = [{"id": "n1", "label": "Entity"}]
        edges = [{"id": "e1"}]
        with (
            patch("app.routers.agent.get_collection", new_callable=AsyncMock, return_value=FAKE_COLLECTION),
            patch("app.routers.agent.list_graph_nodes", new_callable=AsyncMock, return_value=nodes),
            patch("app.routers.agent.list_graph_edges", new_callable=AsyncMock, return_value=edges),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/agent/status", params={"collection_id": FAKE_COLLECTION_ID})

        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True
        assert data["node_count"] == 1
        assert data["edge_count"] == 1

    async def test_returns_not_ready_when_empty_graph(self, app):
        with (
            patch("app.routers.agent.get_collection", new_callable=AsyncMock, return_value=FAKE_COLLECTION),
            patch("app.routers.agent.list_graph_nodes", new_callable=AsyncMock, return_value=[]),
            patch("app.routers.agent.list_graph_edges", new_callable=AsyncMock, return_value=[]),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/agent/status", params={"collection_id": FAKE_COLLECTION_ID})

        assert response.status_code == 200
        assert response.json()["ready"] is False

    async def test_status_collection_not_found(self, app):
        with patch("app.routers.agent.get_collection", new_callable=AsyncMock, return_value=None):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/agent/status", params={"collection_id": FAKE_COLLECTION_ID})

        assert response.status_code == 404
