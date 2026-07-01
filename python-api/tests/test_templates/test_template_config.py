"""Tests for TemplateConfig and related Pydantic models."""

import pytest
from pydantic import ValidationError

from app.models.template import (
    TemplateConfig,
    TemplateType,
    FieldType,
    FieldDef,
    EntitySchema,
    RelationSchema,
    ExtractionConfig,
    IdentifierConfig,
    TemplateSummary,
)


def _make_entity_schema():
    return EntitySchema(
        fields=[
            FieldDef(name="name", type=FieldType.STRING, required=True, description="Entity name"),
            FieldDef(name="entity_type", type=FieldType.STRING, required=True, description="Type"),
        ],
        key="{name}",
        display_label="{name} ({entity_type})",
    )


def _make_relation_schema():
    return RelationSchema(
        fields=[
            FieldDef(name="source", type=FieldType.STRING, required=True, description="Source"),
            FieldDef(name="target", type=FieldType.STRING, required=True, description="Target"),
            FieldDef(name="predicate", type=FieldType.STRING, required=True, description="Predicate"),
        ],
        key="{source}|{predicate}|{target}",
        source_field="source",
        target_field="target",
        display_label="{predicate}",
    )


def _make_identifiers():
    return IdentifierConfig(
        entity_key="{name}",
        relation_key="{source}|{predicate}|{target}",
        relation_source="source",
        relation_target="target",
    )


def _make_graph_config():
    return TemplateConfig(
        name="test",
        type=TemplateType.GRAPH,
        entity_schema=_make_entity_schema(),
        relation_schema=_make_relation_schema(),
        identifiers=_make_identifiers(),
    )


# ── TemplateType validation ──────────────────────────────────────────

class TestTemplateTypeValidation:
    def test_all_eight_types_accepted(self):
        for t in ["model", "list", "set", "graph", "hypergraph",
                  "temporal_graph", "spatial_graph", "spatio_temporal_graph"]:
            if t in ("graph", "hypergraph", "temporal_graph", "spatial_graph", "spatio_temporal_graph"):
                TemplateConfig(name="test", type=t, entity_schema=_make_entity_schema(),
                              relation_schema=_make_relation_schema(), identifiers=_make_identifiers())
            else:
                TemplateConfig(name="test", type=t)

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError, match="Input should be"):
            TemplateConfig(name="test", type="invalid_type")


# ── Graph type constraints ───────────────────────────────────────────

class TestGraphTypeConstraints:
    def test_graph_type_requires_entity_schema(self):
        with pytest.raises(ValidationError, match="entity_schema is required"):
            TemplateConfig(name="test", type=TemplateType.GRAPH, entity_schema=None)

    def test_graph_type_requires_relation_schema(self):
        with pytest.raises(ValidationError, match="relation_schema is required"):
            TemplateConfig(
                name="test", type=TemplateType.GRAPH,
                entity_schema=_make_entity_schema(),
                relation_schema=None,
                identifiers=_make_identifiers(),
            )

    def test_graph_type_requires_identifiers(self):
        with pytest.raises(ValidationError, match="identifiers is required"):
            TemplateConfig(
                name="test", type=TemplateType.GRAPH,
                entity_schema=_make_entity_schema(),
                relation_schema=_make_relation_schema(),
                identifiers=None,
            )

    def test_model_type_does_not_require_entity_schema(self):
        cfg = TemplateConfig(name="test", type=TemplateType.MODEL)
        assert cfg.entity_schema is None

    def test_list_type_does_not_require_entity_schema(self):
        cfg = TemplateConfig(name="test", type=TemplateType.LIST)
        assert cfg.entity_schema is None

    def test_set_type_does_not_require_entity_schema(self):
        cfg = TemplateConfig(name="test", type=TemplateType.SET)
        assert cfg.entity_schema is None

    def test_hypergraph_type_requires_all_three(self):
        for missing_field in ("entity_schema", "relation_schema", "identifiers"):
            kwargs = {
                "name": "test",
                "type": TemplateType.HYPERGRAPH,
                "entity_schema": _make_entity_schema(),
                "relation_schema": _make_relation_schema(),
                "identifiers": _make_identifiers(),
            }
            kwargs[missing_field] = None
            with pytest.raises(ValidationError, match="is required"):
                TemplateConfig(**kwargs)

    def test_temporal_graph_requires_all_three(self):
        with pytest.raises(ValidationError):
            TemplateConfig(name="test", type=TemplateType.TEMPORAL_GRAPH)


# ── EntitySchema validation ──────────────────────────────────────────

class TestEntitySchemaValidation:
    def test_key_must_contain_placeholder(self):
        with pytest.raises(ValidationError, match="must contain"):
            EntitySchema(
                fields=[FieldDef(name="name", type=FieldType.STRING)],
                key="static", display_label="{name}",
            )

    def test_display_label_must_contain_placeholder(self):
        with pytest.raises(ValidationError, match="must contain"):
            EntitySchema(
                fields=[FieldDef(name="name", type=FieldType.STRING)],
                key="{name}", display_label="static",
            )

    def test_duplicate_field_names_rejected(self):
        with pytest.raises(ValidationError, match="duplicate field names"):
            EntitySchema(
                fields=[
                    FieldDef(name="name", type=FieldType.STRING),
                    FieldDef(name="name", type=FieldType.STRING),
                ],
                key="{name}", display_label="{name}",
            )

    def test_unique_field_names_accepted(self):
        schema = EntitySchema(
            fields=[
                FieldDef(name="name", type=FieldType.STRING),
                FieldDef(name="entity_type", type=FieldType.STRING),
            ],
            key="{name}", display_label="{name}",
        )
        assert len(schema.fields) == 2


# ── RelationSchema validation ────────────────────────────────────────

class TestRelationSchemaValidation:
    def test_key_must_contain_placeholder(self):
        with pytest.raises(ValidationError, match="must contain"):
            RelationSchema(
                fields=[FieldDef(name="source", type=FieldType.STRING)],
                key="static", source_field="source", target_field="target",
                display_label="{source}",
            )

    def test_duplicate_field_names_rejected(self):
        with pytest.raises(ValidationError, match="duplicate field names"):
            RelationSchema(
                fields=[
                    FieldDef(name="source", type=FieldType.STRING),
                    FieldDef(name="source", type=FieldType.STRING),
                ],
                key="{source}", source_field="source", target_field="target",
                display_label="{source}",
            )


# ── ExtractionConfig validation ──────────────────────────────────────

class TestExtractionConfigValidation:
    def test_valid_modes(self):
        for mode in ("one_stage", "two_stage"):
            cfg = ExtractionConfig(mode=mode)
            assert cfg.mode == mode

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValidationError, match="must be"):
            ExtractionConfig(mode="three_stage")

    def test_valid_merge_strategies(self):
        for strategy in ("exact", "keep_first", "keep_last", "field_overwrite",
                         "llm_balanced", "llm_prefer_first", "llm_prefer_last"):
            cfg = ExtractionConfig(merge_strategy_nodes=strategy, merge_strategy_edges=strategy)
            assert cfg.merge_strategy_nodes == strategy
            assert cfg.merge_strategy_edges == strategy

    def test_invalid_merge_strategy_nodes_rejected(self):
        with pytest.raises(ValidationError, match="merge_strategy_nodes"):
            ExtractionConfig(merge_strategy_nodes="invalid_strategy")

    def test_invalid_merge_strategy_edges_rejected(self):
        with pytest.raises(ValidationError, match="merge_strategy_edges"):
            ExtractionConfig(merge_strategy_edges="bad_value")

    def test_default_values(self):
        cfg = ExtractionConfig()
        assert cfg.mode == "two_stage"
        assert cfg.method == "standard"
        assert cfg.merge_strategy_nodes == "exact"
        assert cfg.merge_strategy_edges == "exact"

    def test_valid_methods(self):
        for method in ("standard", "two_stage", "graph_rag", "light_rag"):
            cfg = ExtractionConfig(method=method)
            assert cfg.method == method

    def test_invalid_method_rejected(self):
        with pytest.raises(ValidationError, match="extraction method"):
            ExtractionConfig(method="invalid_method")


# ── IdentifierConfig ─────────────────────────────────────────────────

class TestIdentifierConfig:
    def test_basic_config(self):
        ic = IdentifierConfig(
            entity_key="{name}",
            relation_key="{source}|{predicate}|{target}",
            relation_source="source",
            relation_target="target",
        )
        assert ic.entity_key == "{name}"

    def test_temporal_fields(self):
        ic = IdentifierConfig(
            entity_key="{name}",
            relation_key="{source}|{predicate}|{target}@{time}",
            relation_source="source",
            relation_target="target",
            time_field="time",
        )
        assert ic.time_field == "time"

    def test_spatial_fields(self):
        ic = IdentifierConfig(
            entity_key="{name}",
            relation_key="{source}|{predicate}|{target}@{location}",
            relation_source="source",
            relation_target="target",
            location_field="location",
        )
        assert ic.location_field == "location"


# ── TemplateSummary ───────────────────────────────────────────────────

class TestTemplateSummary:
    def test_summary_fields(self):
        s = TemplateSummary(
            key="general/graph",
            name="graph",
            domain="general",
            type="graph",
            description="Test",
        )
        assert s.key == "general/graph"
        assert s.type == "graph"


# ── Full TemplateConfig round-trip ───────────────────────────────────

class TestTemplateConfigRoundTrip:
    def test_graph_config_serialization(self):
        cfg = _make_graph_config()
        data = cfg.model_dump()
        assert data["name"] == "test"
        assert data["type"] == TemplateType.GRAPH
        assert data["entity_schema"] is not None
        assert data["relation_schema"] is not None

    def test_graph_config_roundtrip(self):
        cfg = _make_graph_config()
        data = cfg.model_dump()
        restored = TemplateConfig(**data)
        assert restored.name == cfg.name
        assert restored.type == cfg.type