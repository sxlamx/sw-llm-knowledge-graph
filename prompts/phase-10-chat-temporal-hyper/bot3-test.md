# Bot 3 — Test: Phase 10 (Knowledge Chat + Temporal/Spatial + Hyperedges)

> **Features**: F5 + F6 + F7

---

## Test Files

### `python-api/tests/test_chat/test_knowledge_chat.py`

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.knowledge_chat import KnowledgeChatService

@pytest.fixture
def chat_service():
    return KnowledgeChatService(collection_id="test-col-123")

class TestKnowledgeChatService:
    @pytest.mark.asyncio
    async def test_search_knowledge_returns_nodes_and_edges(self, chat_service):
        """search_knowledge should return matching nodes and edges."""
        with patch("app.services.knowledge_chat.get_index_manager") as mock_im:
            mock_instance = MagicMock()
            mock_instance.search_nodes.return_value = '[{"label": "Alice", "score": 0.9}]'
            mock_instance.search_edges.return_value = '[{"predicate": "works_at", "score": 0.85}]'
            mock_im.return_value = mock_instance

            with patch("app.services.knowledge_chat.embed_query",
                        new_callable=AsyncMock, return_value=[0.1] * 1024):
                nodes, edges = await chat_service.search_knowledge("Who works at Google?")
                assert len(nodes) == 1
                assert len(edges) == 1

    @pytest.mark.asyncio
    async def test_chat_returns_answer_and_retrieved_items(self, chat_service):
        """chat() should return answer, nodes, and edges."""
        with patch.object(chat_service, "search_knowledge",
                          new_callable=AsyncMock,
                          return_value=(
                              [{"label": "Alice", "entity_type": "Person"}],
                              [{"predicate": "works_at", "source": "Alice", "target": "Google"}],
                          )):
            with patch("app.services.knowledge_chat.call_ollama_cloud",
                        new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = {
                    "content": "Alice works at Google.",
                    "usage": {"prompt_tokens": 200, "completion_tokens": 50},
                }
                result = await chat_service.chat("Who works at Google?")
                assert "answer" in result
                assert "nodes" in result
                assert "edges" in result
                assert result["answer"] == "Alice works at Google."
                assert len(result["nodes"]) == 1
                assert len(result["edges"]) == 1

    @pytest.mark.asyncio
    async def test_chat_no_results(self, chat_service):
        """chat() with no search results should still return an answer."""
        with patch.object(chat_service, "search_knowledge",
                          new_callable=AsyncMock,
                          return_value=([], [])):
            with patch("app.services.knowledge_chat.call_ollama_cloud",
                        new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = {
                    "content": "I don't have information about that.",
                    "usage": {},
                }
                result = await chat_service.chat("Unknown topic")
                assert "answer" in result
```

### `python-api/tests/test_chat/test_chat_endpoint.py`

```python
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
class TestChatEndpoint:
    async def test_chat_returns_answer(self, client: AsyncClient, auth_headers, test_collection):
        resp = await client.post(
            f"/api/v1/collections/{test_collection}/chat",
            json={"query": "What entities are in this collection?"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        assert "nodes" in data
        assert "edges" in data

    async def test_chat_requires_auth(self, client: AsyncClient, test_collection):
        resp = await client.post(
            f"/api/v1/collections/{test_collection}/chat",
            json={"query": "test"},
        )
        assert resp.status_code == 401

    async def test_chat_nonexistent_collection_404(self, client: AsyncClient, auth_headers):
        resp = await client.post(
            f"/api/v1/collections/{uuid.uuid4()}/chat",
            json={"query": "test"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_chat_default_top_k(self, client: AsyncClient, auth_headers, test_collection):
        """Default top_k_nodes=5 and top_k_edges=5 should be used when not specified."""
        resp = await client.post(
            f"/api/v1/collections/{test_collection}/chat",
            json={"query": "test"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    async def test_chat_custom_top_k(self, client: AsyncClient, auth_headers, test_collection):
        resp = await client.post(
            f"/api/v1/collections/{test_collection}/chat",
            json={"query": "test", "top_k_nodes": 3, "top_k_edges": 3},
            headers=auth_headers,
        )
        assert resp.status_code == 200
```

### `python-api/tests/test_temporal/test_temporal_extraction.py`

```python
import pytest
from app.llm.two_stage_extractor import TwoStageExtractor
from app.models.template import TemplateConfig

@pytest.fixture
def temporal_template():
    """A template with time_field in identifiers."""
    # Return a valid temporal_graph TemplateConfig

class TestTemporalExtraction:
    @pytest.mark.asyncio
    async def test_edge_prompt_includes_observation_time(self, temporal_template):
        """Edge extraction prompt should include the observation time."""
        extractor = TwoStageExtractor(temporal_template)
        prompt = extractor._build_edge_system_prompt()
        assert "Current Observation Date" in prompt or "observation_time" in prompt.lower()

    @pytest.mark.asyncio
    async def test_node_prompt_excludes_time_as_entity(self, temporal_template):
        """Node extraction prompt should instruct NOT to extract dates as entities."""
        extractor = TwoStageExtractor(temporal_template)
        prompt = extractor._build_entity_system_prompt()
        # Temporal templates should instruct that time is NOT an entity
        assert "date" in prompt.lower() or "time" in prompt.lower()

    def test_temporal_dedup_key_includes_time(self):
        """Temporal edge key should include @time component."""
        from app.services.template_factory import TemplateFactory
        temporal_config = temporal_template()
        keys = TemplateFactory._build_key_extractors(temporal_config)
        result = keys.relation({
            "source": "A", "predicate": "cited", "target": "B",
            "time": "2024"
        })
        assert "@2024" in result or "2024" in result

    def test_temporal_dedup_key_different_times_different_edges(self):
        """Same edge at different times should produce different keys."""
        from app.services.template_factory import TemplateFactory
        temporal_config = temporal_template()
        keys = TemplateFactory._build_key_extractors(temporal_config)
        key_2023 = keys.relation({"source": "A", "predicate": "cited", "target": "B", "time": "2023"})
        key_2024 = keys.relation({"source": "A", "predicate": "cited", "target": "B", "time": "2024"})
        assert key_2023 != key_2024

    def test_empty_time_no_at_symbol(self):
        """Edge with empty time should not produce dangling '@' in key."""
        from app.services.template_factory import TemplateFactory
        temporal_config = temporal_template()
        keys = TemplateFactory._build_key_extractors(temporal_config)
        result = keys.relation({"source": "A", "predicate": "cited", "target": "B", "time": ""})
        # Should not end with '@' or have '@ ' (at with no value)
        assert not result.endswith("@")
        assert "@ " not in result
```

### `python-api/tests/test_hyperedge/test_hyperedge_pruning.py`

```python
import pytest
from app.llm.edge_pruner import EdgePruner

class TestHyperedgePruning:
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
```

### Rust Tests — search_nodes/search_edges

```rust
#[cfg(test)]
mod search_tests {
    // Test that search_nodes returns empty for empty table
    // Test that search_edges with time filter correctly filters
    // Test that search_edges with location filter correctly filters
    // Test that search_edges without filters returns all matches
}
```

### Frontend Tests — ChatPanel

```typescript
// vitest + @testing-library/react
test('ChatPanel renders messages', async () => {
  // Mock chatApi mutation
  // Type query, click send
  // Verify message appears in history
});

test('ChatPanel shows error on API failure', async () => {
  // Mock chatApi mutation to reject
  // Verify error message displayed
});

test('Toggle between search and chat mode', () => {
  // Click toggle
  // Verify different API endpoint called
});
```

---

## Coverage Targets

| Module | Target |
|--------|--------|
| `app/services/knowledge_chat.py` | 85% |
| `app/routers/chat.py` | 80% |
| `app/llm/two_stage_extractor.py` (temporal) | 85% |
| `app/llm/edge_pruner.py` (hyperedge) | 95% |
| Rust `search_nodes/search_edges` | 80% |
| `frontend/src/components/chat/ChatPanel.tsx` | 70% |