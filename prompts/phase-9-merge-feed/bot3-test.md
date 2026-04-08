# Bot 3 — Test: Phase 9 (Merge Strategies + Incremental Feeding)

> **Features**: F3 + F4

---

## Test Files

### `python-api/tests/test_merge/test_merge_strategy.py`

```python
import pytest
from app.services.merge_strategy import MergeStrategy

class TestMergeStrategyEnum:
    def test_all_seven_strategies_exist(self):
        strategies = [s.value for s in MergeStrategy]
        assert "exact" in strategies
        assert "keep_first" in strategies
        assert "keep_last" in strategies
        assert "field_overwrite" in strategies
        assert "llm_balanced" in strategies
        assert "llm_prefer_first" in strategies
        assert "llm_prefer_last" in strategies

    def test_is_deterministic_property(self):
        assert MergeStrategy.EXACT.is_deterministic is True
        assert MergeStrategy.KEEP_FIRST.is_deterministic is True
        assert MergeStrategy.KEEP_LAST.is_deterministic is True
        assert MergeStrategy.FIELD_OVERWRITE.is_deterministic is True
        assert MergeStrategy.LLM_BALANCED.is_deterministic is False
        assert MergeStrategy.LLM_PREFER_FIRST.is_deterministic is False
        assert MergeStrategy.LLM_PREFER_LAST.is_deterministic is False

    def test_rust_strategy_name(self):
        assert MergeStrategy.EXACT.rust_strategy_name is None
        assert MergeStrategy.KEEP_FIRST.rust_strategy_name == "keep_first"
        assert MergeStrategy.KEEP_LAST.rust_strategy_name == "keep_last"
        assert MergeStrategy.FIELD_OVERWRITE.rust_strategy_name == "field_overwrite"
        assert MergeStrategy.LLM_BALANCED.rust_strategy_name is None
```

### `python-api/tests/test_merge/test_entity_merger.py`

```python
import pytest
from unittest.mock import AsyncMock, patch
from app.services.entity_merger import EntityMerger
from app.services.merge_strategy import MergeStrategy

@pytest.fixture
def merger():
    return EntityMerger(template=mock_template())

class TestDeterministicMerge:
    def test_keep_first_returns_existing(self, merger):
        existing = {"id": "1", "name": "Alice", "description": "Original"}
        incoming = {"id": "2", "name": "Alice", "description": "Updated"}
        result = merger.merge(existing, incoming, MergeStrategy.KEEP_FIRST, "node")
        assert result["description"] == "Original"
        assert result["id"] == "1"

    def test_keep_last_returns_incoming_with_existing_id(self, merger):
        existing = {"id": "1", "name": "Alice", "description": "Original"}
        incoming = {"id": "2", "name": "Alice", "description": "Updated"}
        result = merger.merge(existing, incoming, MergeStrategy.KEEP_LAST, "node")
        assert result["description"] == "Updated"
        assert result["id"] == "1"  # Canonical ID preserved

    def test_field_overwrite_fills_nulls(self, merger):
        existing = {"id": "1", "name": "Alice", "description": None}
        incoming = {"id": "2", "name": "Alice", "description": "A person"}
        result = merger.merge(existing, incoming, MergeStrategy.FIELD_OVERWRITE, "node")
        assert result["description"] == "A person"

    def test_field_overwrite_appends_lists(self, merger):
        existing = {"id": "1", "name": "Alice", "aliases": ["Al"]}
        incoming = {"id": "2", "name": "Alice", "aliases": ["Alice Smith"]}
        result = merger.merge(existing, incoming, MergeStrategy.FIELD_OVERWRITE, "node")
        assert "Al" in result["aliases"]
        assert "Alice Smith" in result["aliases"]

    def test_field_overwrite_no_duplicate_aliases(self, merger):
        existing = {"id": "1", "name": "Alice", "aliases": ["Al", "Ali"]}
        incoming = {"id": "2", "name": "Alice", "aliases": ["Al", "Alicia"]}
        result = merger.merge(existing, incoming, MergeStrategy.FIELD_OVERWRITE, "node")
        assert result["aliases"].count("Al") == 1  # No duplicates

    def test_exact_returns_existing_unchanged(self, merger):
        existing = {"id": "1", "name": "Alice"}
        incoming = {"id": "2", "name": "Alice", "description": "New info"}
        result = merger.merge(existing, incoming, MergeStrategy.EXACT, "node")
        assert result == existing  # Exact: keep existing, ignore incoming

    def test_field_overwrite_averages_confidence(self, merger):
        existing = {"id": "1", "name": "Alice", "confidence": 0.8}
        incoming = {"id": "2", "name": "Alice", "confidence": 0.6}
        result = merger.merge(existing, incoming, MergeStrategy.FIELD_OVERWRITE, "node")
        assert abs(result["confidence"] - 0.7) < 0.01

    def test_canonical_id_always_preserved(self, merger):
        """Regardless of strategy, the canonical (existing) ID must be preserved."""
        for strategy in [MergeStrategy.KEEP_LAST, MergeStrategy.FIELD_OVERWRITE]:
            existing = {"id": "uuid-aaa", "name": "Alice"}
            incoming = {"id": "uuid-bbb", "name": "Alice"}
            result = merger.merge(existing, incoming, strategy, "node")
            assert result["id"] == "uuid-aaa"

class TestLLMMerge:
    @pytest.mark.asyncio
    async def test_llm_balanced_merge(self, merger):
        """LLM_BALANCED should call Ollama Cloud and return merged result."""
        existing = {"id": "1", "name": "Alice", "description": "A software engineer"}
        incoming = {"id": "2", "name": "Alice", "description": "Works at Google"}
        with patch("app.services.entity_merger.call_ollama_cloud",
                    new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "content": '{"id": "1", "name": "Alice", "description": "A software engineer who works at Google"}',
                "usage": {"prompt_tokens": 200, "completion_tokens": 50},
            }
            result = await merger.merge(existing, incoming, MergeStrategy.LLM_BALANCED, "node")
            assert result["id"] == "1"  # Canonical ID preserved
            assert "Google" in result["description"] or "engineer" in result["description"]

    @pytest.mark.asyncio
    async def test_llm_merge_preserves_canonical_id(self, merger):
        """LLM must not overwrite the canonical UUID."""
        existing = {"id": "1", "name": "Alice"}
        incoming = {"id": "2", "name": "Alice"}
        with patch("app.services.entity_merger.call_ollama_cloud",
                    new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "content": '{"id": "2", "name": "Alice"}',  # LLM tries to use incoming ID
                "usage": {},
            }
            result = await merger.merge(existing, incoming, MergeStrategy.LLM_PREFER_FIRST, "node")
            assert result["id"] == "1"  # Must be canonical (existing)

    @pytest.mark.asyncio
    async def test_llm_merge_fallback_on_error(self, merger):
        """LLM merge failure should fall back to KEEP_FIRST."""
        existing = {"id": "1", "name": "Alice", "description": "Original"}
        incoming = {"id": "2", "name": "Alice", "description": "Updated"}
        with patch("app.services.entity_merger.call_ollama_cloud",
                    new_callable=AsyncMock, side_effect=Exception("API error")):
            result = await merger.merge(existing, incoming, MergeStrategy.LLM_BALANCED, "node")
            # Fallback to KEEP_FIRST
            assert result["description"] == "Original"
```

### `rust-core/src/graph/merge.rs` — Inline Rust Tests

```rust
#[cfg(test)]
mod tests {
    use super::*;

    fn make_node(id: Uuid, label: &str, desc: Option<&str>, aliases: Vec<&str>, confidence: f32) -> GraphNode {
        GraphNode {
            id, node_type: NodeType::Person, label: label.to_string(),
            description: desc.map(|s| s.to_string()), aliases: aliases.iter().map(|s| s.to_string()).collect(),
            confidence, ontology_class: None, properties: HashMap::new(),
            collection_id: Uuid::new_v4(), display_label: None, dedup_key: None,
            created_at: None, updated_at: None,
        }
    }

    #[test]
    fn test_keep_first_preserves_existing() {
        let existing = make_node(Uuid::new_v4(), "Alice", Some("Original"), vec![], 0.8);
        let incoming = make_node(Uuid::new_v4(), "Alice", Some("Updated"), vec![], 0.9);
        let result = merge_nodes_deterministic(&existing, &incoming, &DeterministicMergeStrategy::KeepFirst);
        assert_eq!(result.description, Some("Original".to_string()));
        assert_eq!(result.id, existing.id); // Canonical ID preserved
    }

    #[test]
    fn test_keep_last_preserves_id() {
        let existing_id = Uuid::new_v4();
        let existing = make_node(existing_id, "Alice", Some("Original"), vec![], 0.8);
        let incoming = make_node(Uuid::new_v4(), "Alice", Some("Updated"), vec![], 0.9);
        let result = merge_nodes_deterministic(&existing, &incoming, &DeterministicMergeStrategy::KeepLast);
        assert_eq!(result.id, existing_id); // ID preserved
        assert_eq!(result.description, Some("Updated".to_string()));
    }

    #[test]
    fn test_field_overwrite_fills_nulls() {
        let existing = make_node(Uuid::new_v4(), "Alice", None, vec![], 0.8);
        let incoming = make_node(Uuid::new_v4(), "Alice", Some("A person"), vec![], 0.6);
        let result = merge_nodes_deterministic(&existing, &incoming, &DeterministicMergeStrategy::FieldOverwrite);
        assert_eq!(result.description, Some("A person".to_string()));
    }

    #[test]
    fn test_field_overwrite_appends_aliases() {
        let existing = make_node(Uuid::new_v4(), "Alice", Some("Original"), vec!["Al"], 0.8);
        let incoming = make_node(Uuid::new_v4(), "Alice", Some("Updated"), vec!["Ali"], 0.6);
        let result = merge_nodes_deterministic(&existing, &incoming, &DeterministicMergeStrategy::FieldOverwrite);
        assert!(result.aliases.contains(&"Al".to_string()));
        assert!(result.aliases.contains(&"Ali".to_string()));
    }

    #[test]
    fn test_field_overwrite_averages_confidence() {
        let existing = make_node(Uuid::new_v4(), "Alice", None, vec![], 0.8);
        let incoming = make_node(Uuid::new_v4(), "Alice", None, vec![], 0.6);
        let result = merge_nodes_deterministic(&existing, &incoming, &DeterministicMergeStrategy::FieldOverwrite);
        assert!((result.confidence - 0.7).abs() < 0.01);
    }

    #[test]
    fn test_does_not_mutate_inputs() {
        let desc = "Original".to_string();
        let mut existing = make_node(Uuid::new_v4(), "Alice", Some(desc.clone()), vec![], 0.8);
        let incoming = make_node(Uuid::new_v4(), "Alice", Some("Updated".to_string()), vec![], 0.9);
        let _result = merge_nodes_deterministic(&existing, &incoming, &DeterministicMergeStrategy::KeepLast);
        // Original should be unchanged
        assert_eq!(existing.description, Some("Original".to_string()));
    }

    #[test]
    fn test_detect_no_conflicts() {
        let existing = vec![make_node(Uuid::new_v4(), "Alice", Some("Original"), vec![], 0.8)];
        let incoming = vec![make_node(Uuid::new_v4(), "Bob", Some("New person"), vec![], 0.7)];
        let conflicts = detect_node_conflicts(&existing, &incoming);
        assert!(conflicts.is_empty()); // No dedup_key overlap = no conflicts
    }
}
```

### `python-api/tests/test_merge/test_feed_endpoint.py`

```python
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
class TestFeedEndpoint:
    async def test_feed_creates_job(self, client: AsyncClient, auth_headers, test_collection):
        """POST /collections/{id}/feed should create an ingest job."""
        resp = await client.post(
            f"/api/v1/collections/{test_collection}/feed",
            json={"file_paths": ["/path/to/new/doc.pdf"]},
            headers=auth_headers,
        )
        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data

    async def test_feed_with_template(self, client: AsyncClient, auth_headers, test_collection):
        """POST /collections/{id}/feed with template should route through two-stage extraction."""
        resp = await client.post(
            f"/api/v1/collections/{test_collection}/feed",
            json={"file_paths": ["/path/to/new/doc.pdf"], "template": "general/graph"},
            headers=auth_headers,
        )
        assert resp.status_code == 202

    async def test_feed_requires_auth(self, client: AsyncClient, test_collection):
        """POST /collections/{id}/feed should require authentication."""
        resp = await client.post(
            f"/api/v1/collections/{test_collection}/feed",
            json={"file_paths": ["/path/to/new/doc.pdf"]},
        )
        assert resp.status_code == 401

    async def test_feed_nonexistent_collection(self, client: AsyncClient, auth_headers):
        """POST /collections/{bad_id}/feed should return 404."""
        resp = await client.post(
            f"/api/v1/collections/{uuid.uuid4()}/feed",
            json={"file_paths": ["/path/to/new/doc.pdf"]},
            headers=auth_headers,
        )
        assert resp.status_code == 404
```

---

## Coverage Targets

| Module | Target |
|--------|--------|
| `app/services/merge_strategy.py` | 100% |
| `app/services/entity_merger.py` | 90% |
| `rust-core/src/graph/merge.rs` | 95% |
| `app/routers/ingest.py` (feed endpoint) | 85% |