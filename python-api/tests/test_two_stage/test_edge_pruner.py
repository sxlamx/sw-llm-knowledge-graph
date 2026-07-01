"""Tests for EdgePruner — dangling edge removal for binary and hyperedges."""

import pytest
from app.llm.edge_pruner import EdgePruner
from app.models.template import (
    TemplateConfig, TemplateType, FieldType, FieldDef,
    EntitySchema, RelationSchema, ExtractionConfig, IdentifierConfig,
)


def _make_graph_template():
    return TemplateConfig(
        name="test_graph",
        type=TemplateType.GRAPH,
        domain="test",
        description="Test",
        entity_schema=EntitySchema(
            fields=[
                FieldDef(name="name", type=FieldType.STRING, description="Name", required=True),
                FieldDef(name="entity_type", type=FieldType.STRING, description="Type", required=True),
            ],
            key="{name}",
            display_label="{name}",
        ),
        relation_schema=RelationSchema(
            fields=[
                FieldDef(name="source", type=FieldType.STRING, description="Source", required=True),
                FieldDef(name="target", type=FieldType.STRING, description="Target", required=True),
                FieldDef(name="predicate", type=FieldType.STRING, description="Predicate", required=True),
            ],
            key="{source}|{predicate}|{target}",
            source_field="source",
            target_field="target",
            display_label="{predicate}",
        ),
        extraction=ExtractionConfig(mode="two_stage"),
        identifiers=IdentifierConfig(
            entity_key="name",
            relation_key="{source}|{predicate}|{target}",
            relation_source="source",
            relation_target="target",
        ),
    )


def _make_hypergraph_template(participants_field="participants"):
    return TemplateConfig(
        name="test_hypergraph",
        type=TemplateType.HYPERGRAPH,
        domain="test",
        description="Test",
        entity_schema=EntitySchema(
            fields=[
                FieldDef(name="name", type=FieldType.STRING, description="Name", required=True),
                FieldDef(name="entity_type", type=FieldType.STRING, description="Type", required=True),
            ],
            key="{name}",
            display_label="{name}",
        ),
        relation_schema=RelationSchema(
            fields=[
                FieldDef(name="name", type=FieldType.STRING, description="Event name", required=True),
                FieldDef(name="event_type", type=FieldType.STRING, description="Event type", required=True),
                FieldDef(name=participants_field, type=FieldType.LIST, description="Participants", required=True),
            ],
            key="{name}|{event_type}",
            source_field="name",
            target_field="name",
            display_label="{event_type}",
            participants_field=participants_field,
        ),
        extraction=ExtractionConfig(mode="two_stage"),
        identifiers=IdentifierConfig(
            entity_key="name",
            relation_key="{name}|{event_type}",
            relation_source="name",
            relation_target="name",
        ),
    )


class TestPruneDanglingBinary:
    def test_removes_dangling_source(self):
        edges = [
            {"source": "Alice", "target": "Bob", "predicate": "knows"},
            {"source": "Unknown", "target": "Bob", "predicate": "mentions"},
        ]
        result = EdgePruner.prune_dangling_binary(edges, {"Alice", "Bob"})
        assert len(result) == 1
        assert result[0]["predicate"] == "knows"

    def test_removes_dangling_target(self):
        edges = [
            {"source": "Alice", "target": "Unknown", "predicate": "mentions"},
        ]
        result = EdgePruner.prune_dangling_binary(edges, {"Alice", "Bob"})
        assert len(result) == 0

    def test_preserves_all_valid_edges(self):
        edges = [
            {"source": "Alice", "target": "Bob", "predicate": "knows"},
            {"source": "Bob", "target": "Alice", "predicate": "knows"},
            {"source": "Alice", "target": "Carol", "predicate": "works_with"},
        ]
        result = EdgePruner.prune_dangling_binary(edges, {"Alice", "Bob", "Carol"})
        assert len(result) == 3

    def test_empty_edges_returns_empty(self):
        result = EdgePruner.prune_dangling_binary([], {"Alice"})
        assert result == []

    def test_empty_entity_keys_removes_all(self):
        edges = [
            {"source": "Alice", "target": "Bob", "predicate": "knows"},
        ]
        result = EdgePruner.prune_dangling_binary(edges, set())
        assert len(result) == 0

    def test_strings_are_coerced(self):
        edges = [
            {"source": "key-1", "target": "key-2", "predicate": "x"},
        ]
        result = EdgePruner.prune_dangling_binary(edges, {"key-1", "key-2"})
        assert len(result) == 1


class TestPruneDanglingHyperedges:
    def test_removes_hyperedge_with_missing_participant(self):
        edges = [
            {"name": "meeting", "participants": ["Alice", "Bob", "Unknown"]},
        ]
        result = EdgePruner.prune_dangling_hyperedges(edges, {"Alice", "Bob"})
        assert len(result) == 0

    def test_preserves_hyperedge_with_all_participants(self):
        edges = [
            {"name": "meeting", "participants": ["Alice", "Bob", "Carol"]},
        ]
        result = EdgePruner.prune_dangling_hyperedges(edges, {"Alice", "Bob", "Carol"})
        assert len(result) == 1

    def test_default_participants_field(self):
        edges = [
            {"name": "e1", "participants": ["A", "B"]},
        ]
        result = EdgePruner.prune_dangling_hyperedges(edges, {"A", "B"})
        assert len(result) == 1

    def test_custom_participants_field(self):
        edges = [
            {"name": "e1", "members": ["A", "B"]},
        ]
        result = EdgePruner.prune_dangling_hyperedges(edges, {"A", "B"}, participants_field="members")
        assert len(result) == 1

    def test_string_participant_coerced_to_list(self):
        edges = [
            {"name": "e1", "participants": "A"},
        ]
        result = EdgePruner.prune_dangling_hyperedges(edges, {"A"})
        assert len(result) == 1

    def test_empty_participants_list_removes_edge(self):
        edges = [
            {"name": "e1", "participants": []},
        ]
        result = EdgePruner.prune_dangling_hyperedges(edges, {"A", "B"})
        assert len(result) == 0

    def test_mixed_valid_and_invalid(self):
        edges = [
            {"name": "meeting", "participants": ["Alice", "Bob"]},
            {"name": "party", "participants": ["Alice", "Unknown"]},
        ]
        result = EdgePruner.prune_dangling_hyperedges(edges, {"Alice", "Bob"})
        assert len(result) == 1
        assert result[0]["name"] == "meeting"


class TestEdgePrunerAutoDetect:
    def test_graph_template_uses_binary_pruning(self):
        template = _make_graph_template()
        edges = [
            {"source": "A", "target": "Unknown", "predicate": "x"},
        ]
        result = EdgePruner.prune(edges, {"A"}, template)
        assert len(result) == 0

    def test_hypergraph_template_uses_hyperedge_pruning(self):
        template = _make_hypergraph_template()
        edges = [
            {"name": "meeting", "participants": ["Alice", "Unknown"]},
        ]
        result = EdgePruner.prune(edges, {"Alice"}, template)
        assert len(result) == 0

    def test_hypergraph_with_custom_participants_field(self):
        template = _make_hypergraph_template(participants_field="members")
        edges = [
            {"name": "meeting", "members": ["Alice", "Bob"]},
        ]
        result = EdgePruner.prune(edges, {"Alice", "Bob"}, template)
        assert len(result) == 1