"""Tests for template API endpoints — list, get, validate."""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.routers.templates import router
from app.auth.middleware import get_current_user, require_admin
from app.services.template_gallery import TemplateGallery

FAKE_USER = {"id": "test-user-id", "email": "test@example.com", "name": "Test User"}
FAKE_ADMIN = {"id": "admin-id", "email": "admin@example.com", "name": "Admin", "role": "admin"}


@pytest.fixture
def app():
    _app = FastAPI()
    _app.include_router(router, prefix="/templates")
    _app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    _app.dependency_overrides[require_admin] = lambda: FAKE_ADMIN
    return _app


@pytest.fixture
def gallery_with_templates():
    TemplateGallery.reset()
    gallery = TemplateGallery()
    TemplateGallery._instance = gallery
    yield gallery
    TemplateGallery.reset()


class TestListTemplates:
    @pytest.mark.asyncio
    async def test_list_templates_returns_200(self, app, gallery_with_templates):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/templates")
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_list_template_fields(self, app, gallery_with_templates):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/templates")
            data = resp.json()
            if data:
                assert "key" in data[0]
                assert "name" in data[0]
                assert "domain" in data[0]
                assert "type" in data[0]

    @pytest.mark.asyncio
    async def test_list_filter_by_domain(self, app, gallery_with_templates):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/templates?domain=general")
            assert resp.status_code == 200
            data = resp.json()
            for t in data:
                assert t["domain"] == "general"

    @pytest.mark.asyncio
    async def test_list_filter_by_type(self, app, gallery_with_templates):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/templates?type_filter=graph")
            assert resp.status_code == 200
            data = resp.json()
            for t in data:
                assert t["type"] == "graph"


class TestGetTemplate:
    @pytest.mark.asyncio
    async def test_get_existing_template(self, app, gallery_with_templates):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/templates/general/graph")
            assert resp.status_code == 200
            data = resp.json()
            assert data["name"] == "graph"
            assert data["domain"] == "general"

    @pytest.mark.asyncio
    async def test_get_template_no_prompts(self, app, gallery_with_templates):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/templates/general/graph")
            data = resp.json()
            assert "node_prompt" not in data
            assert "edge_prompt" not in data
            assert "node_prompt_extra" not in data
            assert "edge_prompt_extra" not in data

    @pytest.mark.asyncio
    async def test_get_template_includes_schemas(self, app, gallery_with_templates):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/templates/general/graph")
            data = resp.json()
            assert "entity_schema" in data
            assert data["entity_schema"] is not None
            assert "fields" in data["entity_schema"]
            assert "key" in data["entity_schema"]

    @pytest.mark.asyncio
    async def test_get_nonexistent_template_404(self, app, gallery_with_templates):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/templates/nonexistent/name")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_hypergraph_template(self, app, gallery_with_templates):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/templates/general/hypergraph")
            assert resp.status_code == 200
            data = resp.json()
            assert data["name"] == "hypergraph"
            assert data["relation_schema"]["participants_field"] == "participants"


class TestValidateTemplate:
    @pytest.mark.asyncio
    async def test_validate_graph_without_entity_schema(self, app, gallery_with_templates):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/templates/validate",
                json={"name": "test", "type": "graph", "domain": "test"},
            )
            assert resp.status_code == 200
            result = resp.json()
            assert result["valid"] is False

    @pytest.mark.asyncio
    async def test_validate_valid_template(self, app, gallery_with_templates):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/templates/validate",
                json={
                    "name": "test_valid",
                    "type": "model",
                },
            )
            assert resp.status_code == 200
            result = resp.json()
            assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_validate_warns_on_unimplemented_method(self, app, gallery_with_templates):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/templates/validate",
                json={
                    "name": "test_warn",
                    "type": "model",
                    "extraction": {"method": "graph_rag"},
                },
            )
            assert resp.status_code == 200
            result = resp.json()
            assert result["valid"] is True
            assert "warnings" in result
            assert any("not yet implemented" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_non_admin_cannot_validate(self):
        from fastapi import HTTPException
        _app = FastAPI()
        _app.include_router(router, prefix="/templates")
        _app.dependency_overrides[get_current_user] = lambda: FAKE_USER
        _app.dependency_overrides[require_admin] = lambda: (_ for _ in ()).throw(
            HTTPException(status_code=403, detail="Admin required")
        )
        async with AsyncClient(
            transport=ASGITransport(app=_app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/templates/validate",
                json={"name": "test", "type": "model"},
            )
            assert resp.status_code == 403


class TestExtractionMethods:
    @pytest.mark.asyncio
    async def test_list_extraction_methods(self, app, gallery_with_templates):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/templates/extraction-methods")
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)
            assert len(data) >= 2

    @pytest.mark.asyncio
    async def test_extraction_methods_have_required_fields(self, app, gallery_with_templates):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/templates/extraction-methods")
            data = resp.json()
            for m in data:
                assert "name" in m
                assert "auto_type" in m
                assert "description" in m
                assert "implemented" in m

    @pytest.mark.asyncio
    async def test_extraction_methods_default_implemented_only(self, app, gallery_with_templates):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/templates/extraction-methods")
            data = resp.json()
            for m in data:
                assert m["implemented"] is True

    @pytest.mark.asyncio
    async def test_extraction_methods_include_unimplemented(self, app, gallery_with_templates):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/templates/extraction-methods?implemented_only=false")
            data = resp.json()
            names = {m["name"] for m in data}
            assert "graph_rag" in names
            assert "light_rag" in names