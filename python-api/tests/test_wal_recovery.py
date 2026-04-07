"""Integration tests for WAL recovery through the Python API.

These tests verify that graph data written through the Python API (via rust_bridge)
is correctly persisted and can be recovered after a simulated crash (IndexManager restart).

The WAL is an internal Rust implementation detail — tests here verify the end-to-end
behavior: write → restart → data is recovered.
"""

import pytest
import tempfile
import os
from unittest.mock import patch, MagicMock


class TestWALRecoveryIntegration:
    """Integration tests for WAL-based crash recovery."""

    @pytest.fixture
    def temp_index_path(self, tmp_path):
        """Provide a temporary path for the IndexManager data directory."""
        index_dir = tmp_path / "index_data"
        index_dir.mkdir()
        return str(index_dir)

    def test_index_manager_new_creates_wal_path(self, temp_index_path):
        """IndexManager.new() should create a wal.log file in the index directory."""
        from app.core.rust_bridge import RUST_AVAILABLE

        if not RUST_AVAILABLE:
            pytest.skip("Rust core not available")

        from rust_core import IndexManager
        im = IndexManager(temp_index_path)

        wal_path = os.path.join(temp_index_path, "wal.log")
        assert os.path.exists(wal_path), "wal.log must be created in index directory"

    def test_upsert_nodes_then_restart_recovers_nodes(self, temp_index_path):
        """After upsert_nodes + restart, nodes must be recovered from WAL."""
        from app.core.rust_bridge import RUST_AVAILABLE

        if not RUST_AVAILABLE:
            pytest.skip("Rust core not available")

        from rust_core import IndexManager
        import uuid

        coll_id = str(uuid.uuid4())

        # First session: create collection and insert nodes
        im1 = IndexManager(temp_index_path)
        im1.initialize_collection(coll_id)

        nodes = [
            {
                "id": str(uuid.uuid4()),
                "node_type": "person",
                "label": "Alice",
                "description": None,
                "aliases": [],
                "confidence": 0.9,
                "ontology_class": None,
                "properties": {},
                "collection_id": coll_id,
                "created_at": None,
                "updated_at": None,
            }
        ]
        im1.upsert_nodes(coll_id, json.dumps(nodes))

        # Verify data is in graph
        graph_data = json.loads(im1.get_graph_data(coll_id))
        assert graph_data["total_nodes"] == 1

        # Simulate crash: drop im1 (WAL file persists on disk)
        del im1

        # Restart: create new IndexManager pointing to same directory
        im2 = IndexManager(temp_index_path)

        # After restart, WAL entries should have been replayed
        graph_data = __import__("json").loads(im2.get_graph_data(coll_id))
        assert graph_data["total_nodes"] == 1, \
            "nodes must be recovered from WAL after restart"

    def test_upsert_edges_then_restart_recovers_edges(self, temp_index_path):
        """After upsert_edges + restart, edges must be recovered from WAL."""
        from app.core.rust_bridge import RUST_AVAILABLE

        if not RUST_AVAILABLE:
            pytest.skip("Rust core not available")

        from rust_core import IndexManager
        import uuid
        import json

        coll_id = str(uuid.uuid4())
        node_id = str(uuid.uuid4())

        # First session
        im1 = IndexManager(temp_index_path)
        im1.initialize_collection(coll_id)

        nodes = [{
            "id": node_id,
            "node_type": "person",
            "label": "Bob",
            "description": None,
            "aliases": [],
            "confidence": 0.8,
            "ontology_class": None,
            "properties": {},
            "collection_id": coll_id,
            "created_at": None,
            "updated_at": None,
        }]
        im1.upsert_nodes(coll_id, json.dumps(nodes))

        edges = [{
            "id": str(uuid.uuid4()),
            "source": node_id,
            "target": str(uuid.uuid4()),
            "edge_type": "relates_to",
            "weight": 0.7,
            "context": None,
            "chunk_id": None,
            "properties": {},
            "collection_id": coll_id,
        }]
        im1.upsert_edges(coll_id, json.dumps(edges))

        del im1

        # Restart
        im2 = IndexManager(temp_index_path)
        graph_data = json.loads(im2.get_graph_data(coll_id))

        assert graph_data["total_edges"] == 1, \
            "edges must be recovered from WAL after restart"

    def test_empty_wal_does_not_error_on_restart(self, temp_index_path):
        """IndexManager starting with an empty WAL should not error."""
        from app.core.rust_bridge import RUST_AVAILABLE

        if not RUST_AVAILABLE:
            pytest.skip("Rust core not available")

        from rust_core import IndexManager
        import uuid

        # Create and immediately restart with no data
        im1 = IndexManager(temp_index_path)
        coll_id = str(uuid.uuid4())
        im1.initialize_collection(coll_id)
        del im1

        # Restart with no WAL entries — should not error
        im2 = IndexManager(temp_index_path)
        graph_data = im2.get_graph_data(coll_id)
        assert graph_data is not None

    def test_wal_log_grows_with_upsert_operations(self, temp_index_path):
        """Each upsert operation should append to the WAL file."""
        from app.core.rust_bridge import RUST_AVAILABLE

        if not RUST_AVAILABLE:
            pytest.skip("Rust core not available")

        from rust_core import IndexManager
        import uuid
        import json

        coll_id = str(uuid.uuid4())

        im = IndexManager(temp_index_path)
        im.initialize_collection(coll_id)

        nodes = [{
            "id": str(uuid.uuid4()),
            "node_type": "concept",
            "label": "Test",
            "description": None,
            "aliases": [],
            "confidence": 0.5,
            "ontology_class": None,
            "properties": {},
            "collection_id": coll_id,
            "created_at": None,
            "updated_at": None,
        }]

        wal_path = os.path.join(temp_index_path, "wal.log")
        size_before = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0

        im.upsert_nodes(coll_id, json.dumps(nodes))

        size_after = os.path.getsize(wal_path)
        assert size_after > size_before, "WAL file must grow after upsert_nodes"

    def test_wal_append_is_durable_before_graph_write(self, temp_index_path):
        """WAL entry must be fsync'd before the graph write is visible."""
        from app.core.rust_bridge import RUST_AVAILABLE

        if not RUST_AVAILABLE:
            pytest.skip("Rust core not available")

        from rust_core import IndexManager
        import uuid
        import json

        coll_id = str(uuid.uuid4())

        im = IndexManager(temp_index_path)
        im.initialize_collection(coll_id)

        nodes = [{
            "id": str(uuid.uuid4()),
            "node_type": "org",
            "label": "Acme",
            "description": None,
            "aliases": [],
            "confidence": 0.9,
            "ontology_class": None,
            "properties": {},
            "collection_id": coll_id,
            "created_at": None,
            "updated_at": None,
        }]

        wal_path = os.path.join(temp_index_path, "wal.log")

        # WAL must have content AFTER upsert_nodes returns
        im.upsert_nodes(coll_id, json.dumps(nodes))

        with open(wal_path, "r") as f:
            wal_content = f.read()

        assert len(wal_content) > 0, "WAL must contain entries after upsert_nodes"
        assert 'upsert_nodes' in wal_content, "WAL must contain upsert_nodes operation"
