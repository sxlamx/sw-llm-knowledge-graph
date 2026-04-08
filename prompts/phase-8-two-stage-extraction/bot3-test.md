# Bot 3 — Test: Phase 8 (Two-Stage Extraction)

> **Feature**: F2 (Two-Stage Extraction)
> **Spec References**: `15-hyper-extract-integration.md` Section 2.2

---

## Role

QA engineer writing comprehensive tests for the two-stage extraction pipeline.

---

## Test Frameworks

| Layer | Framework | Location |
|-------|-----------|----------|
| Python unit | pytest + pytest-asyncio | `python-api/tests/test_two_stage/` |
| Python integration | httpx.AsyncClient + respx | `python-api/tests/test_two_stage/` |
| Rust unit | cargo test | `rust-core/src/graph/builder.rs` (inline) |

---

## Test Files

### `python-api/tests/test_two_stage/test_two_stage_extractor.py`

```python
import pytest
from unittest.mock import AsyncMock, patch
from app.llm.two_stage_extractor import TwoStageExtractor
from app.models.template import TemplateConfig, EntitySchema, RelationSchema, FieldDef, FieldType, ExtractionConfig, IdentifierConfig

@pytest.fixture
def graph_template():
    return TemplateConfig(
        name="test_graph",
        type="graph",
        domain="test",
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

class TestTwoStageExtractor:
    @pytest.mark.asyncio
    async def test_extract_entities_returns_list(self, graph_template):
        """Stage 1 should return a list of entity dicts."""
        extractor = TwoStageExtractor(graph_template)
        with patch("app.llm.two_stage_extractor.call_ollama_cloud",
                    new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "content": '{"items": [{"name": "Alice", "entity_type": "Person"}]}',
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            }
            entities = await extractor.extract_entities("Alice works at Google.")
            assert len(entities) == 1
            assert entities[0]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_extract_relations_with_known_entities(self, graph_template):
        """Stage 2 should receive entity context from Stage 1."""
        extractor = TwoStageExtractor(graph_template)
        known_entities = [{"name": "Alice", "entity_type": "Person"},
                          {"name": "Google", "entity_type": "Organization"}]
        with patch("app.llm.two_stage_extractor.call_ollama_cloud",
                    new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "content": '{"items": [{"source": "Alice", "target": "Google", "predicate": "works_at"}]}',
                "usage": {"prompt_tokens": 150, "completion_tokens": 30},
            }
            relations = await extractor.extract_relations("Alice works at Google.", known_entities)
            assert len(relations) == 1
            # Verify that known entities were in the prompt
            call_args = mock_llm.call_args
            assert "Alice" in call_args[1]["user_prompt"] or "Alice" in str(call_args)

    @pytest.mark.asyncio
    async def test_empty_entities_skips_relations(self, graph_template):
        """If Stage 1 returns no entities, Stage 2 should be skipped."""
        extractor = TwoStageExtractor(graph_template)
        with patch("app.llm.two_stage_extractor.call_ollama_cloud",
                    new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "content": '{"items": []}',
                "usage": {"prompt_tokens": 50, "completion_tokens": 5},
            }
            entities, relations = await extractor.extract_two_stage("No entities here.")
            assert len(entities) == 0
            assert len(relations) == 0
            # LLM should be called only once (Stage 1)
            assert mock_llm.call_count == 1

    @pytest.mark.asyncio
    async def test_two_stage_full_pipeline(self, graph_template):
        """Full two-stage pipeline: entities then relations."""
        extractor = TwoStageExtractor(graph_template)
        with patch("app.llm.two_stage_extractor.call_ollama_cloud",
                    new_callable=AsyncMock) as mock_llm:
            # Stage 1 response
            mock_llm.side_effect = [
                {"content": '{"items": [{"name": "Alice", "entity_type": "Person"}, {"name": "Google", "entity_type": "Organization"}]}', "usage": {}},
                {"content": '{"items": [{"source": "Alice", "target": "Google", "predicate": "works_at"}]}', "usage": {}},
            ]
            entities, relations = await extractor.extract_two_stage("Alice works at Google.")
            assert len(entities) == 2
            assert len(relations) == 1
            # LLM called twice (Stage 1 + Stage 2)
            assert mock_llm.call_count == 2
```

### `python-api/tests/test_two_stage/test_edge_pruner.py`

```python
import pytest
from app.llm.edge_pruner import EdgePruner
from app.models.template import TemplateConfig

class TestEdgePruner:
    def test_prune_binary_edges_removes_dangling(self):
        """Binary edges with unknown source/target should be removed."""
        edges = [
            {"source": "Alice", "target": "Bob", "predicate": "knows"},
            {"source": "Alice", "target": "Unknown", "predicate": "mentions"},
            {"source": "Unknown2", "target": "Bob", "predicate": "mentioned_by"},
        ]
        entity_keys = {"Alice", "Bob"}
        result = EdgePruner.prune_dangling_binary(edges, entity_keys)
        assert len(result) == 1
        assert result[0]["predicate"] == "knows"

    def test_prune_binary_edges_preserves_valid(self):
        """All valid edges should be preserved."""
        edges = [
            {"source": "Alice", "target": "Bob", "predicate": "knows"},
            {"source": "Bob", "target": "Alice", "predicate": "knows"},
        ]
        entity_keys = {"Alice", "Bob"}
        result = EdgePruner.prune_dangling_binary(edges, entity_keys)
        assert len(result) == 2

    def test_prune_hyperedges_removes_dangling(self):
        """Hyperedges with unknown participants should be removed."""
        edges = [
            {"name": "meeting", "type": "event", "participants": ["Alice", "Bob", "Charlie"]},
            {"name": "party", "type": "event", "participants": ["Alice", "Unknown"]},
        ]
        entity_keys = {"Alice", "Bob", "Charlie"}
        result = EdgePruner.prune_dangling_hyperedges(edges, entity_keys, "participants")
        assert len(result) == 1
        assert result[0]["name"] == "meeting"

    def test_prune_hyperedges_all_participants_must_exist(self):
        """ALL participants must exist for a hyperedge to be valid."""
        edges = [
            {"name": "meeting", "type": "event", "participants": ["Alice", "Bob", "Unknown"]},
        ]
        entity_keys = {"Alice", "Bob"}
        result = EdgePruner.prune_dangling_hyperedges(edges, entity_keys, "participants")
        assert len(result) == 0

    def test_prune_auto_detects_hypergraph(self, graph_template_factory):
        """prune() auto-detects binary vs hypergraph based on template type."""
        binary_template = graph_template_factory(type="graph")
        hyper_template = graph_template_factory(type="hypergraph")
        binary_edges = [{"source": "A", "target": "Unknown", "predicate": "x"}]
        hyper_edges = [{"name": "e1", "type": "event", "participants": ["A", "Unknown"]}]
        entity_keys = {"A"}

        assert len(EdgePruner.prune(binary_edges, entity_keys, binary_template)) == 0
        assert len(EdgePruner.prune(hyper_edges, entity_keys, hyper_template)) == 0

    def test_empty_edges_returns_empty(self):
        """Pruning empty list returns empty."""
        result = EdgePruner.prune_dangling_binary([], {"Alice"})
        assert result == []
```

### `python-api/tests/test_two_stage/test_ollama_client.py`

```python
import pytest
from unittest.mock import AsyncMock, patch
import httpx
from app.llm.ollama_client import call_ollama_cloud

class TestOllamaCloudClient:
    @pytest.mark.asyncio
    async def test_successful_call(self):
        """Normal call returns parsed content and usage."""
        mock_response = {
            "choices": [{"message": {"content": '{"items": []}'}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10},
        }
        with patch("app.llm.ollama_client.httpx.AsyncClient") as mock_client:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=httpx.Response(200, json=mock_response))
            mock_client.return_value.__aenter__ = AsyncMock(return_value=instance)
            mock_client.return_value.__aexit__ = AsyncMock()
            result = await call_ollama_cloud("system", "user")
            assert "content" in result
            assert "usage" in result

    @pytest.mark.asyncio
    async def test_strips_code_fences(self):
        """Markdown code fences should be stripped from response."""
        mock_response = {
            "choices": [{"message": {"content": '```json\n{"items": []}\n```'}}],
            "usage": {},
        }
        with patch("app.llm.ollama_client.httpx.AsyncClient") as mock_client:
            # ... mock setup ...
            result = await call_ollama_cloud("system", "user")
            assert not result["content"].startswith("```")

    @pytest.mark.asyncio
    async def test_401_raises_error(self):
        """401 response should raise descriptive error."""

    @pytest.mark.asyncio
    async def test_429_retries(self):
        """429 response should retry up to 3 times with backoff."""

    @pytest.mark.asyncio
    async def test_missing_api_key_raises(self):
        """Missing API key should raise clear error, not silent failure."""
```

### `rust-core/src/graph/builder.rs` — Inline Rust Tests

```rust
#[cfg(test)]
mod prune_tests {
    use super::*;

    #[test]
    fn test_prune_binary_dangling_edges() {
        let mut graph = KnowledgeGraph::new(Uuid::new_v4());
        // Add nodes
        let node_a = Uuid::new_v4();
        let node_b = Uuid::new_v4();
        graph.nodes.insert(node_a, /* ... */);
        graph.nodes.insert(node_b, /* ... */);
        // Add valid edge
        let edge_valid = GraphEdge { source: node_a, target: node_b, participants: None, /* ... */ };
        let valid_id = edge_valid.id;
        graph.edges.insert(valid_id, edge_valid);
        // Add dangling edge (source doesn't exist)
        let node_fake = Uuid::new_v4();
        let edge_dangling = GraphEdge { source: node_fake, target: node_b, participants: None, /* ... */ };
        let dangling_id = edge_dangling.id;
        graph.edges.insert(dangling_id, edge_dangling);
        // Prune
        let count = prune_dangling_edges(&mut graph);
        assert_eq!(count, 1);
        assert!(graph.edges.contains_key(&valid_id));
        assert!(!graph.edges.contains_key(&dangling_id));
    }

    #[test]
    fn test_prune_hyperedge_dangling() {
        let mut graph = KnowledgeGraph::new(Uuid::new_v4());
        let node_a = Uuid::new_v4();
        let node_b = Uuid::new_v4();
        let node_fake = Uuid::new_v4();
        graph.nodes.insert(node_a, /* ... */);
        graph.nodes.insert(node_b, /* ... */);
        // Hyperedge with all valid participants
        let edge_ok = GraphEdge {
            source: node_a, target: node_b,
            participants: Some(vec![node_a, node_b]),
            /* ... */
        };
        // Hyperedge with invalid participant
        let edge_bad = GraphEdge {
            source: node_a, target: node_b,
            participants: Some(vec![node_a, node_fake]),
            /* ... */
        };
        graph.edges.insert(edge_ok.id, edge_ok);
        graph.edges.insert(edge_bad.id, edge_bad);
        let count = prune_dangling_edges(&mut graph);
        assert_eq!(count, 1);
    }

    #[test]
    fn test_prune_preserves_valid_edges() {
        // All edges reference existing nodes → none pruned
    }

    #[test]
    fn test_prune_empty_graph() {
        let mut graph = KnowledgeGraph::new(Uuid::new_v4());
        let count = prune_dangling_edges(&mut graph);
        assert_eq!(count, 0);
    }
}
```

---

## Mock Patterns

| Component | Mock |
|-----------|------|
| Ollama Cloud API | `respx` mock for `POST /chat/completions` returning structured JSON |
| Cost tracker | `AsyncMock` that tracks `record()` calls |
| TemplateConfig | Factory fixture producing valid graph/hypergraph/list templates |
| LanceDB | In-memory via `tmp_path` |
| Entity list | `[{"name": "Alice", "entity_type": "Person"}, ...]` |

---

## Coverage Targets

| Module | Target |
|--------|--------|
| `app/llm/two_stage_extractor.py` | 90% |
| `app/llm/edge_pruner.py` | 95% |
| `app/llm/ollama_client.py` | 85% |
| `rust-core/src/graph/builder.rs` (prune) | 95% |