"""Mock-based tests for lancedb_client — covers CRUD, graph, ontology, token ops."""

import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_table(rows: list[dict] | None = None):
    """Return a MagicMock that behaves like a LanceDB table."""
    tbl = MagicMock()
    q = MagicMock()
    rows = rows or []
    q.to_list.return_value = rows
    q.where.return_value = q
    q.limit.return_value = q
    q.offset.return_value = q
    q.nearest_to.return_value = q
    tbl.query.return_value = q
    tbl.search.return_value = q
    tbl.add = MagicMock()
    tbl.delete = MagicMock()
    tbl.update = MagicMock()
    return tbl


def _mock_db(tables: dict[str, list[dict]] | None = None):
    """Return a MagicMock LanceDB connection with pre-populated tables."""
    tables = tables or {}
    db = MagicMock()

    def open_table(name):
        if name in tables:
            return _mock_table(tables[name])
        raise Exception(f"Table {name} not found")

    db.open_table.side_effect = open_table
    db.create_table.return_value = _mock_table()
    return db


# ---------------------------------------------------------------------------
# Fixture: patch get_lancedb
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db():
    db = _mock_db()
    with patch("app.db.lancedb_client._db", db):
        yield db


# ---------------------------------------------------------------------------
# get_collection
# ---------------------------------------------------------------------------

class TestGetCollection:
    async def test_returns_collection_when_found(self):
        cid = str(uuid.uuid4())
        row = {"id": cid, "user_id": "u1", "name": "Test"}
        db = _mock_db({"collections": [row]})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_collection
            result = await get_collection(cid)
        assert result == row

    async def test_returns_none_when_not_found(self):
        db = _mock_db({"collections": []})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_collection
            result = await get_collection("nonexistent")
        assert result is None

    async def test_returns_none_on_exception(self):
        db = _mock_db()  # no collections table
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_collection
            result = await get_collection("any")
        assert result is None


# ---------------------------------------------------------------------------
# get_user_by_google_sub
# ---------------------------------------------------------------------------

class TestGetUserByGoogleSub:
    async def test_returns_user_when_found(self):
        row = {"id": "u1", "google_sub": "sub123", "email": "a@b.com"}
        db = _mock_db({"users": [row]})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_user_by_google_sub
            result = await get_user_by_google_sub("sub123")
        assert result == row

    async def test_returns_none_when_missing(self):
        db = _mock_db({"users": []})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_user_by_google_sub
            result = await get_user_by_google_sub("nope")
        assert result is None


# ---------------------------------------------------------------------------
# get_user_by_id
# ---------------------------------------------------------------------------

class TestGetUserById:
    async def test_returns_user(self):
        row = {"id": "uid1", "email": "x@y.com"}
        db = _mock_db({"users": [row]})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_user_by_id
            assert await get_user_by_id("uid1") == row

    async def test_returns_none_on_error(self):
        db = _mock_db()
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_user_by_id
            assert await get_user_by_id("x") is None


# ---------------------------------------------------------------------------
# create_or_update_user
# ---------------------------------------------------------------------------

class TestCreateOrUpdateUser:
    async def test_creates_new_user(self):
        db = _mock_db({"users": []})  # open_table succeeds, no existing user
        tbl = _mock_table([])
        db.open_table.return_value = tbl

        user = {"id": "u1", "google_sub": "sub1", "email": "a@b.com",
                "name": "A", "avatar_url": None}
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import create_or_update_user
            uid = await create_or_update_user(user.copy())
        assert uid == "u1"

    async def test_updates_existing_user(self):
        existing = {"id": "u1", "google_sub": "sub1"}
        tbl = _mock_table([existing])
        db = MagicMock()
        db.open_table.return_value = tbl

        user = {"id": "u1", "google_sub": "sub1", "email": "new@b.com",
                "name": "B", "avatar_url": None}
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import create_or_update_user
            uid = await create_or_update_user(user.copy())
        assert uid == "u1"
        tbl.update.assert_called_once()


# ---------------------------------------------------------------------------
# create_collection
# ---------------------------------------------------------------------------

class TestCreateCollection:
    async def test_creates_and_returns_id(self):
        tbl = _mock_table()
        db = MagicMock()
        db.open_table.return_value = tbl

        cid = str(uuid.uuid4())
        data = {"id": cid, "user_id": "u1", "name": "My Collection",
                "description": "", "folder_path": "/tmp"}
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import create_collection
            result = await create_collection(data.copy())
        assert result == cid
        tbl.add.assert_called_once()


# ---------------------------------------------------------------------------
# list_collections
# ---------------------------------------------------------------------------

class TestListCollections:
    async def test_returns_user_collections(self):
        rows = [{"id": "c1", "user_id": "u1"}, {"id": "c2", "user_id": "u1"}]
        db = _mock_db({"collections": rows})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import list_collections
            result = await list_collections("u1")
        assert result == rows

    async def test_returns_empty_on_exception(self):
        db = _mock_db()  # no collections table
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import list_collections
            result = await list_collections("u1")
        assert result == []


# ---------------------------------------------------------------------------
# update_collection / delete_collection
# ---------------------------------------------------------------------------

class TestUpdateDeleteCollection:
    async def test_update_calls_table_update(self):
        tbl = _mock_table()
        db = MagicMock()
        db.open_table.return_value = tbl
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import update_collection
            await update_collection("c1", {"name": "New Name"})
        tbl.update.assert_called_once()

    async def test_update_no_exception_on_error(self):
        db = _mock_db()  # no table
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import update_collection
            await update_collection("c1", {"name": "x"})  # must not raise

    async def test_delete_calls_table_delete(self):
        tbl = _mock_table()
        db = MagicMock()
        db.open_table.return_value = tbl
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import delete_collection
            await delete_collection("c1")
        tbl.delete.assert_called_once()


# ---------------------------------------------------------------------------
# Ingest jobs
# ---------------------------------------------------------------------------

class TestIngestJobs:
    async def test_create_ingest_job_sets_status(self):
        tbl = _mock_table()
        db = MagicMock()
        db.open_table.return_value = tbl

        jid = str(uuid.uuid4())
        data = {"id": jid, "collection_id": "c1", "total_docs": 5, "error_msg": "", "options": "{}"}
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import create_ingest_job
            result = await create_ingest_job(data.copy())
        assert result == jid

    async def test_update_ingest_job(self):
        tbl = _mock_table()
        db = MagicMock()
        db.open_table.return_value = tbl
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import update_ingest_job
            await update_ingest_job("j1", {"status": "completed"})
        tbl.update.assert_called_once()

    async def test_get_ingest_job_returns_row(self):
        row = {"id": "j1", "status": "running"}
        db = _mock_db({"ingest_jobs": [row]})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_ingest_job
            result = await get_ingest_job("j1")
        assert result == row

    async def test_get_ingest_job_returns_none_when_missing(self):
        db = _mock_db({"ingest_jobs": []})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_ingest_job
            assert await get_ingest_job("nope") is None

    async def test_list_ingest_jobs_all(self):
        rows = [{"id": "j1"}, {"id": "j2"}]
        db = _mock_db({"ingest_jobs": rows})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import list_ingest_jobs
            result = await list_ingest_jobs()
        assert result == rows

    async def test_list_ingest_jobs_filtered(self):
        rows = [{"id": "j1", "collection_id": "c1"}]
        db = _mock_db({"ingest_jobs": rows})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import list_ingest_jobs
            result = await list_ingest_jobs(collection_id="c1")
        assert result == rows


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

class TestDocuments:
    async def test_upsert_document_new(self):
        db = _mock_db()
        created_tbl = _mock_table()
        db.create_table.return_value = created_tbl

        doc = {"id": "d1", "collection_id": "c1", "filename": "f.pdf",
               "path": "/tmp/f.pdf", "status": "ok", "metadata": "{}"}
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import upsert_document
            result = await upsert_document(doc.copy())
        assert result == "d1"

    async def test_get_document_returns_row(self):
        row = {"id": "d1", "collection_id": "c1"}
        db = _mock_db({"c1_documents": [row]})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_document
            result = await get_document("d1", "c1")
        assert result == row

    async def test_get_document_returns_none_when_missing(self):
        db = _mock_db({"c1_documents": []})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_document
            assert await get_document("x", "c1") is None

    async def test_list_documents(self):
        rows = [{"id": "d1"}, {"id": "d2"}]
        db = _mock_db({"c1_documents": rows})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import list_documents
            result = await list_documents("c1")
        assert result == rows

    async def test_list_documents_returns_empty_on_error(self):
        db = _mock_db()
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import list_documents
            assert await list_documents("nonexistent") == []

    async def test_delete_document(self):
        doc_tbl = _mock_table()
        chunk_tbl = _mock_table()
        db = MagicMock()

        def open_table(name):
            if name == "c1_documents":
                return doc_tbl
            if name == "c1_chunks":
                return chunk_tbl
            raise Exception("no table")
        db.open_table.side_effect = open_table

        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import delete_document
            await delete_document("d1", "c1")
        doc_tbl.delete.assert_called_once()
        chunk_tbl.delete.assert_called_once()

    async def test_get_document_by_drive_file_id(self):
        import json
        meta = json.dumps({"drive_file_id": "gfile123"})
        row = {"id": "d1", "metadata": meta}
        db = _mock_db({"c1_documents": [row]})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_document_by_drive_file_id
            result = await get_document_by_drive_file_id("gfile123", "c1")
        assert result == row

    async def test_get_document_by_drive_file_id_not_found(self):
        import json
        row = {"id": "d1", "metadata": json.dumps({"drive_file_id": "other"})}
        db = _mock_db({"c1_documents": [row]})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_document_by_drive_file_id
            assert await get_document_by_drive_file_id("gfile123", "c1") is None


# ---------------------------------------------------------------------------
# Graph nodes / edges
# ---------------------------------------------------------------------------

class TestGraphNodes:
    async def test_list_graph_nodes_returns_rows(self):
        rows = [{"id": "n1", "label": "Person"}]
        db = _mock_db({"c1_nodes": rows})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import list_graph_nodes
            result = await list_graph_nodes("c1")
        assert result == rows

    async def test_list_graph_nodes_empty_on_error(self):
        db = _mock_db()
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import list_graph_nodes
            assert await list_graph_nodes("c1") == []

    async def test_get_graph_node_found(self):
        row = {"id": "n1", "label": "Org"}
        db = _mock_db({"c1_nodes": [row]})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_graph_node
            result = await get_graph_node("c1", "n1")
        assert result == row

    async def test_get_graph_node_not_found(self):
        db = _mock_db({"c1_nodes": []})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_graph_node
            assert await get_graph_node("c1", "n1") is None

    async def test_update_graph_node_returns_merged(self):
        existing = {"id": "n1", "label": "Org", "collection_id": "c1"}
        tbl = _mock_table([existing])
        db = MagicMock()
        db.open_table.return_value = tbl

        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import update_graph_node
            merged = await update_graph_node("c1", "n1", {"label": "Person"})
        assert merged["label"] == "Person"

    async def test_update_graph_node_not_found_returns_none(self):
        db = _mock_db({"c1_nodes": []})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import update_graph_node
            assert await update_graph_node("c1", "n1", {"label": "x"}) is None


class TestGraphEdges:
    async def test_list_graph_edges_returns_rows(self):
        rows = [{"id": "e1", "source_id": "n1", "target_id": "n2"}]
        db = _mock_db({"c1_edges": rows})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import list_graph_edges
            result = await list_graph_edges("c1")
        assert result == rows

    async def test_list_graph_edges_empty_on_error(self):
        db = _mock_db()
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import list_graph_edges
            assert await list_graph_edges("c1") == []

    async def test_get_graph_edge_found(self):
        row = {"id": "e1"}
        db = _mock_db({"c1_edges": [row]})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_graph_edge
            assert await get_graph_edge("c1", "e1") == row

    async def test_get_graph_edge_not_found(self):
        db = _mock_db({"c1_edges": []})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_graph_edge
            assert await get_graph_edge("c1", "e1") is None

    async def test_delete_graph_edge(self):
        tbl = _mock_table()
        db = MagicMock()
        db.open_table.return_value = tbl
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import delete_graph_edge
            await delete_graph_edge("c1", "e1")
        tbl.delete.assert_called_once()


# ---------------------------------------------------------------------------
# Ontology
# ---------------------------------------------------------------------------

class TestOntology:
    async def test_get_ontology_found(self):
        row = {"collection_id": "c1", "entity_types": "[]", "relation_types": "[]"}
        db = _mock_db({"ontologies": [row]})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_ontology
            assert await get_ontology("c1") == row

    async def test_get_ontology_not_found(self):
        db = _mock_db({"ontologies": []})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_ontology
            assert await get_ontology("c1") is None

    async def test_upsert_ontology(self):
        tbl = _mock_table()
        db = MagicMock()
        db.open_table.return_value = tbl
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import upsert_ontology
            await upsert_ontology({"collection_id": "c1", "entity_types": "[]", "relation_types": "[]"})
        tbl.delete.assert_called_once()
        tbl.add.assert_called_once()


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------

class TestTopics:
    async def test_list_topics_returns_rows(self):
        rows = [{"id": "t1", "label": "Finance"}]
        db = _mock_db({"c1_topics": rows})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import list_topics
            assert await list_topics("c1") == rows

    async def test_list_topics_empty_on_error(self):
        db = _mock_db()
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import list_topics
            assert await list_topics("c1") == []

    async def test_upsert_topic(self):
        tbl = _mock_table()
        db = MagicMock()
        db.open_table.return_value = tbl
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import upsert_topic
            await upsert_topic("c1", {"id": "t1", "label": "Tax"})
        tbl.add.assert_called_once()


# ---------------------------------------------------------------------------
# Token revocation
# ---------------------------------------------------------------------------

class TestTokenRevocation:
    async def test_revoke_token_db_adds_record(self):
        tbl = _mock_table()
        db = MagicMock()
        db.open_table.return_value = tbl
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import revoke_token_db
            await revoke_token_db("jti-123", 9999999999)
        tbl.add.assert_called_once()

    async def test_is_token_revoked_true(self):
        tbl = _mock_table([{"jti": "jti-123"}])
        db = MagicMock()
        db.open_table.return_value = tbl
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import is_token_revoked
            assert await is_token_revoked("jti-123") is True

    async def test_is_token_revoked_false(self):
        tbl = _mock_table([])
        db = MagicMock()
        db.open_table.return_value = tbl
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import is_token_revoked
            assert await is_token_revoked("not-there") is False

    async def test_is_token_revoked_returns_false_on_exception(self):
        db = _mock_db()
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import is_token_revoked
            assert await is_token_revoked("x") is False

    async def test_purge_expired_revocations_success(self):
        tbl = _mock_table()
        db = MagicMock()
        db.open_table.return_value = tbl
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import purge_expired_revocations
            result = await purge_expired_revocations()
        assert result == 1
        tbl.delete.assert_called_once()

    async def test_purge_expired_revocations_returns_zero_on_error(self):
        db = _mock_db()
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import purge_expired_revocations
            assert await purge_expired_revocations() == 0


# ---------------------------------------------------------------------------
# Vector search
# ---------------------------------------------------------------------------

class TestVectorSearch:
    async def test_returns_normalised_scores(self):
        rows = [{"id": "c1", "text": "hello", "_distance": 0.2}]
        tbl = _mock_table(rows)
        db = MagicMock()
        db.open_table.return_value = tbl
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import vector_search
            results = await vector_search("col1", [0.1, 0.2], limit=5)
        assert len(results) == 1
        assert results[0]["vector_score"] == pytest.approx(0.8)

    async def test_returns_empty_on_exception(self):
        db = _mock_db()
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import vector_search
            assert await vector_search("c1", [0.1]) == []


# ---------------------------------------------------------------------------
# get_lancedb — connection caching
# ---------------------------------------------------------------------------

class TestGetLancedb:
    async def test_reuses_existing_connection(self):
        db = _mock_db()
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_lancedb
            result = await get_lancedb()
        assert result is db

    async def test_creates_new_connection_when_none(self):
        db = _mock_db()
        with (
            patch("app.db.lancedb_client._db", None),
            patch("lancedb.connect", return_value=db) as mock_connect,
        ):
            import app.db.lancedb_client as ldc
            ldc._db = None
            from app.db.lancedb_client import get_lancedb
            result = await get_lancedb()
        assert result is db


# ---------------------------------------------------------------------------
# upsert_to_table
# ---------------------------------------------------------------------------

class TestUpsertToTable:
    async def test_returns_zero_for_empty_records(self):
        db = _mock_db()
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import upsert_to_table
            result = await upsert_to_table("some_table", [])
        assert result == 0

    async def test_creates_table_when_not_exists(self):
        db = _mock_db()  # open_table raises
        created_tbl = _mock_table()
        db.create_table.return_value = created_tbl

        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import upsert_to_table
            result = await upsert_to_table("new_table", [{"id": "r1", "val": 1}])
        assert result == 1
        db.create_table.assert_called_once()


# ---------------------------------------------------------------------------
# Drive watch channels
# ---------------------------------------------------------------------------

class TestDriveChannels:
    async def test_upsert_drive_channel(self):
        tbl = _mock_table()
        db = MagicMock()
        db.open_table.return_value = tbl
        channel = {"channel_id": "ch1", "resource_id": "r1", "collection_id": "c1",
                   "folder_id": "f1", "access_token": "tok", "expiry_ms": 9999}
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import upsert_drive_channel
            await upsert_drive_channel(channel.copy())

    async def test_get_drive_channel_found(self):
        row = {"channel_id": "ch1"}
        db = _mock_db({"drive_watch_channels": [row]})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_drive_channel
            assert await get_drive_channel("ch1") == row

    async def test_get_drive_channel_not_found(self):
        db = _mock_db({"drive_watch_channels": []})
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import get_drive_channel
            assert await get_drive_channel("ch1") is None

    async def test_delete_drive_channel(self):
        tbl = _mock_table()
        db = MagicMock()
        db.open_table.return_value = tbl
        with patch("app.db.lancedb_client._db", db):
            from app.db.lancedb_client import delete_drive_channel
            await delete_drive_channel("ch1")
        tbl.delete.assert_called_once()
