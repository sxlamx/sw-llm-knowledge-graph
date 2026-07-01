"""Tests for hyperedge adjacency, pruning, and merge behavior.

Complements test_two_stage/test_edge_pruner.py with Rust-facing and
integration-level hyperedge tests.
"""

import pytest
from app.llm.edge_pruner import EdgePruner
from app.models.template import (
    TemplateConfig, TemplateType, FieldType, FieldDef,
    EntitySchema, RelationSchema, ExtractionConfig, IdentifierConfig,
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


class TestHyperedgePruning:
    """Tests from the Bot 3 spec for hyperedge pruning."""

    def test_valid_hyperedge_preserved(self):
        """Hyperedge with all valid participants should be kept."""
        edges = [{"name": "meeting", "type": "event", "participants": ["Alice", "Bob"]}]
        entity_keys = {"Alice", "Bob"}
        result = EdgePruner.prune_dangling_hyperedges(edges, entity_keys, "participants")
        assert len(result) == 1

    def test_dangling_hyperedge_removed(self):
        """Hyperedge with unknown participant should be removed."""
        edges = [{"name": "meeting", "type": "event", "participants": ["Alice", "Unknown"]}]
        entity_keys = {"Alice"}
        result = EdgePruner.prune_dangling_hyperedges(edges, entity_keys, "participants")
        assert len(result) == 0

    def test_hyperedge_all_participants_must_exist(self):
        """ALL participants must exist for hyperedge to survive."""
        edges = [{"name": "meeting", "type": "event", "participants": ["Alice", "Bob", "Unknown"]}]
        entity_keys = {"Alice", "Bob"}
        result = EdgePruner.prune_dangling_hyperedges(edges, entity_keys, "participants")
        assert len(result) == 0

    def test_hyperedge_empty_participants_removed(self):
        """Hyperedge with empty participants list should be removed."""
        edges = [{"name": "empty", "type": "event", "participants": []}]
        entity_keys = {"Alice"}
        result = EdgePruner.prune_dangling_hyperedges(edges, entity_keys, "participants")
        assert len(result) == 0


class TestHyperedgeAutoDispatch:
    """EdgePruner.prune() dispatches based on template type."""

    def test_graph_template_uses_binary_pruning(self):
        template = TemplateConfig(
            name="g",
            type=TemplateType.GRAPH,
            domain="test",
            entity_schema=EntitySchema(
                fields=[FieldDef(name="name", type=FieldType.STRING, description="Name", required=True)],
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
            identifiers=IdentifierConfig(
                entity_key="name",
                relation_key="{source}|{predicate}|{target}",
                relation_source="source",
                relation_target="target",
            ),
        )
        edges = [{"source": "A", "target": "Unknown", "predicate": "x"}]
        result = EdgePruner.prune(edges, {"A"}, template)
        assert len(result) == 0

    def test_hypergraph_template_uses_hyperedge_pruning(self):
        template = _make_hypergraph_template()
        edges = [{"name": "meeting", "participants": ["Alice", "Unknown"]}]
        result = EdgePruner.prune(edges, {"Alice"}, template)
        assert len(result) == 0

    def test_hypergraph_with_custom_participants_field(self):
        template = _make_hypergraph_template(participants_field="members")
        edges = [{"name": "meeting", "members": ["Alice", "Bob"]}]
        result = EdgePruner.prune(edges, {"Alice", "Bob"}, template)
        assert len(result) == 1