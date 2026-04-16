"""Tests for admin router — user management and NER re-tagging."""

import pytest
import uuid
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.routers.admin import router
from app.auth.middleware import require_admin, get_current_user

FAKE_ADMIN = {"id": "admin-user-id", "email": "admin@example.com", "name": "Admin", "role": "admin"}
FAKE_USER = {"id": "regular-user-id", "email": "user@example.com", "name": "Regular"}

FAKE_USER_ROW = {
    "id": "user-abc",
    "email": "user@example.com",
    "name": "Test User",
    "avatar_url": None,
    "role": "user",
    "status": "active",
    "created_at": 1700000000000000,
    "last_login": 1700000010000000,
}

FAKE_COLLECTION_ID = str(uuid.uuid4())


@pytest.fixture
def admin_app():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/admin")
    app.dependency_overrides[require_admin] = lambda: FAKE_ADMIN
    app.dependency_overrides[get_current_user] = lambda: FAKE_ADMIN
    return app


@pytest.fixture
def non_admin_app():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/admin")
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    app.dependency_overrides[require_admin] = lambda: (_ for _ in ()).throw(
        __import__("fastapi").HTTPException(status_code=403, detail="Admin access required")
    )
    return app


class TestAdminListUsers:
    async def test_list_users_requires_admin(self, non_admin_app):
        with patch(
            "app.routers.admin.list_users",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=non_admin_app), base_url="http://test"
            ) as ac:
                response = await ac.get("/api/v1/admin/users")

        assert response.status_code == 403

    async def test_list_users_returns_all(self, admin_app):
        user2 = {**FAKE_USER_ROW, "id": "user-def", "email": "other@example.com"}
        with patch(
            "app.routers.admin.list_users",
            new_callable=AsyncMock,
            return_value=[FAKE_USER_ROW, user2],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=admin_app), base_url="http://test"
            ) as ac:
                response = await ac.get("/api/v1/admin/users")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["email"] == "user@example.com"
        assert data[1]["email"] == "other@example.com"


class TestAdminUpdateUser:
    async def test_update_role_requires_admin(self, non_admin_app):
        async with AsyncClient(
            transport=ASGITransport(app=non_admin_app), base_url="http://test"
        ) as ac:
            response = await ac.patch(
                "/api/v1/admin/users/user-abc",
                json={"role": "admin"},
            )

        assert response.status_code == 403

    async def test_update_invalid_role_rejected(self, admin_app):
        async with AsyncClient(
            transport=ASGITransport(app=admin_app), base_url="http://test"
        ) as ac:
            response = await ac.patch(
                "/api/v1/admin/users/user-abc",
                json={"role": "superadmin"},
            )

        assert response.status_code == 422

    async def test_update_invalid_status_rejected(self, admin_app):
        async with AsyncClient(
            transport=ASGITransport(app=admin_app), base_url="http://test"
        ) as ac:
            response = await ac.patch(
                "/api/v1/admin/users/user-abc",
                json={"status": "suspended"},
            )

        assert response.status_code == 422


class TestAdminNerRetag:
    async def test_start_ner_retag_requires_admin(self, non_admin_app):
        async with AsyncClient(
            transport=ASGITransport(app=non_admin_app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                f"/api/v1/admin/collections/{FAKE_COLLECTION_ID}/ner-tag"
            )

        assert response.status_code == 403

    async def test_start_ner_retag_returns_job_id(self, admin_app):
        with patch(
            "app.routers.admin.get_chunks_for_collection",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=admin_app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    f"/api/v1/admin/collections/{FAKE_COLLECTION_ID}/ner-tag"
                )

        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert data["collection_id"] == FAKE_COLLECTION_ID
        assert data["status"] == "started"

    async def test_get_ner_retag_status_requires_admin(self, non_admin_app):
        async with AsyncClient(
            transport=ASGITransport(app=non_admin_app), base_url="http://test"
        ) as ac:
            response = await ac.get(
                f"/api/v1/admin/collections/{FAKE_COLLECTION_ID}/ner-tag/{str(uuid.uuid4())}"
            )

        assert response.status_code == 403


class TestAdminNerStats:
    async def test_ner_stats_requires_admin(self, non_admin_app):
        with patch(
            "app.routers.admin.get_chunks_for_collection",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=non_admin_app), base_url="http://test"
            ) as ac:
                response = await ac.get(
                    f"/api/v1/admin/collections/{FAKE_COLLECTION_ID}/ner-stats"
                )

        assert response.status_code == 403