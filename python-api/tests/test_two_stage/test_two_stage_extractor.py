"""Tests for TwoStageExtractor — two-stage LLM entity/edge extraction."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.llm.two_stage_extractor import TwoStageExtractor
from app.models.template import (
    TemplateConfig, TemplateType, FieldType, FieldDef,
    EntitySchema, RelationSchema, ExtractionConfig, IdentifierConfig,
)


def _make_graph_template(**overrides) -> TemplateConfig:
    defaults = dict(
        name="test_graph",
        type=TemplateType.GRAPH,
        domain="test",
        description="Test graph template",
        entity_schema=EntitySchema(
            fields=[
                FieldDef(name="name", type=FieldType.STRING, description="Entity name", required=True),
                FieldDef(name="entity_type", type=FieldType.STRING, description="Entity type", required=True),
                FieldDef(name="description", type=FieldType.STRING, description="Description", required=False),
            ],
            key="{name}",
            display_label="{name} ({entity_type})",
        ),
        relation_schema=RelationSchema(
            fields=[
                FieldDef(name="source", type=FieldType.STRING, description="Source entity", required=True),
                FieldDef(name="target", type=FieldType.STRING, description="Target entity", required=True),
                FieldDef(name="predicate", type=FieldType.STRING, description="Relation type", required=True),
                FieldDef(name="context", type=FieldType.STRING, description="Context excerpt", required=False),
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
    defaults.update(overrides)
    return TemplateConfig(**defaults)


def _make_hypergraph_template(**overrides) -> TemplateConfig:
    overrides.setdefault("type", TemplateType.HYPERGRAPH)
    defaults = dict(
        name="test_hypergraph",
        domain="test",
        description="Test hypergraph template",
        entity_schema=EntitySchema(
            fields=[
                FieldDef(name="name", type=FieldType.STRING, description="Entity name", required=True),
                FieldDef(name="entity_type", type=FieldType.STRING, description="Entity type", required=True),
            ],
            key="{name}",
            display_label="{name}",
        ),
        relation_schema=RelationSchema(
            fields=[
                FieldDef(name="name", type=FieldType.STRING, description="Event name", required=True),
                FieldDef(name="event_type", type=FieldType.STRING, description="Event type", required=True),
                FieldDef(name="participants", type=FieldType.LIST, description="List of participant names", required=True),
            ],
            key="{name}|{event_type}",
            source_field="name",
            target_field="name",
            display_label="{event_type}",
        ),
        extraction=ExtractionConfig(mode="two_stage"),
        identifiers=IdentifierConfig(
            entity_key="name",
            relation_key="{name}|{event_type}",
            relation_source="name",
            relation_target="name",
        ),
    )
    defaults.update(overrides)
    return TemplateConfig(**defaults)


class TestTwoStageExtractorInit:
    def test_init_builds_entity_model(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        assert extractor.entity_model is not None
        assert extractor.entity_list_model is not None

    def test_init_builds_relation_model(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        assert extractor.relation_model is not None
        assert extractor.relation_list_model is not None

    def test_init_builds_key_function(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        assert extractor._entity_key_fn is not None

    def test_init_stores_template(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        assert extractor.template is template

    def test_init_stores_job_id(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template, job_id="job-123")
        assert extractor.job_id == "job-123"


class TestExtractEntities:
    @pytest.mark.asyncio
    async def test_returns_entity_list(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        with patch("app.llm.two_stage_extractor.call_ollama_cloud", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "content": '{"items": [{"name": "Alice", "entity_type": "Person"}]}',
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            }
            entities = await extractor.extract_entities("Alice works at Google.")
            assert len(entities) == 1
            assert entities[0]["name"] == "Alice"
            assert entities[0]["entity_type"] == "Person"

    @pytest.mark.asyncio
    async def test_handles_entities_key(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        with patch("app.llm.two_stage_extractor.call_ollama_cloud", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "content": '{"entities": [{"name": "Bob", "entity_type": "Organization"}]}',
                "usage": {},
            }
            entities = await extractor.extract_entities("Bob Corp exists.")
            assert len(entities) == 1
            assert entities[0]["name"] == "Bob"

    @pytest.mark.asyncio
    async def test_passes_system_and_user_prompts(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        with patch("app.llm.two_stage_extractor.call_ollama_cloud", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {"content": '{"items": []}', "usage": {}}
            await extractor.extract_entities("Some text")
            call_kwargs = mock_llm.call_args[1]
            assert "system_prompt" in call_kwargs
            assert "user_prompt" in call_kwargs
            assert "Some text" in call_kwargs["user_prompt"]

    @pytest.mark.asyncio
    async def test_passes_response_format(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        with patch("app.llm.two_stage_extractor.call_ollama_cloud", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {"content": '{"items": []}', "usage": {}}
            await extractor.extract_entities("Text")
            call_kwargs = mock_llm.call_args[1]
            assert call_kwargs.get("response_format") == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_passes_job_id_for_cost_tracking(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template, job_id="job-abc")
        with patch("app.llm.two_stage_extractor.call_ollama_cloud", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {"content": '{"items": []}', "usage": {}}
            await extractor.extract_entities("Text")
            call_kwargs = mock_llm.call_args[1]
            assert call_kwargs.get("job_id") == "job-abc"

    @pytest.mark.asyncio
    async def test_empty_content_returns_empty_list(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        with patch("app.llm.two_stage_extractor.call_ollama_cloud", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {"content": "", "usage": {}}
            entities = await extractor.extract_entities("No content")
            assert entities == []

    @pytest.mark.asyncio
    async def test_invalid_json_returns_empty_list(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        with patch("app.llm.two_stage_extractor.call_ollama_cloud", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {"content": "not valid json{{{", "usage": {}}
            entities = await extractor.extract_entities("Bad response")
            assert entities == []


class TestExtractRelations:
    @pytest.mark.asyncio
    async def test_returns_relation_list(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        known_entities = [
            {"name": "Alice", "entity_type": "Person"},
            {"name": "Google", "entity_type": "Organization"},
        ]
        with patch("app.llm.two_stage_extractor.call_ollama_cloud", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "content": '{"items": [{"source": "Alice", "target": "Google", "predicate": "works_at"}]}',
                "usage": {},
            }
            relations = await extractor.extract_relations("Alice works at Google.", known_entities)
            assert len(relations) == 1
            assert relations[0]["predicate"] == "works_at"

    @pytest.mark.asyncio
    async def test_includes_known_entities_in_prompt(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        known_entities = [
            {"name": "Alice", "entity_type": "Person"},
            {"name": "Google", "entity_type": "Organization"},
        ]
        with patch("app.llm.two_stage_extractor.call_ollama_cloud", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {"content": '{"items": []}', "usage": {}}
            await extractor.extract_relations("Text", known_entities)
            call_kwargs = mock_llm.call_args[1]
            user_prompt = call_kwargs["user_prompt"]
            assert "Alice" in user_prompt
            assert "Google" in user_prompt
            assert "Known Entities" in user_prompt

    @pytest.mark.asyncio
    async def test_no_entities_shows_placeholder(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        with patch("app.llm.two_stage_extractor.call_ollama_cloud", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {"content": '{"items": []}', "usage": {}}
            await extractor.extract_relations("Text", [])
            call_kwargs = mock_llm.call_args[1]
            assert "No entities identified" in call_kwargs["user_prompt"]

    @pytest.mark.asyncio
    async def test_handles_relationships_key(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        known_entities = [{"name": "A", "entity_type": "Person"}]
        with patch("app.llm.two_stage_extractor.call_ollama_cloud", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "content": '{"relationships": [{"source": "A", "target": "B", "predicate": "knows"}]}',
                "usage": {},
            }
            relations = await extractor.extract_relations("Text", known_entities)
            assert len(relations) == 1


class TestTwoStageFullPipeline:
    @pytest.mark.asyncio
    async def test_two_stage_calls_llm_twice(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        with patch("app.llm.two_stage_extractor.call_ollama_cloud", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [
                {"content": '{"items": [{"name": "Alice", "entity_type": "Person"}, {"name": "Google", "entity_type": "Organization"}]}', "usage": {}},
                {"content": '{"items": [{"source": "Alice", "target": "Google", "predicate": "works_at"}]}', "usage": {}},
            ]
            entities, relations = await extractor.extract_two_stage("Alice works at Google.")
            assert len(entities) == 2
            assert len(relations) == 1
            assert mock_llm.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_entities_skips_stage2(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        with patch("app.llm.two_stage_extractor.call_ollama_cloud", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {"content": '{"items": []}', "usage": {}}
            entities, relations = await extractor.extract_two_stage("Nothing here.")
            assert len(entities) == 0
            assert len(relations) == 0
            assert mock_llm.call_count == 1


class TestPromptConstruction:
    def test_entity_prompt_includes_field_descriptions(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        prompt = extractor._build_entity_system_prompt()
        assert "name" in prompt
        assert "entity_type" in prompt
        assert "required" in prompt
        assert "optional" in prompt

    def test_edge_prompt_includes_critical_rules(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        prompt = extractor._build_edge_system_prompt()
        assert "CRITICAL RULES" in prompt
        assert "ONLY extract relationships" in prompt
        assert "known entity list" in prompt

    def test_entity_prompt_includes_node_prompt_extra(self):
        template = _make_graph_template(extraction=ExtractionConfig(
            mode="two_stage", node_prompt_extra="Focus on legal entities."
        ))
        extractor = TwoStageExtractor(template)
        prompt = extractor._build_entity_system_prompt()
        assert "legal entities" in prompt

    def test_edge_prompt_includes_edge_prompt_extra(self):
        template = _make_graph_template(extraction=ExtractionConfig(
            mode="two_stage", edge_prompt_extra="Only extract explicitly stated legal relationships."
        ))
        extractor = TwoStageExtractor(template)
        prompt = extractor._build_edge_system_prompt()
        assert "explicitly stated legal relationships" in prompt

    def test_format_known_entities_includes_key(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        entities = [{"name": "Alice", "entity_type": "Person"}]
        formatted = extractor._format_known_entities(entities)
        assert "Alice" in formatted
        assert "Person" in formatted
        assert "key=" in formatted

    def test_format_known_entities_without_key(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        extractor._entity_key_fn = None
        entities = [{"name": "Alice", "entity_type": "Person"}]
        formatted = extractor._format_known_entities(entities)
        assert "Alice" in formatted
        assert "Person" in formatted
        assert "key=" not in formatted


class TestPydanticValidation:
    def test_entity_model_validates_required_fields(self):
        from app.services.template_factory import build_entity_pydantic_model
        template = _make_graph_template()
        model = build_entity_pydantic_model(template)
        obj = model(name="Alice", entity_type="Person")
        assert obj.name == "Alice"
        assert obj.description is None

    def test_entity_model_validates_all_fields(self):
        from app.services.template_factory import build_entity_pydantic_model
        template = _make_graph_template()
        model = build_entity_pydantic_model(template)
        obj = model(name="Alice", entity_type="Person", description="A person")
        assert obj.description == "A person"

    def test_entity_model_rejects_missing_required(self):
        from app.services.template_factory import build_entity_pydantic_model
        from pydantic import ValidationError
        template = _make_graph_template()
        model = build_entity_pydantic_model(template)
        with pytest.raises(ValidationError):
            model(name="Alice")

    def test_relation_model_validates(self):
        from app.services.template_factory import build_relation_pydantic_model
        template = _make_graph_template()
        model = build_relation_pydantic_model(template)
        obj = model(source="A", target="B", predicate="knows")
        assert obj.source == "A"
        assert obj.context is None

    def test_entity_list_model_validates(self):
        from app.services.template_factory import build_entity_pydantic_model, build_entity_list_model
        template = _make_graph_template()
        entity_model = build_entity_pydantic_model(template)
        list_model = build_entity_list_model(entity_model)
        obj = list_model(items=[{"name": "A", "entity_type": "Person"}])
        assert len(obj.items) == 1

    def test_relation_list_model_validates(self):
        from app.services.template_factory import build_relation_pydantic_model, build_relation_list_model
        template = _make_graph_template()
        relation_model = build_relation_pydantic_model(template)
        list_model = build_relation_list_model(relation_model)
        obj = list_model(items=[{"source": "A", "target": "B", "predicate": "knows"}])
        assert len(obj.items) == 1

    def test_response_parsing_with_entity_list_model(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        response = {"content": '{"items": [{"name": "A", "entity_type": "Person"}]}', "usage": {}}
        entities = extractor._parse_entity_response(response)
        assert len(entities) == 1
        assert entities[0]["name"] == "A"

    def test_response_parsing_with_invalid_json_falls_back(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        response = {"content": "not json", "usage": {}}
        entities = extractor._parse_entity_response(response)
        assert entities == []

    def test_response_parsing_with_entities_key(self):
        template = _make_graph_template()
        extractor = TwoStageExtractor(template)
        response = {"content": '{"entities": [{"name": "A", "entity_type": "Person"}]}', "usage": {}}
        entities = extractor._parse_entity_response(response)
        assert len(entities) == 1