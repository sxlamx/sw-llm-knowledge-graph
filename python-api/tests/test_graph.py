"""Tests for the graph router — node/edge CRUD, path finding, export."""

import json
import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.routers.graph import router
from app.auth.middleware import get_current_user

FAKE_USER = {"id": "test-user-id", "email": "test@example.com", "name": "Test"}
COLLECTION_ID = str(uuid.uuid4())

FAKE_COLLECTION = {
    "id": COLLECTION_ID,
    "user_id": "test-user-id",
    "name": "Test Collection",
    "status": "active",
}

NODE_ID_1 = str(uuid.uuid4())
NODE_ID_2 = str(uuid.uuid4())
EDGE_ID_1 = str(uuid.uuid4())

FAKE_NODE_1 = {
    "id": NODE_ID_1,
    "collection_id": COLLECTION_ID,
    "label": "Alice",
    "entity_type": "Person",
    "description": "A researcher",
    "confidence": 0.9,
    "properties": {},
    "source_chunk_ids": [],
    "topics": ["AI"],
}

FAKE_NODE_2 = {
    "id": NODE_ID_2,
    "collection_id": COLLECTION_ID,
    "label": "Acme Corp",
    "entity_type": "Organization",
    "description": "A company",
    "confidence": 0.85,
    "properties": {},
    "source_chunk_ids": [],
    "topics": [],
}

FAKE_EDGE = {
    "id": EDGE_ID_1,
    "collection_id": COLLECTION_ID,
    "source": NODE_ID_1,
    "source_id": NODE_ID_1,
    "target": NODE_ID_2,
    "target_id": NODE_ID_2,
    "relation_type": "works_at",
    "weight": 0.8,
    "properties": {},
}

# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    _app = FastAPI()
    _app.include_router(router, prefix="/graph")
    _app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    return _app


# ---------------------------------------------------------------------------
# GET /graph/subgraph
# ---------------------------------------------------------------------------

class TestGetSubgraph:
    async def test_returns_graph_data_from_lancedb(self, app):
        with (
            patch("app.routers.graph.get_collection", new_callable=AsyncMock,
                  return_value=FAKE_COLLECTION),
            patch("app.routers.graph.get_index_manager", return_value=None),
            patch("app.routers.graph.list_graph_nodes", new_callable=AsyncMock,
                  return_value=[FAKE_NODE_1, FAKE_NODE_2]),
            patch("app.routers.graph.list_graph_edges", new_callable=AsyncMock,
                  return_value=[FAKE_EDGE]),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get(
                    f"/graph/subgraph?collection_id={COLLECTION_ID}"
                )

        assert response.status_code == 200
        data = response.json()
        assert data["total_nodes"] == 2
        assert data["total_edges"] == 1
        labels = [n["label"] for n in data["nodes"]]
        assert "Alice" in labels
        assert "Acme Corp" in labels

    async def test_collection_not_found_returns_404(self, app):
        with patch("app.routers.graph.get_collection", new_callable=AsyncMock, return_value=None):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get(f"/graph/subgraph?collection_id={COLLECTION_ID}")

        assert response.status_code == 404

    async def test_other_user_collection_returns_403(self, app):
        with patch(
            "app.routers.graph.get_collection",
            new_callable=AsyncMock,
            return_value={**FAKE_COLLECTION, "user_id": "other-user"},
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get(f"/graph/subgraph?collection_id={COLLECTION_ID}")

        assert response.status_code == 403

    async def test_empty_graph_returns_empty_lists(self, app):
        with (
            patch("app.routers.graph.get_collection", new_callable=AsyncMock,
                  return_value=FAKE_COLLECTION),
            patch("app.routers.graph.get_index_manager", return_value=None),
            patch("app.routers.graph.list_graph_nodes", new_callable=AsyncMock,
                  return_value=[]),
            patch("app.routers.graph.list_graph_edges", new_callable=AsyncMock,
                  return_value=[]),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get(f"/graph/subgraph?collection_id={COLLECTION_ID}")

        assert response.status_code == 200
        data = response.json()
        assert data["nodes"] == []
        assert data["edges"] == []


# ---------------------------------------------------------------------------
# GET /graph/nodes/{node_id}
# ---------------------------------------------------------------------------

class TestGetNodeDetail:
    async def test_returns_node_detail(self, app):
        with (
            patch("app.routers.graph.get_collection", new_callable=AsyncMock,
                  return_value=FAKE_COLLECTION),
            patch("app.routers.graph.get_graph_node", new_callable=AsyncMock,
                  return_value=FAKE_NODE_1),
            patch("app.routers.graph.list_graph_edges", new_callable=AsyncMock,
                  return_value=[FAKE_EDGE]),
            patch("app.routers.graph.get_graph_node", new_callable=AsyncMock,
                  side_effect=lambda cid, nid: FAKE_NODE_1 if nid == NODE_ID_1 else FAKE_NODE_2),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get(
                    f"/graph/nodes/{NODE_ID_1}?collection_id={COLLECTION_ID}"
                )

        assert response.status_code == 200
        data = response.json()
        assert data["label"] == "Alice"
        assert "neighbors" in data
        assert "linked_chunks" in data

    async def test_node_not_found_returns_404(self, app):
        with (
            patch("app.routers.graph.get_collection", new_callable=AsyncMock,
                  return_value=FAKE_COLLECTION),
            patch("app.routers.graph.get_graph_node", new_callable=AsyncMock,
                  return_value=None),
            patch("app.routers.graph.list_graph_edges", new_callable=AsyncMock,
                  return_value=[]),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get(
                    f"/graph/nodes/{NODE_ID_1}?collection_id={COLLECTION_ID}"
                )

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /graph/path
# ---------------------------------------------------------------------------

class TestGetPath:
    async def test_finds_path_between_nodes(self, app):
        with (
            patch("app.routers.graph.get_collection", new_callable=AsyncMock,
                  return_value=FAKE_COLLECTION),
            patch("app.routers.graph.get_index_manager", return_value=None),
            patch("app.routers.graph.list_graph_nodes", new_callable=AsyncMock,
                  return_value=[FAKE_NODE_1, FAKE_NODE_2]),
            patch("app.routers.graph.list_graph_edges", new_callable=AsyncMock,
                  return_value=[FAKE_EDGE]),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get(
                    f"/graph/path?start_id={NODE_ID_1}&end_id={NODE_ID_2}"
                    f"&collection_id={COLLECTION_ID}"
                )

        assert response.status_code == 200
        data = response.json()
        assert NODE_ID_1 in data["path"]
        assert NODE_ID_2 in data["path"]

    async def test_no_path_returns_404(self, app):
        with (
            patch("app.routers.graph.get_collection", new_callable=AsyncMock,
                  return_value=FAKE_COLLECTION),
            patch("app.routers.graph.get_index_manager", return_value=None),
            patch("app.routers.graph.list_graph_nodes", new_callable=AsyncMock,
                  return_value=[FAKE_NODE_1, FAKE_NODE_2]),
            patch("app.routers.graph.list_graph_edges", new_callable=AsyncMock,
                  return_value=[]),  # no edges → no path
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get(
                    f"/graph/path?start_id={NODE_ID_1}&end_id={NODE_ID_2}"
                    f"&collection_id={COLLECTION_ID}"
                )

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# PUT /graph/nodes/{node_id}
# ---------------------------------------------------------------------------

class TestUpdateNode:
    async def test_update_node_returns_updated_data(self, app):
        updated = {**FAKE_NODE_1, "label": "Alice Updated"}
        with (
            patch("app.routers.graph.get_collection", new_callable=AsyncMock,
                  return_value=FAKE_COLLECTION),
            patch("app.routers.graph.get_graph_node", new_callable=AsyncMock,
                  return_value=FAKE_NODE_1),
            patch("app.routers.graph.update_graph_node", new_callable=AsyncMock,
                  return_value=updated),
            patch("app.routers.graph.insert_user_feedback", new_callable=AsyncMock),
            patch("app.routers.graph.get_index_manager", return_value=None),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.put(
                    f"/graph/nodes/{NODE_ID_1}?collection_id={COLLECTION_ID}",
                    json={"label": "Alice Updated"},
                )

        assert response.status_code == 200
        assert response.json()["label"] == "Alice Updated"

    async def test_update_nonexistent_node_returns_404(self, app):
        with (
            patch("app.routers.graph.get_collection", new_callable=AsyncMock,
                  return_value=FAKE_COLLECTION),
            patch("app.routers.graph.get_graph_node", new_callable=AsyncMock,
                  return_value=None),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.put(
                    f"/graph/nodes/{NODE_ID_1}?collection_id={COLLECTION_ID}",
                    json={"label": "X"},
                )

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /graph/edges
# ---------------------------------------------------------------------------

class TestCreateEdge:
    async def test_creates_edge_returns_201(self, app):
        with (
            patch("app.routers.graph.get_collection", new_callable=AsyncMock,
                  return_value=FAKE_COLLECTION),
            patch("app.routers.graph.upsert_graph_edge", new_callable=AsyncMock),
            patch("app.routers.graph.get_index_manager", return_value=None),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    "/graph/edges",
                    json={
                        "collection_id": COLLECTION_ID,
                        "source": NODE_ID_1,
                        "target": NODE_ID_2,
                        "relation_type": "works_at",
                        "weight": 0.9,
                    },
                )

        assert response.status_code == 201
        data = response.json()
        assert data["source"] == NODE_ID_1
        assert data["target"] == NODE_ID_2
        assert data["relation_type"] == "works_at"


# ---------------------------------------------------------------------------
# DELETE /graph/edges/{edge_id}
# ---------------------------------------------------------------------------

class TestDeleteEdge:
    async def test_delete_edge_returns_204(self, app):
        with (
            patch("app.routers.graph.get_collection", new_callable=AsyncMock,
                  return_value=FAKE_COLLECTION),
            patch("app.routers.graph.get_graph_edge", new_callable=AsyncMock,
                  return_value=FAKE_EDGE),
            patch("app.routers.graph.delete_graph_edge", new_callable=AsyncMock),
            patch("app.routers.graph.insert_user_feedback", new_callable=AsyncMock),
            patch("app.routers.graph.get_index_manager", return_value=None),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.delete(
                    f"/graph/edges/{EDGE_ID_1}?collection_id={COLLECTION_ID}"
                )

        assert response.status_code == 204

    async def test_delete_nonexistent_edge_returns_404(self, app):
        with (
            patch("app.routers.graph.get_collection", new_callable=AsyncMock,
                  return_value=FAKE_COLLECTION),
            patch("app.routers.graph.get_graph_edge", new_callable=AsyncMock,
                  return_value=None),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.delete(
                    f"/graph/edges/{EDGE_ID_1}?collection_id={COLLECTION_ID}"
                )

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /graph/export
# ---------------------------------------------------------------------------

class TestExportGraph:
    async def test_export_json_format(self, app):
        with (
            patch("app.routers.graph.get_collection", new_callable=AsyncMock,
                  return_value=FAKE_COLLECTION),
            patch("app.routers.graph.get_index_manager", return_value=None),
            patch("app.routers.graph.list_graph_nodes", new_callable=AsyncMock,
                  return_value=[FAKE_NODE_1]),
            patch("app.routers.graph.list_graph_edges", new_callable=AsyncMock,
                  return_value=[]),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.get(
                    f"/graph/export?collection_id={COLLECTION_ID}&format=json"
                )

        assert response.status_code == 200
        data = json.loads(response.text)
        assert "nodes" in data
        assert len(data["nodes"]) == 1

    async def test_invalid_format_returns_422(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get(
                f"/graph/export?collection_id={COLLECTION_ID}&format=csv"
            )

        assert response.status_code == 422
