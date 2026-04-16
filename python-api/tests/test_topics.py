"""Tests for topics router."""

import pytest
import uuid
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.routers.topics import router
from app.auth.middleware import get_current_user

FAKE_USER = {"id": "test-user-id", "email": "test@example.com", "name": "Test User"}
FAKE_OTHER_USER = {"id": "other-user-id", "email": "other@example.com", "name": "Other User"}
FAKE_COLLECTION_ID = str(uuid.uuid4())

FAKE_COLLECTION = {
    "id": FAKE_COLLECTION_ID,
    "user_id": "test-user-id",
    "name": "Test Collection",
    "status": "active",
}

FAKE_NODES = [
    {
        "id": "node-1",
        "label": "Node A",
        "topics": ["machine learning", "AI"],
    },
    {
        "id": "node-2",
        "label": "Node B",
        "topics": ["machine learning"],
    },
    {
        "id": "node-3",
        "label": "Node C",
        "topics": ["law"],
    },
]


@pytest.fixture
def app():
    _app = FastAPI()
    _app.include_router(router, prefix="/api/v1/topics")
    _app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    return _app


@pytest.fixture
def other_user_app():
    _app = FastAPI()
    _app.include_router(router, prefix="/api/v1/topics")
    _app.dependency_overrides[get_current_user] = lambda: FAKE_OTHER_USER
    return _app


class TestListTopics:
    async def test_list_topics_requires_collection_id(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get("/api/v1/topics")

        assert response.status_code == 422

    async def test_list_topics_requires_auth(self):
        _app = FastAPI()
        _app.include_router(router, prefix="/api/v1/topics")
        async with AsyncClient(
            transport=ASGITransport(app=_app), base_url="http://test"
        ) as ac:
            response = await ac.get(
                "/api/v1/topics",
                params={"collection_id": FAKE_COLLECTION_ID},
            )

        assert response.status_code == 401

    async def test_list_topics_returns_topics(self, app):
        with (
            patch(
                "app.routers.topics.list_topics",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "app.routers.topics.list_graph_nodes",
                new_callable=AsyncMock,
                return_value=FAKE_NODES,
            ),
            patch(
                "app.routers.topics.get_collection",
                new_callable=AsyncMock,
                return_value=FAKE_COLLECTION,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get(
                    "/api/v1/topics",
                    params={"collection_id": FAKE_COLLECTION_ID},
                )

        assert response.status_code == 200
        data = response.json()
        assert "topics" in data
        topic_names = [t["name"] for t in data["topics"]]
        assert "machine learning" in topic_names
        assert "AI" in topic_names

    async def test_list_topics_denied_for_other_user(self, other_user_app):
        with patch(
            "app.routers.topics.get_collection",
            new_callable=AsyncMock,
            return_value=FAKE_COLLECTION,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=other_user_app), base_url="http://test"
            ) as ac:
                response = await ac.get(
                    "/api/v1/topics",
                    params={"collection_id": FAKE_COLLECTION_ID},
                )

        assert response.status_code == 403


class TestGetTopicNodes:
    async def test_get_topic_nodes_returns_matching(self, app):
        with (
            patch(
                "app.routers.topics.list_graph_nodes",
                new_callable=AsyncMock,
                return_value=FAKE_NODES,
            ),
            patch(
                "app.routers.topics.get_collection",
                new_callable=AsyncMock,
                return_value=FAKE_COLLECTION,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get(
                    "/api/v1/topics/machine_learning/nodes",
                    params={"collection_id": FAKE_COLLECTION_ID},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["topic_id"] == "machine_learning"
        matching_labels = [n["label"] for n in data["nodes"]]
        assert "Node A" in matching_labels
        assert "Node B" in matching_labels
        assert "Node C" not in matching_labels