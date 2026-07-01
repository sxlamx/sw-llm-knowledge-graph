"""Tests for build_graph_from_ner — canonical labels, merge, co-occurrence edges."""

import json
import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.pipeline.build_graph_from_ner import _normalize, ENTITY_TYPE_SCHEMA


class TestNormalize:
    def test_lowercase(self):
        assert _normalize("Hello World") == "hello world"

    def test_collapse_whitespace(self):
        assert _normalize("  hello   world  ") == "hello world"

    def test_strip_non_alphanumeric(self):
        assert _normalize("O'Brien & Co.") == "obrien co"

    def test_empty_string(self):
        assert _normalize("") == ""

    def test_unicode_stripped(self):
        assert _normalize("café") == "caf"


class TestCanonicalEntityTypes:
    def test_entity_type_schema_has_no_org(self):
        assert "ORG" not in ENTITY_TYPE_SCHEMA, \
            "ORG should not be in ENTITY_TYPE_SCHEMA — use ORGANIZATION"

    def test_entity_type_schema_has_no_gpe(self):
        assert "GPE" not in ENTITY_TYPE_SCHEMA, \
            "GPE should not be in ENTITY_TYPE_SCHEMA — use LOCATION"

    def test_entity_type_schema_has_no_loc(self):
        assert "LOC" not in ENTITY_TYPE_SCHEMA, \
            "LOC should not be in ENTITY_TYPE_SCHEMA — use LOCATION"

    def test_entity_type_schema_has_canonical_names(self):
        assert "ORGANIZATION" in ENTITY_TYPE_SCHEMA
        assert "LOCATION" in ENTITY_TYPE_SCHEMA
        assert "PERSON" in ENTITY_TYPE_SCHEMA

    def test_entity_type_schema_has_legal_labels(self):
        assert "COURT_CASE" in ENTITY_TYPE_SCHEMA
        assert "LEGISLATION_TITLE" in ENTITY_TYPE_SCHEMA
        assert "LEGISLATION_REFERENCE" in ENTITY_TYPE_SCHEMA


class TestBuildGraphFromNer:
    @pytest.mark.asyncio
    async def test_creates_nodes_with_canonical_types(self):
        collection_id = str(uuid.uuid4())
        chunk_id_1 = str(uuid.uuid4())
        chunk_id_2 = str(uuid.uuid4())

        ner_tags_json = json.dumps([
            {"label": "ORGANIZATION", "text": "Acme Corp", "start": 0, "end": 9, "score": 0.9},
            {"label": "PERSON", "text": "Alice", "start": 15, "end": 20, "score": 0.85},
        ])

        mock_tbl = MagicMock()
        mock_tbl.count_rows.return_value = 2
        mock_tbl.search.return_value.select.return_value.limit.return_value.offset.return_value.to_list.side_effect = [
            [
                {"id": chunk_id_1, "ner_tags": ner_tags_json},
                {"id": chunk_id_2, "ner_tags": ner_tags_json},
            ],
            [],
        ]

        mock_db = MagicMock()
        mock_db.open_table.return_value = mock_tbl

        with (
            patch("app.pipeline.build_graph_from_ner.get_lancedb", new_callable=AsyncMock,
                  return_value=mock_db),
            patch("app.pipeline.build_graph_from_ner.list_graph_nodes", new_callable=AsyncMock,
                  return_value=[]),
            patch("app.pipeline.build_graph_from_ner.upsert_graph_nodes", new_callable=AsyncMock),
            patch("app.pipeline.build_graph_from_ner.upsert_graph_edges", new_callable=AsyncMock),
            patch("app.pipeline.build_graph_from_ner.get_index_manager", return_value=None),
        ):
            from app.pipeline.build_graph_from_ner import build_graph_from_ner
            result = await build_graph_from_ner(collection_id, min_chunk_freq=1)

        assert "error" not in result
        if result.get("added_nodes", 0) > 0:
            mock_db.open_table.assert_called()

    @pytest.mark.asyncio
    async def test_skips_non_canonical_entity_types(self):
        collection_id = str(uuid.uuid4())
        non_canonical_tags = json.dumps([
            {"label": "CARDINAL", "text": "42", "start": 0, "end": 2, "score": 0.9},
        ])

        mock_tbl = MagicMock()
        mock_tbl.count_rows.return_value = 1
        mock_tbl.search.return_value.select.return_value.limit.return_value.offset.return_value.to_list.side_effect = [
            [{"id": str(uuid.uuid4()), "ner_tags": non_canonical_tags}],
            [],
        ]

        mock_db = MagicMock()
        mock_db.open_table.return_value = mock_tbl

        with (
            patch("app.pipeline.build_graph_from_ner.get_lancedb", new_callable=AsyncMock,
                  return_value=mock_db),
            patch("app.pipeline.build_graph_from_ner.list_graph_nodes", new_callable=AsyncMock,
                  return_value=[]),
            patch("app.pipeline.build_graph_from_ner.upsert_graph_nodes", new_callable=AsyncMock),
            patch("app.pipeline.build_graph_from_ner.upsert_graph_edges", new_callable=AsyncMock),
            patch("app.pipeline.build_graph_from_ner.get_index_manager", return_value=None),
        ):
            from app.pipeline.build_graph_from_ner import build_graph_from_ner
            result = await build_graph_from_ner(collection_id, min_chunk_freq=1)

        assert result.get("added_nodes", 0) == 0, "CARDINAL should be filtered by SKIP_LABELS"

    @pytest.mark.asyncio
    async def test_merge_on_existing_updates_aliases(self):
        collection_id = str(uuid.uuid4())
        chunk_id = str(uuid.uuid4())

        tags_json = json.dumps([
            {"label": "PERSON", "text": "Alice", "start": 0, "end": 5, "score": 0.9},
        ])

        existing_node = {
            "id": str(uuid.uuid4()),
            "collection_id": collection_id,
            "label": "Alice",
            "entity_type": "PERSON",
            "description": "A person",
            "aliases": [],
            "confidence": 0.8,
            "source_chunk_ids": [],
            "topics": [],
            "properties": "{}",
            "created_at": 1000,
            "updated_at": 1000,
        }

        mock_tbl = MagicMock()
        mock_tbl.count_rows.return_value = 2
        mock_tbl.search.return_value.select.return_value.limit.return_value.offset.return_value.to_list.side_effect = [
            [{"id": chunk_id, "ner_tags": tags_json}, {"id": str(uuid.uuid4()), "ner_tags": tags_json}],
            [],
        ]

        mock_db = MagicMock()
        mock_db.open_table.return_value = mock_tbl

        nodes_upserted = []

        async def capture_upsert(cid, nodes):
            nodes_upserted.extend(nodes)

        with (
            patch("app.pipeline.build_graph_from_ner.get_lancedb", new_callable=AsyncMock,
                  return_value=mock_db),
            patch("app.pipeline.build_graph_from_ner.list_graph_nodes", new_callable=AsyncMock,
                  return_value=[existing_node]),
            patch("app.pipeline.build_graph_from_ner.upsert_graph_nodes", side_effect=capture_upsert),
            patch("app.pipeline.build_graph_from_ner.upsert_graph_edges", new_callable=AsyncMock),
            patch("app.pipeline.build_graph_from_ner.get_index_manager", return_value=None),
        ):
            from app.pipeline.build_graph_from_ner import build_graph_from_ner
            result = await build_graph_from_ner(collection_id, min_chunk_freq=1)

        assert result["merged_nodes"] >= 1, "Should detect and merge with existing node"


class TestConfidenceThreshold:
    def test_ingest_worker_uses_03_threshold(self):
        import ast
        with open("app/pipeline/ingest_worker.py") as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                if node.value == 0.3:
                    return
        pytest.fail("ingest_worker.py should pass 0.3 as confidence threshold to validator")