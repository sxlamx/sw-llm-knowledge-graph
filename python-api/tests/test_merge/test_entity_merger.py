import pytest
from unittest.mock import AsyncMock, patch

from app.services.entity_merger import EntityMerger
from app.services.merge_strategy import MergeStrategy
from app.llm.ollama_client import OllamaCloudError


@pytest.fixture
def merger():
    return EntityMerger()


@pytest.fixture
def merger_with_job():
    return EntityMerger(job_id="test-job-123")


class TestDeterministicMerge:
    @pytest.mark.asyncio
    async def test_keep_first_returns_existing(self, merger):
        existing = {"id": "1", "name": "Alice", "description": "Original"}
        incoming = {"id": "2", "name": "Alice", "description": "Updated"}
        result = await merger.merge(existing, incoming, MergeStrategy.KEEP_FIRST, "node")
        assert result["description"] == "Original"
        assert result["id"] == "1"

    @pytest.mark.asyncio
    async def test_keep_last_returns_incoming_with_existing_id(self, merger):
        existing = {"id": "1", "name": "Alice", "description": "Original"}
        incoming = {"id": "2", "name": "Alice", "description": "Updated"}
        result = await merger.merge(existing, incoming, MergeStrategy.KEEP_LAST, "node")
        assert result["description"] == "Updated"
        assert result["id"] == "1"

    @pytest.mark.asyncio
    async def test_field_overwrite_fills_nulls(self, merger):
        existing = {"id": "1", "name": "Alice", "description": None}
        incoming = {"id": "2", "name": "Alice", "description": "A person"}
        result = await merger.merge(existing, incoming, MergeStrategy.FIELD_OVERWRITE, "node")
        assert result["description"] == "A person"

    @pytest.mark.asyncio
    async def test_field_overwrite_appends_lists(self, merger):
        existing = {"id": "1", "name": "Alice", "aliases": ["Al"]}
        incoming = {"id": "2", "name": "Alice", "aliases": ["Alice Smith"]}
        result = await merger.merge(existing, incoming, MergeStrategy.FIELD_OVERWRITE, "node")
        assert "Al" in result["aliases"]
        assert "Alice Smith" in result["aliases"]

    @pytest.mark.asyncio
    async def test_field_overwrite_no_duplicate_aliases(self, merger):
        existing = {"id": "1", "name": "Alice", "aliases": ["Al", "Ali"]}
        incoming = {"id": "2", "name": "Alice", "aliases": ["Al", "Alicia"]}
        result = await merger.merge(existing, incoming, MergeStrategy.FIELD_OVERWRITE, "node")
        assert result["aliases"].count("Al") == 1

    @pytest.mark.asyncio
    async def test_exact_returns_existing_unchanged(self, merger):
        existing = {"id": "1", "name": "Alice"}
        incoming = {"id": "2", "name": "Alice", "description": "New info"}
        result = await merger.merge(existing, incoming, MergeStrategy.EXACT, "node")
        assert result == existing

    @pytest.mark.asyncio
    async def test_field_overwrite_averages_confidence(self, merger):
        existing = {"id": "1", "name": "Alice", "confidence": 0.8}
        incoming = {"id": "2", "name": "Alice", "confidence": 0.6}
        result = await merger.merge(existing, incoming, MergeStrategy.FIELD_OVERWRITE, "node")
        assert abs(result["confidence"] - 0.7) < 0.01

    @pytest.mark.asyncio
    async def test_canonical_id_always_preserved(self, merger):
        for strategy in [MergeStrategy.KEEP_LAST, MergeStrategy.FIELD_OVERWRITE]:
            existing = {"id": "uuid-aaa", "name": "Alice"}
            incoming = {"id": "uuid-bbb", "name": "Alice"}
            result = await merger.merge(existing, incoming, strategy, "node")
            assert result["id"] == "uuid-aaa"


class TestLLMMerge:
    @pytest.mark.asyncio
    async def test_llm_balanced_merge(self, merger):
        existing = {"id": "1", "name": "Alice", "description": "A software engineer"}
        incoming = {"id": "2", "name": "Alice", "description": "Works at Google"}
        with patch(
            "app.services.entity_merger.call_ollama_cloud", new_callable=AsyncMock
        ) as mock_llm:
            mock_llm.return_value = {
                "content": '{"id": "1", "name": "Alice", "description": "A software engineer who works at Google"}',
                "usage": {"prompt_tokens": 200, "completion_tokens": 50},
            }
            result = await merger.merge(
                existing, incoming, MergeStrategy.LLM_BALANCED, "node"
            )
            assert result["id"] == "1"
            mock_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_merge_preserves_canonical_id(self, merger):
        existing = {"id": "1", "name": "Alice"}
        incoming = {"id": "2", "name": "Alice"}
        with patch(
            "app.services.entity_merger.call_ollama_cloud", new_callable=AsyncMock
        ) as mock_llm:
            mock_llm.return_value = {
                "content": '{"id": "2", "name": "Alice"}',
                "usage": {},
            }
            result = await merger.merge(
                existing, incoming, MergeStrategy.LLM_PREFER_FIRST, "node"
            )
            assert result["id"] == "1"

    @pytest.mark.asyncio
    async def test_llm_merge_fallback_on_error(self, merger):
        existing = {"id": "1", "name": "Alice", "description": "Original"}
        incoming = {"id": "2", "name": "Alice", "description": "Updated"}
        with patch(
            "app.services.entity_merger.call_ollama_cloud",
            new_callable=AsyncMock,
            side_effect=OllamaCloudError("API error"),
        ):
            result = await merger.merge(
                existing, incoming, MergeStrategy.LLM_BALANCED, "node"
            )
            assert result["description"] == "Original"
            assert result["id"] == "1"

    @pytest.mark.asyncio
    async def test_llm_merge_fallback_on_json_error(self, merger):
        existing = {"id": "1", "name": "Alice", "description": "Original"}
        incoming = {"id": "2", "name": "Alice", "description": "Updated"}
        with patch(
            "app.services.entity_merger.call_ollama_cloud", new_callable=AsyncMock
        ) as mock_llm:
            mock_llm.return_value = {
                "content": "not valid json{{{",
                "usage": {},
            }
            result = await merger.merge(
                existing, incoming, MergeStrategy.LLM_BALANCED, "node"
            )
            assert result["description"] == "Original"

    @pytest.mark.asyncio
    async def test_llm_merge_passes_job_id(self, merger_with_job):
        existing = {"id": "1", "name": "Alice"}
        incoming = {"id": "2", "name": "Alice"}
        with patch(
            "app.services.entity_merger.call_ollama_cloud", new_callable=AsyncMock
        ) as mock_llm:
            mock_llm.return_value = {
                "content": '{"id": "1", "name": "Alice"}',
                "usage": {},
            }
            await merger_with_job.merge(
                existing, incoming, MergeStrategy.LLM_BALANCED, "node"
            )
            call_kwargs = mock_llm.call_args
            assert call_kwargs.kwargs.get("job_id") == "test-job-123"

    @pytest.mark.asyncio
    async def test_llm_prefer_first_bias(self, merger):
        existing = {"id": "1", "name": "Alice"}
        incoming = {"id": "2", "name": "Alice"}
        with patch(
            "app.services.entity_merger.call_ollama_cloud", new_callable=AsyncMock
        ) as mock_llm:
            mock_llm.return_value = {
                "content": '{"id": "1", "name": "Alice"}',
                "usage": {},
            }
            await merger.merge(
                existing, incoming, MergeStrategy.LLM_PREFER_FIRST, "node"
            )
            system_prompt = mock_llm.call_args.kwargs.get(
                "system_prompt", mock_llm.call_args[0][0] if mock_llm.call_args[0] else ""
            )
            assert "prefer the existing version" in system_prompt

    @pytest.mark.asyncio
    async def test_llm_prefer_last_bias(self, merger):
        existing = {"id": "1", "name": "Alice"}
        incoming = {"id": "2", "name": "Alice"}
        with patch(
            "app.services.entity_merger.call_ollama_cloud", new_callable=AsyncMock
        ) as mock_llm:
            mock_llm.return_value = {
                "content": '{"id": "1", "name": "Alice"}',
                "usage": {},
            }
            await merger.merge(
                existing, incoming, MergeStrategy.LLM_PREFER_LAST, "node"
            )
            system_prompt = mock_llm.call_args.kwargs.get(
                "system_prompt", mock_llm.call_args[0][0] if mock_llm.call_args[0] else ""
            )
            assert "prefer the incoming version" in system_prompt


class TestSchemaFields:
    def test_default_node_schema(self, merger):
        fields = merger._get_schema_fields("node")
        assert "label" in fields
        assert "entity_type" in fields
        assert "description" in fields

    def test_default_edge_schema(self, merger):
        fields = merger._get_schema_fields("edge")
        assert "predicate" in fields
        assert "source" in fields
        assert "target" in fields

    def test_no_template_returns_defaults(self, merger):
        node_fields = merger._get_schema_fields("node")
        edge_fields = merger._get_schema_fields("edge")
        assert node_fields != {}
        assert edge_fields != {}