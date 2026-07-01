"""Tests for TemplateFactory — schemas, prompts, key patterns, label renderers."""

import pytest
from app.models.template import (
    TemplateConfig,
    TemplateType,
    FieldType,
    FieldDef,
    EntitySchema,
    RelationSchema,
    ExtractionConfig,
    IdentifierConfig,
)
from app.services.template_factory import (
    TemplateFactory,
    TemplateArtifacts,
    _compile_key_pattern,
    _compile_label_pattern,
)


def _make_graph_config():
    return TemplateConfig(
        name="test_graph",
        type=TemplateType.GRAPH,
        language=["en"],
        domain="test",
        description="Test graph template",
        entity_schema=EntitySchema(
            fields=[
                FieldDef(name="name", type=FieldType.STRING, required=True, description="Entity name"),
                FieldDef(name="entity_type", type=FieldType.STRING, required=True, description="Type"),
                FieldDef(name="description", type=FieldType.STRING, required=False, description="Desc"),
            ],
            key="{name}",
            display_label="{name} ({entity_type})",
        ),
        relation_schema=RelationSchema(
            fields=[
                FieldDef(name="source", type=FieldType.STRING, required=True),
                FieldDef(name="target", type=FieldType.STRING, required=True),
                FieldDef(name="predicate", type=FieldType.STRING, required=True),
                FieldDef(name="context", type=FieldType.STRING, required=False),
            ],
            key="{source}|{predicate}|{target}",
            source_field="source",
            target_field="target",
            display_label="{predicate}",
        ),
        extraction=ExtractionConfig(mode="two_stage"),
        identifiers=IdentifierConfig(
            entity_key="{name}",
            relation_key="{source}|{predicate}|{target}",
            relation_source="source",
            relation_target="target",
        ),
    )


# ── Key pattern compilation ──────────────────────────────────────────

class TestKeyPatternCompiler:
    def test_simple_key(self):
        fn = _compile_key_pattern("{name}")
        assert fn({"name": "Alice"}) == "Alice"

    def test_composite_key(self):
        fn = _compile_key_pattern("{source}|{predicate}|{target}")
        assert fn({"source": "A", "predicate": "cited", "target": "B"}) == "A|cited|B"

    def test_temporal_key(self):
        fn = _compile_key_pattern("{source}|{predicate}|{target}@{time}")
        result = fn({"source": "A", "predicate": "cited", "target": "B", "time": "2024"})
        assert result == "A|cited|B@2024"

    def test_spatial_key(self):
        fn = _compile_key_pattern("{source}|{predicate}|{target}@{location}")
        result = fn({"source": "A", "predicate": "located", "target": "B", "location": "NYC"})
        assert result == "A|located|B@NYC"

    def test_spatio_temporal_key(self):
        fn = _compile_key_pattern("{source}|{predicate}|{target}@{time}|{location}")
        result = fn({"source": "A", "predicate": "occurred", "target": "B", "time": "2024", "location": "DC"})
        assert result == "A|occurred|B@2024|DC"

    def test_missing_field_stripped_trailing_separator(self):
        fn = _compile_key_pattern("{source}|{predicate}|{target}")
        result = fn({"source": "A", "predicate": "cited"})
        assert result == "A|cited"

    def test_no_placeholder_pattern(self):
        fn = _compile_key_pattern("static_key")
        assert fn({"name": "Alice"}) == "static_key"

    def test_empty_pattern(self):
        fn = _compile_key_pattern("")
        assert fn({}) == ""

    def test_list_field_in_key(self):
        fn = _compile_key_pattern("{predicate}|{participants}")
        result = fn({"predicate": "collaborated", "participants": ["Alice", "Bob", "Carol"]})
        assert result == "collaborated|Alice|Bob|Carol"

    def test_deterministic_output(self):
        fn = _compile_key_pattern("{name}")
        data = {"name": "Test"}
        assert fn(data) == fn(data)
        assert fn(data) == fn(data)


# ── Display label rendering ──────────────────────────────────────────

class TestDisplayLabelRenderer:
    def test_entity_label(self):
        fn = _compile_label_pattern("{name} ({entity_type})")
        assert fn({"name": "Alice", "entity_type": "Person"}) == "Alice (Person)"

    def test_simple_label(self):
        fn = _compile_label_pattern("{predicate}")
        assert fn({"predicate": "cited"}) == "cited"

    def test_missing_field_falls_back_to_name(self):
        fn = _compile_label_pattern("{name} ({entity_type})")
        result = fn({"name": "Alice"})
        assert "Alice" in result

    def test_missing_all_fields_falls_back_to_unknown(self):
        fn = _compile_label_pattern("{title}")
        assert fn({}) == "unknown"

    def test_label_with_time(self):
        fn = _compile_label_pattern("{predicate} ({time})")
        assert fn({"predicate": "cited", "time": "2024"}) == "cited (2024)"


# ── TemplateFactory.create() ─────────────────────────────────────────

class TestTemplateFactoryCreate:
    @pytest.fixture
    def graph_config(self):
        return _make_graph_config()

    def test_create_produces_all_artifacts(self, graph_config):
        artifacts = TemplateFactory.create(graph_config)
        assert artifacts.config is graph_config
        assert artifacts.entity_schema is not None
        assert artifacts.relation_schema is not None
        assert artifacts.node_prompt != ""
        assert artifacts.edge_prompt != ""
        assert artifacts.entity_key_fn is not None
        assert artifacts.relation_key_fn is not None
        assert artifacts.entity_label_fn is not None
        assert artifacts.relation_label_fn is not None

    def test_entity_key_fn(self, graph_config):
        artifacts = TemplateFactory.create(graph_config)
        assert artifacts.entity_key_fn({"name": "Alice"}) == "Alice"

    def test_relation_key_fn(self, graph_config):
        artifacts = TemplateFactory.create(graph_config)
        key = artifacts.relation_key_fn({"source": "A", "predicate": "knows", "target": "B"})
        assert key == "A|knows|B"

    def test_entity_label_fn(self, graph_config):
        artifacts = TemplateFactory.create(graph_config)
        label = artifacts.entity_label_fn({"name": "Alice", "entity_type": "PERSON"})
        assert label == "Alice (PERSON)"

    def test_relation_label_fn(self, graph_config):
        artifacts = TemplateFactory.create(graph_config)
        label = artifacts.relation_label_fn({"predicate": "works_at"})
        assert label == "works_at"

    def test_non_graph_template(self):
        config = TemplateConfig(name="mylist", type=TemplateType.LIST)
        artifacts = TemplateFactory.create(config)
        assert artifacts.entity_schema is None
        assert artifacts.relation_schema is None
        assert artifacts.entity_key_fn is None
        assert artifacts.relation_key_fn is None

    def test_schema_json_structure(self, graph_config):
        artifacts = TemplateFactory.create(graph_config)
        schema = artifacts.entity_schema
        assert schema["type"] == "object"
        assert "name" in schema["properties"]
        assert "name" in schema["required"]
        assert "description" not in schema["required"]


# ── Prompt construction ──────────────────────────────────────────────

class TestPromptConstruction:
    def test_language_param_in_prompt(self):
        config = TemplateConfig(name="test", type=TemplateType.LIST, language=["fr"])
        artifacts = TemplateFactory.create(config, language="fr")
        assert "fr" in artifacts.node_prompt

    def test_english_no_language_note(self):
        config = TemplateConfig(name="test", type=TemplateType.LIST, language=["en"])
        artifacts = TemplateFactory.create(config, language="en")
        assert "Respond in en" not in artifacts.node_prompt
        assert "Respond in fr" not in artifacts.node_prompt

    def test_prompt_extra_included(self):
        config = TemplateConfig(
            name="test", type=TemplateType.LIST,
            language=["en"],
            extraction=ExtractionConfig(node_prompt_extra="Focus on legal entities."),
        )
        artifacts = TemplateFactory.create(config)
        assert "legal entities" in artifacts.node_prompt

    def test_edge_prompt_extra_included(self):
        config = _make_graph_config()
        config.extraction.edge_prompt_extra = "Only extract explicit relationships."
        artifacts = TemplateFactory.create(config)
        assert "explicit relationships" in artifacts.edge_prompt

    def test_entity_field_names_in_prompt(self):
        artifacts = TemplateFactory.create(_make_graph_config())
        assert '"name"' in artifacts.node_prompt
        assert '"entity_type"' in artifacts.node_prompt

    def test_relation_field_names_in_prompt(self):
        artifacts = TemplateFactory.create(_make_graph_config())
        assert '"source"' in artifacts.edge_prompt
        assert '"predicate"' in artifacts.edge_prompt