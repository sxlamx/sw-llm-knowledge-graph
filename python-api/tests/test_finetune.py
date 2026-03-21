"""Tests for the finetune router — dataset export and OpenAI fine-tuning job management."""

import pytest
import uuid
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.routers.finetune import router
from app.auth.middleware import get_current_user

FAKE_USER = {"id": "test-user-id", "email": "test@example.com", "name": "Test User"}
FAKE_COLLECTION_ID = str(uuid.uuid4())
FAKE_JOB_ID = "ftjob-abc123"
FAKE_COLLECTION = {
    "id": FAKE_COLLECTION_ID,
    "user_id": "test-user-id",
    "name": "Test Collection",
    "status": "active",
}

FAKE_EXAMPLES = [
    {
        "messages": [
            {"role": "system", "content": "Extract entities."},
            {"role": "user", "content": "OpenAI was founded in 2015."},
            {"role": "assistant", "content": '{"entities": [{"name": "OpenAI", "type": "Organization"}]}'},
        ]
    }
]

FAKE_JOB_STATUS = {
    "id": FAKE_JOB_ID,
    "status": "running",
    "model": "gpt-4o-mini-2024-07-18",
    "fine_tuned_model": None,
    "trained_tokens": 0,
}


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    _app = FastAPI()
    _app.include_router(router, prefix="/finetune")
    _app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    return _app


# ---------------------------------------------------------------------------
# POST /finetune/export
# ---------------------------------------------------------------------------

class TestExportDataset:
    async def test_returns_example_preview(self, app):
        with (
            patch("app.routers.finetune.get_collection", new_callable=AsyncMock, return_value=FAKE_COLLECTION),
            patch("app.services.finetune_service.build_training_dataset", new_callable=AsyncMock, return_value=FAKE_EXAMPLES),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/finetune/export",
                    json={"collection_id": FAKE_COLLECTION_ID, "max_examples": 100},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["collection_id"] == FAKE_COLLECTION_ID
        assert data["example_count"] == len(FAKE_EXAMPLES)
        assert data["total"] == len(FAKE_EXAMPLES)
        assert isinstance(data["examples"], list)

    async def test_collection_not_found_returns_404(self, app):
        with patch("app.routers.finetune.get_collection", new_callable=AsyncMock, return_value=None):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/finetune/export",
                    json={"collection_id": FAKE_COLLECTION_ID},
                )

        assert response.status_code == 404

    async def test_other_user_collection_returns_403(self, app):
        other = {**FAKE_COLLECTION, "user_id": "other-user"}
        with patch("app.routers.finetune.get_collection", new_callable=AsyncMock, return_value=other):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/finetune/export",
                    json={"collection_id": FAKE_COLLECTION_ID},
                )

        assert response.status_code == 403

    async def test_preview_capped_at_50(self, app):
        """examples field in response is capped at 50 entries."""
        many_examples = FAKE_EXAMPLES * 100  # 100 copies
        with (
            patch("app.routers.finetune.get_collection", new_callable=AsyncMock, return_value=FAKE_COLLECTION),
            patch("app.services.finetune_service.build_training_dataset", new_callable=AsyncMock, return_value=many_examples),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/finetune/export",
                    json={"collection_id": FAKE_COLLECTION_ID},
                )

        data = response.json()
        assert data["total"] == 100
        assert len(data["examples"]) <= 50

    async def test_empty_dataset_returns_zero(self, app):
        with (
            patch("app.routers.finetune.get_collection", new_callable=AsyncMock, return_value=FAKE_COLLECTION),
            patch("app.services.finetune_service.build_training_dataset", new_callable=AsyncMock, return_value=[]),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/finetune/export",
                    json={"collection_id": FAKE_COLLECTION_ID},
                )

        assert response.status_code == 200
        assert response.json()["example_count"] == 0


# ---------------------------------------------------------------------------
# POST /finetune/start
# ---------------------------------------------------------------------------

class TestStartFinetune:
    async def test_returns_job_result(self, app):
        job_result = {
            "job_id": FAKE_JOB_ID,
            "status": "queued",
            "model": "gpt-4o-mini-2024-07-18",
            "example_count": 42,
        }
        with (
            patch("app.routers.finetune.get_collection", new_callable=AsyncMock, return_value=FAKE_COLLECTION),
            patch("app.services.finetune_service.export_and_finetune", new_callable=AsyncMock, return_value=job_result),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/finetune/start",
                    json={"collection_id": FAKE_COLLECTION_ID},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == FAKE_JOB_ID
        assert data["status"] == "queued"

    async def test_collection_not_found_returns_404(self, app):
        with patch("app.routers.finetune.get_collection", new_callable=AsyncMock, return_value=None):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/finetune/start",
                    json={"collection_id": FAKE_COLLECTION_ID},
                )

        assert response.status_code == 404

    async def test_other_user_collection_returns_403(self, app):
        other = {**FAKE_COLLECTION, "user_id": "other-user"}
        with patch("app.routers.finetune.get_collection", new_callable=AsyncMock, return_value=other):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/finetune/start",
                    json={"collection_id": FAKE_COLLECTION_ID},
                )

        assert response.status_code == 403

    async def test_value_error_returns_400(self, app):
        """ValueError (e.g., no feedback data) maps to 400."""
        with (
            patch("app.routers.finetune.get_collection", new_callable=AsyncMock, return_value=FAKE_COLLECTION),
            patch(
                "app.services.finetune_service.export_and_finetune",
                new_callable=AsyncMock,
                side_effect=ValueError("No training examples available"),
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/finetune/start",
                    json={"collection_id": FAKE_COLLECTION_ID},
                )

        assert response.status_code == 400
        assert "No training examples" in response.json()["detail"]

    async def test_openai_error_returns_502(self, app):
        with (
            patch("app.routers.finetune.get_collection", new_callable=AsyncMock, return_value=FAKE_COLLECTION),
            patch(
                "app.services.finetune_service.export_and_finetune",
                new_callable=AsyncMock,
                side_effect=Exception("OpenAI timeout"),
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/finetune/start",
                    json={"collection_id": FAKE_COLLECTION_ID},
                )

        assert response.status_code == 502

    async def test_n_epochs_out_of_range_rejected(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/finetune/start",
                json={"collection_id": FAKE_COLLECTION_ID, "n_epochs": 100},
            )

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /finetune/jobs/{job_id}
# ---------------------------------------------------------------------------

class TestGetFinetuneStatus:
    async def test_returns_job_status(self, app):
        with patch(
            "app.services.finetune_service.get_finetune_job_status",
            new_callable=AsyncMock,
            return_value=FAKE_JOB_STATUS,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get(f"/finetune/jobs/{FAKE_JOB_ID}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == FAKE_JOB_ID
        assert data["status"] == "running"

    async def test_invalid_job_id_returns_400(self, app):
        with patch(
            "app.services.finetune_service.get_finetune_job_status",
            new_callable=AsyncMock,
            side_effect=ValueError("Invalid job ID"),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/finetune/jobs/bad-id")

        assert response.status_code == 400

    async def test_openai_error_returns_502(self, app):
        with patch(
            "app.services.finetune_service.get_finetune_job_status",
            new_callable=AsyncMock,
            side_effect=Exception("OpenAI error"),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get(f"/finetune/jobs/{FAKE_JOB_ID}")

        assert response.status_code == 502
