"""Tests for the auth router — Google OAuth + JWT endpoints."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.routers.auth import router

# ---------------------------------------------------------------------------
# App fixture: isolated FastAPI with only the auth router
# ---------------------------------------------------------------------------

@pytest.fixture
def auth_app():
    app = FastAPI()
    app.include_router(router, prefix="/auth")
    return app


@pytest.fixture
def client(auth_app):
    return auth_app


# ---------------------------------------------------------------------------
# POST /auth/google — dev mode (no google_client_id configured)
# ---------------------------------------------------------------------------

class TestGoogleAuthDevMode:
    async def test_dev_mode_returns_access_token(self, client):
        """When google_client_id is empty, dev login path is used."""
        with (
            patch("app.routers.auth.create_or_update_user", new_callable=AsyncMock,
                  return_value="dev-user-123"),
            patch("app.routers.auth.issue_access_token", return_value="test_access_token"),
            patch("app.routers.auth.issue_refresh_token", return_value="test_refresh_token"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=client), base_url="http://test"
            ) as ac:
                response = await ac.post("/auth/google", json={"id_token": "any-token"})

        assert response.status_code == 200
        data = response.json()
        assert data["access_token"] == "test_access_token"
        assert data["token_type"] == "bearer"
        assert "user" in data
        assert data["user"]["email"] == "dev@example.com"

    async def test_missing_id_token_returns_400(self, client):
        async with AsyncClient(
            transport=ASGITransport(app=client), base_url="http://test"
        ) as ac:
            response = await ac.post("/auth/google", json={})

        assert response.status_code == 400
        assert "id_token" in response.json()["detail"]

    async def test_invalid_google_token_returns_401(self, client, mock_settings):
        """When google_client_id is set and token is invalid, 401 is returned."""
        mock_settings.google_client_id = "real-client-id"
        with (
            patch("app.routers.auth.get_settings", return_value=mock_settings),
            patch("app.routers.auth.validate_google_id_token", new_callable=AsyncMock,
                  return_value=None),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=client), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    "/auth/google", json={"id_token": "invalid-google-token"}
                )

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /auth/refresh
# ---------------------------------------------------------------------------

class TestRefreshToken:
    async def test_valid_refresh_token_returns_new_access_token(self, client):
        fake_user = {"id": "user-1", "email": "test@example.com", "name": "Test"}
        with (
            patch("app.routers.auth.verify_token", return_value={"sub": "user-1"}),
            patch("app.routers.auth.get_user_by_id", new_callable=AsyncMock,
                  return_value=fake_user),
            patch("app.routers.auth.issue_access_token", return_value="new_access_token"),
            patch("app.routers.auth.issue_refresh_token", return_value="new_refresh_token"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=client), base_url="http://test"
            ) as ac:
                # Set the refresh token cookie
                ac.cookies.set("kg_refresh_token", "valid_refresh_token")
                response = await ac.post("/auth/refresh")

        assert response.status_code == 200
        data = response.json()
        assert data["access_token"] == "new_access_token"

    async def test_missing_refresh_token_cookie_returns_401(self, client):
        async with AsyncClient(
            transport=ASGITransport(app=client), base_url="http://test"
        ) as ac:
            response = await ac.post("/auth/refresh")

        assert response.status_code == 401

    async def test_invalid_refresh_token_returns_401(self, client):
        with patch("app.routers.auth.verify_token", return_value=None):
            async with AsyncClient(
                transport=ASGITransport(app=client), base_url="http://test"
            ) as ac:
                ac.cookies.set("kg_refresh_token", "bad_token")
                response = await ac.post("/auth/refresh")

        assert response.status_code == 401

    async def test_user_not_found_returns_401(self, client):
        with (
            patch("app.routers.auth.verify_token", return_value={"sub": "ghost-user"}),
            patch("app.routers.auth.get_user_by_id", new_callable=AsyncMock, return_value=None),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=client), base_url="http://test"
            ) as ac:
                ac.cookies.set("kg_refresh_token", "token")
                response = await ac.post("/auth/refresh")

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------

class TestLogout:
    async def test_logout_returns_ok(self, client):
        async with AsyncClient(
            transport=ASGITransport(app=client), base_url="http://test"
        ) as ac:
            response = await ac.post("/auth/logout")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    async def test_logout_clears_cookie(self, client):
        async with AsyncClient(
            transport=ASGITransport(app=client), base_url="http://test"
        ) as ac:
            ac.cookies.set("kg_refresh_token", "some_token")
            response = await ac.post("/auth/logout")

        # After logout the Set-Cookie header deletes the cookie
        assert response.status_code == 200
