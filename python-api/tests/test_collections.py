"""Tests for the collections router — CRUD operations."""

import pytest
import uuid
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.routers.collections import router
from app.auth.middleware import get_current_user

FAKE_USER = {"id": "test-user-id", "email": "test@example.com", "name": "Test User"}
FAKE_COLLECTION_ID = str(uuid.uuid4())

FAKE_COLLECTION = {
    "id": FAKE_COLLECTION_ID,
    "user_id": "test-user-id",
    "name": "My Collection",
    "description": "A test collection",
    "folder_path": "/tmp/docs",
    "status": "active",
    "doc_count": 3,
    "created_at": 1700000000000000,
    "updated_at": 1700000001000000,
}

# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    _app = FastAPI()
    _app.include_router(router, prefix="/collections")
    _app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    return _app


# ---------------------------------------------------------------------------
# GET /collections
# ---------------------------------------------------------------------------

class TestListCollections:
    async def test_returns_empty_list(self, app):
        with patch(
            "app.routers.collections.list_collections",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get("/collections")

        assert response.status_code == 200
        assert response.json() == {"collections": []}

    async def test_returns_collections_list(self, app):
        with patch(
            "app.routers.collections.list_collections",
            new_callable=AsyncMock,
            return_value=[FAKE_COLLECTION],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get("/collections")

        assert response.status_code == 200
        data = response.json()
        assert len(data["collections"]) == 1
        assert data["collections"][0]["name"] == "My Collection"
        assert data["collections"][0]["doc_count"] == 3

    async def test_multiple_collections_returned(self, app):
        coll2 = {**FAKE_COLLECTION, "id": str(uuid.uuid4()), "name": "Second"}
        with patch(
            "app.routers.collections.list_collections",
            new_callable=AsyncMock,
            return_value=[FAKE_COLLECTION, coll2],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get("/collections")

        assert response.status_code == 200
        assert len(response.json()["collections"]) == 2


# ---------------------------------------------------------------------------
# POST /collections
# ---------------------------------------------------------------------------

class TestCreateCollection:
    async def test_creates_collection_returns_201(self, app):
        with patch(
            "app.routers.collections.create_collection",
            new_callable=AsyncMock,
            return_value=None,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    "/collections",
                    json={"name": "New Collection", "description": "Desc"},
                )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "New Collection"
        assert data["description"] == "Desc"
        assert data["status"] == "active"
        assert data["doc_count"] == 0
        # ID should be a valid UUID
        uuid.UUID(data["id"])

    async def test_collection_with_folder_path(self, app):
        with patch(
            "app.routers.collections.create_collection",
            new_callable=AsyncMock,
            return_value=None,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    "/collections",
                    json={"name": "Docs", "folder_path": "/data/docs"},
                )

        assert response.status_code == 201
        assert response.json()["folder_path"] == "/data/docs"

    async def test_missing_name_returns_422(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post("/collections", json={})

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /collections/{collection_id}
# ---------------------------------------------------------------------------

class TestGetCollection:
    async def test_returns_collection_details(self, app):
        with patch(
            "app.routers.collections.get_collection",
            new_callable=AsyncMock,
            return_value=FAKE_COLLECTION,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get(f"/collections/{FAKE_COLLECTION_ID}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == FAKE_COLLECTION_ID
        assert data["name"] == "My Collection"

    async def test_not_found_returns_404(self, app):
        with patch(
            "app.routers.collections.get_collection",
            new_callable=AsyncMock,
            return_value=None,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get(f"/collections/{FAKE_COLLECTION_ID}")

        assert response.status_code == 404

    async def test_other_user_collection_returns_403(self, app):
        other_user_collection = {**FAKE_COLLECTION, "user_id": "other-user-999"}
        with patch(
            "app.routers.collections.get_collection",
            new_callable=AsyncMock,
            return_value=other_user_collection,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get(f"/collections/{FAKE_COLLECTION_ID}")

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /collections/{collection_id}
# ---------------------------------------------------------------------------

class TestDeleteCollection:
    async def test_delete_returns_204(self, app):
        with (
            patch(
                "app.routers.collections.get_collection",
                new_callable=AsyncMock,
                return_value=FAKE_COLLECTION,
            ),
            patch(
                "app.routers.collections.delete_collection",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.delete(f"/collections/{FAKE_COLLECTION_ID}")

        assert response.status_code == 204

    async def test_delete_not_found_returns_404(self, app):
        with patch(
            "app.routers.collections.get_collection",
            new_callable=AsyncMock,
            return_value=None,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.delete(f"/collections/{FAKE_COLLECTION_ID}")

        assert response.status_code == 404

    async def test_delete_other_user_collection_returns_403(self, app):
        other = {**FAKE_COLLECTION, "user_id": "other-user"}
        with patch(
            "app.routers.collections.get_collection",
            new_callable=AsyncMock,
            return_value=other,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.delete(f"/collections/{FAKE_COLLECTION_ID}")

        assert response.status_code == 403
