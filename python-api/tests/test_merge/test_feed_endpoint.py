import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.routers.ingest import router
from app.auth.middleware import get_current_user


FAKE_USER = {"id": "test-user-id", "email": "test@example.com", "name": "Test User"}


@pytest.fixture
def app():
    _app = FastAPI()
    _app.include_router(router, prefix="/api/v1/ingest")
    _app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    return _app


class TestFeedEndpoint:
    @pytest.mark.asyncio
    async def test_feed_creates_job(self, app):
        with (
            patch(
                "app.routers.ingest.get_collection",
                new_callable=AsyncMock,
                return_value={"id": "col-1", "user_id": FAKE_USER["id"]},
            ),
            patch("app.routers.ingest.create_ingest_job", new_callable=AsyncMock),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post(
                    "/api/v1/ingest/collections/col-1/feed",
                    json={"file_paths": ["/path/to/new/doc.pdf"]},
                )
        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "pending"

    @pytest.mark.asyncio
    async def test_feed_with_template(self, app):
        mock_template = MagicMock()
        mock_template.extraction.merge_strategy_nodes = "keep_first"
        mock_template.extraction.merge_strategy_edges = "keep_first"

        mock_gallery_instance = MagicMock()
        mock_gallery_instance.get.return_value = mock_template

        with (
            patch(
                "app.routers.ingest.get_collection",
                new_callable=AsyncMock,
                return_value={"id": "col-1", "user_id": FAKE_USER["id"]},
            ),
            patch("app.routers.ingest.create_ingest_job", new_callable=AsyncMock),
            patch(
                "app.routers.ingest.TemplateGallery.get_instance",
                return_value=mock_gallery_instance,
            ),
            patch(
                "app.pipeline.ingest_worker.TemplateGallery.get_instance",
                return_value=mock_gallery_instance,
            ),
            patch(
                "app.core.rust_bridge.get_ingestion_engine",
                return_value=None,
            ),
            patch(
                "app.core.rust_bridge.get_index_manager",
                return_value=None,
            ),
            patch(
                "app.pipeline.ingest_worker.update_ingest_job",
                new_callable=AsyncMock,
            ),
            patch(
                "app.pipeline.ingest_worker.update_collection",
                new_callable=AsyncMock,
            ),
            patch(
                "app.pipeline.ingest_worker.get_job_manager",
                return_value=MagicMock(
                    emit=MagicMock(),
                    is_cancelled=AsyncMock(return_value=False),
                ),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post(
                    "/api/v1/ingest/collections/col-1/feed",
                    json={
                        "file_paths": ["/path/to/new/doc.pdf"],
                        "template": "general/graph",
                    },
                )
        assert resp.status_code == 202

    @pytest.mark.asyncio
    async def test_feed_requires_auth(self, app):
        del app.dependency_overrides[get_current_user]
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/api/v1/ingest/collections/col-1/feed",
                json={"file_paths": ["/path/to/new/doc.pdf"]},
            )
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_feed_nonexistent_collection(self, app):
        with patch(
            "app.routers.ingest.get_collection",
            new_callable=AsyncMock,
            return_value=None,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post(
                    f"/api/v1/ingest/collections/{uuid.uuid4()}/feed",
                    json={"file_paths": ["/path/to/new/doc.pdf"]},
                )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_feed_other_user_collection_returns_403(self, app):
        with patch(
            "app.routers.ingest.get_collection",
            new_callable=AsyncMock,
            return_value={"id": "col-1", "user_id": "other-user"},
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post(
                    "/api/v1/ingest/collections/col-1/feed",
                    json={"file_paths": ["/path/to/new/doc.pdf"]},
                )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_feed_empty_file_paths_rejected(self, app):
        with (
            patch(
                "app.routers.ingest.get_collection",
                new_callable=AsyncMock,
                return_value={"id": "col-1", "user_id": FAKE_USER["id"]},
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post(
                    "/api/v1/ingest/collections/col-1/feed",
                    json={"file_paths": []},
                )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_feed_bad_template_returns_400(self, app):
        mock_gallery_instance = MagicMock()
        mock_gallery_instance.get.return_value = None

        with (
            patch(
                "app.routers.ingest.get_collection",
                new_callable=AsyncMock,
                return_value={"id": "col-1", "user_id": FAKE_USER["id"]},
            ),
            patch(
                "app.routers.ingest.TemplateGallery.get_instance",
                return_value=mock_gallery_instance,
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post(
                    "/api/v1/ingest/collections/col-1/feed",
                    json={
                        "file_paths": ["/path/to/doc.pdf"],
                        "template": "nonexistent/template",
                    },
                )
        assert resp.status_code == 400