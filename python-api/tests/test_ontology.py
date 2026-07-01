"""Tests for ontology router — CRUD, generate, validate, versions."""

import json
import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.routers.ontology import router
from app.auth.middleware import get_current_user

FAKE_USER = {"id": "test-user-id", "email": "test@example.com", "name": "Test"}
COLLECTION_ID = str(uuid.uuid4())

FAKE_COLLECTION = {
    "id": COLLECTION_ID,
    "user_id": "test-user-id",
    "name": "Test Collection",
    "status": "active",
}


@pytest.fixture
def app():
    _app = FastAPI()
    _app.include_router(router, prefix="/ontology")
    _app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    return _app


class TestGetOntology:
    async def test_returns_default_when_no_ontology(self, app):
        with (
            patch("app.routers.ontology.get_collection", new_callable=AsyncMock,
                  return_value=FAKE_COLLECTION),
            patch("app.routers.ontology.get_ontology", new_callable=AsyncMock,
                  return_value=None),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get(f"/ontology?collection_id={COLLECTION_ID}")

        assert response.status_code == 200
        data = response.json()
        assert "entity_types" in data
        assert "Person" in data["entity_types"]
        assert "Organization" in data["entity_types"]

    async def test_returns_existing_ontology(self, app):
        fake_ontology = {
            "collection_id": COLLECTION_ID,
            "version": 3,
            "entity_types": json.dumps({"Person": {"description": "A human", "examples": []}}),
            "relationship_types": json.dumps({"WORKS_AT": {"domain": ["Person"], "range": ["Organization"], "description": "Works at"}}),
            "updated_at": 1234567890,
        }
        with (
            patch("app.routers.ontology.get_collection", new_callable=AsyncMock,
                  return_value=FAKE_COLLECTION),
            patch("app.routers.ontology.get_ontology", new_callable=AsyncMock,
                  return_value=fake_ontology),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get(f"/ontology?collection_id={COLLECTION_ID}")

        assert response.status_code == 200
        data = response.json()
        assert data["version"] == 3

    async def test_collection_not_found(self, app):
        with patch(
            "app.routers.ontology.get_collection", new_callable=AsyncMock, return_value=None
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get(f"/ontology?collection_id={COLLECTION_ID}")

        assert response.status_code == 404


class TestGenerateOntology:
    async def test_generate_returns_proposal_not_applied(self, app):
        mock_db = MagicMock()
        mock_tbl = MagicMock()
        mock_tbl.search.return_value.limit.return_value.to_list.return_value = []
        mock_db.open_table.return_value = mock_tbl

        with (
            patch("app.routers.ontology.get_collection", new_callable=AsyncMock,
                  return_value=FAKE_COLLECTION),
            patch("app.db.lancedb_client.get_lancedb", new_callable=AsyncMock,
                  return_value=mock_db),
            patch("app.routers.ontology.upsert_ontology", new_callable=AsyncMock) as mock_upsert,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    "/ontology/generate",
                    json={"collection_id": COLLECTION_ID},
                )

        assert response.status_code == 200
        data = response.json()
        assert "proposal" in data
        assert data["applied"] is False
        assert "Review the proposal" in data["message"]
        mock_upsert.assert_not_called()


class TestValidateOntology:
    async def test_validate_unknown_entity_types(self, app):
        with (
            patch("app.routers.ontology.get_collection", new_callable=AsyncMock,
                  return_value=FAKE_COLLECTION),
            patch("app.routers.ontology.get_ontology", new_callable=AsyncMock,
                  return_value={
                      "entity_types": json.dumps({"Person": {"description": "A human", "examples": []}}),
                      "relationship_types": json.dumps({"WORKS_AT": {"domain": ["Person"], "range": ["Organization"], "description": "Works at"}}),
                  }),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    f"/ontology/validate?collection_id={COLLECTION_ID}&entities=GADGET&entities=Person",
                )

        assert response.status_code == 200
        data = response.json()
        assert "warnings" in data
        assert any("GADGET" in w for w in data["warnings"])

    async def test_validate_unknown_relationship_type(self, app):
        with (
            patch("app.routers.ontology.get_collection", new_callable=AsyncMock,
                  return_value=FAKE_COLLECTION),
            patch("app.routers.ontology.get_ontology", new_callable=AsyncMock,
                  return_value={
                      "entity_types": json.dumps({"Person": {"description": "A human", "examples": []}}),
                      "relationship_types": json.dumps({}),
                  }),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    f"/ontology/validate?collection_id={COLLECTION_ID}&relationships=FLIES_TO",
                )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert any("FLIES_TO" in e for e in data["errors"])

    async def test_validate_no_ontology_returns_error(self, app):
        with (
            patch("app.routers.ontology.get_collection", new_callable=AsyncMock,
                  return_value=FAKE_COLLECTION),
            patch("app.routers.ontology.get_ontology", new_callable=AsyncMock,
                  return_value=None),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    f"/ontology/validate?collection_id={COLLECTION_ID}",
                )

        assert response.status_code == 200
        data = response.json()
        assert len(data["errors"]) > 0


class TestOntologyVersions:
    async def test_list_versions(self, app):
        with (
            patch("app.routers.ontology.get_collection", new_callable=AsyncMock,
                  return_value=FAKE_COLLECTION),
            patch("app.routers.ontology.list_ontology_versions", new_callable=AsyncMock,
                  return_value=[
                      {"version": 3, "updated_at": 3000},
                      {"version": 2, "updated_at": 2000},
                      {"version": 1, "updated_at": 1000},
                  ]),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get(
                    f"/ontology/versions?collection_id={COLLECTION_ID}",
                )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert len(data["versions"]) == 3