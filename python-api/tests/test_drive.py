"""Tests for the drive router — ingestion, watch channel, and webhook."""

import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.routers.drive import router
from app.auth.middleware import get_current_user

FAKE_USER = {"id": "test-user-id", "email": "test@example.com", "name": "Test User"}
FAKE_COLLECTION_ID = str(uuid.uuid4())
FAKE_FOLDER_ID = "gdrive-folder-123"
FAKE_ACCESS_TOKEN = "ya29.test-token"
FAKE_COLLECTION = {
    "id": FAKE_COLLECTION_ID,
    "user_id": "test-user-id",
    "name": "Test Collection",
    "status": "active",
}
FAKE_CHANNEL_ID = str(uuid.uuid4())
FAKE_CHANNEL = {
    "channel_id": FAKE_CHANNEL_ID,
    "resource_id": "res-abc123",
    "collection_id": FAKE_COLLECTION_ID,
    "folder_id": FAKE_FOLDER_ID,
    "access_token": FAKE_ACCESS_TOKEN,
    "expiry_ms": 9999999999000,
    "created_at": 1700000000000000,
}


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    _app = FastAPI()
    _app.include_router(router, prefix="/drive")
    _app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    return _app


# ---------------------------------------------------------------------------
# POST /drive/ingest
# ---------------------------------------------------------------------------

class TestDriveIngest:
    async def test_returns_job_id(self, app):
        with (
            patch("app.routers.drive.get_collection", new_callable=AsyncMock, return_value=FAKE_COLLECTION),
            patch("app.routers.drive.create_ingest_job", new_callable=AsyncMock, return_value=None),
            patch("app.services.drive_service.run_drive_ingest_pipeline", new_callable=AsyncMock),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/drive/ingest",
                    json={
                        "collection_id": FAKE_COLLECTION_ID,
                        "folder_id": FAKE_FOLDER_ID,
                        "access_token": FAKE_ACCESS_TOKEN,
                    },
                )

        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "pending"
        assert data["collection_id"] == FAKE_COLLECTION_ID

    async def test_collection_not_found_returns_404(self, app):
        with patch("app.routers.drive.get_collection", new_callable=AsyncMock, return_value=None):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/drive/ingest",
                    json={
                        "collection_id": FAKE_COLLECTION_ID,
                        "folder_id": FAKE_FOLDER_ID,
                        "access_token": FAKE_ACCESS_TOKEN,
                    },
                )

        assert response.status_code == 404

    async def test_other_user_collection_returns_403(self, app):
        other = {**FAKE_COLLECTION, "user_id": "other-user"}
        with patch("app.routers.drive.get_collection", new_callable=AsyncMock, return_value=other):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/drive/ingest",
                    json={
                        "collection_id": FAKE_COLLECTION_ID,
                        "folder_id": FAKE_FOLDER_ID,
                        "access_token": FAKE_ACCESS_TOKEN,
                    },
                )

        assert response.status_code == 403

    async def test_response_includes_stream_url(self, app):
        with (
            patch("app.routers.drive.get_collection", new_callable=AsyncMock, return_value=FAKE_COLLECTION),
            patch("app.routers.drive.create_ingest_job", new_callable=AsyncMock, return_value=None),
            patch("app.services.drive_service.run_drive_ingest_pipeline", new_callable=AsyncMock),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/drive/ingest",
                    json={
                        "collection_id": FAKE_COLLECTION_ID,
                        "folder_id": FAKE_FOLDER_ID,
                        "access_token": FAKE_ACCESS_TOKEN,
                    },
                )

        assert "stream_url" in response.json()


# ---------------------------------------------------------------------------
# POST /drive/watch
# ---------------------------------------------------------------------------

class TestDriveWatch:
    async def test_registers_channel_successfully(self, app, mock_settings):
        mock_settings.drive_webhook_url = "https://example.com/api/v1/drive/webhook"
        channel_result = {
            "channel_id": FAKE_CHANNEL_ID,
            "resource_id": "res-abc",
            "collection_id": FAKE_COLLECTION_ID,
            "folder_id": FAKE_FOLDER_ID,
            "access_token": FAKE_ACCESS_TOKEN,
            "expiry_ms": 9999999999000,
        }
        with (
            patch("app.routers.drive.get_collection", new_callable=AsyncMock, return_value=FAKE_COLLECTION),
            patch("app.routers.drive.settings", mock_settings),
            patch("app.services.drive_service.register_watch_channel", new_callable=AsyncMock, return_value=channel_result),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/drive/watch",
                    json={
                        "collection_id": FAKE_COLLECTION_ID,
                        "folder_id": FAKE_FOLDER_ID,
                        "access_token": FAKE_ACCESS_TOKEN,
                    },
                )

        assert response.status_code == 201
        data = response.json()
        assert data["channel_id"] == FAKE_CHANNEL_ID
        assert "expiry_ms" in data

    async def test_no_webhook_url_returns_503(self, app, mock_settings):
        mock_settings.drive_webhook_url = ""
        with (
            patch("app.routers.drive.get_collection", new_callable=AsyncMock, return_value=FAKE_COLLECTION),
            patch("app.routers.drive.settings", mock_settings),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/drive/watch",
                    json={
                        "collection_id": FAKE_COLLECTION_ID,
                        "folder_id": FAKE_FOLDER_ID,
                        "access_token": FAKE_ACCESS_TOKEN,
                    },
                )

        assert response.status_code == 503

    async def test_watch_collection_not_found(self, app):
        with patch("app.routers.drive.get_collection", new_callable=AsyncMock, return_value=None):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/drive/watch",
                    json={
                        "collection_id": FAKE_COLLECTION_ID,
                        "folder_id": FAKE_FOLDER_ID,
                        "access_token": FAKE_ACCESS_TOKEN,
                    },
                )

        assert response.status_code == 404

    async def test_drive_api_error_returns_502(self, app, mock_settings):
        mock_settings.drive_webhook_url = "https://example.com/api/v1/drive/webhook"
        with (
            patch("app.routers.drive.get_collection", new_callable=AsyncMock, return_value=FAKE_COLLECTION),
            patch("app.routers.drive.settings", mock_settings),
            patch(
                "app.services.drive_service.register_watch_channel",
                new_callable=AsyncMock,
                side_effect=Exception("Drive API error"),
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/drive/watch",
                    json={
                        "collection_id": FAKE_COLLECTION_ID,
                        "folder_id": FAKE_FOLDER_ID,
                        "access_token": FAKE_ACCESS_TOKEN,
                    },
                )

        assert response.status_code == 502


# ---------------------------------------------------------------------------
# POST /drive/webhook
# ---------------------------------------------------------------------------

class TestDriveWebhook:
    async def test_sync_state_returns_200_no_job(self, app):
        """Initial 'sync' notification should be acknowledged without triggering a job."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/drive/webhook",
                headers={
                    "X-Goog-Channel-ID": FAKE_CHANNEL_ID,
                    "X-Goog-Resource-State": "sync",
                },
            )

        assert response.status_code == 200

    async def test_change_state_triggers_ingest(self, app):
        with (
            patch("app.routers.drive.get_drive_channel", new_callable=AsyncMock, return_value=FAKE_CHANNEL),
            patch("app.routers.drive.create_ingest_job", new_callable=AsyncMock, return_value=None),
            patch("app.services.drive_service.run_drive_ingest_pipeline", new_callable=AsyncMock),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/drive/webhook",
                    headers={
                        "X-Goog-Channel-ID": FAKE_CHANNEL_ID,
                        "X-Goog-Resource-State": "change",
                    },
                )

        assert response.status_code == 200

    async def test_unknown_channel_returns_200(self, app):
        """Unknown channel IDs are silently ignored — Google may retry."""
        with patch("app.routers.drive.get_drive_channel", new_callable=AsyncMock, return_value=None):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/drive/webhook",
                    headers={
                        "X-Goog-Channel-ID": "unknown-channel",
                        "X-Goog-Resource-State": "change",
                    },
                )

        assert response.status_code == 200

    async def test_missing_channel_header_returns_200(self, app):
        """Webhook without channel header should not crash."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/drive/webhook")

        assert response.status_code == 200

    async def test_update_state_triggers_ingest(self, app):
        with (
            patch("app.routers.drive.get_drive_channel", new_callable=AsyncMock, return_value=FAKE_CHANNEL),
            patch("app.routers.drive.create_ingest_job", new_callable=AsyncMock, return_value=None),
            patch("app.services.drive_service.run_drive_ingest_pipeline", new_callable=AsyncMock),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/drive/webhook",
                    headers={
                        "X-Goog-Channel-ID": FAKE_CHANNEL_ID,
                        "X-Goog-Resource-State": "update",
                    },
                )

        assert response.status_code == 200
