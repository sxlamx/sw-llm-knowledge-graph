# Bot 3 — Test: Phase 7 (YAML Templates + Structured Identifiers & Display Labels)

> **Features**: F1 + F10
> **Spec References**: `15-hyper-extract-integration.md` Sections 2.1, 2.10, 7.2

---

## Role

You are a QA engineer writing comprehensive tests for the Phase 7 implementation. You write tests that cover all acceptance criteria and edge cases identified in the Bot 2 review.

---

## Test Frameworks

| Layer | Framework | Location |
|-------|-----------|----------|
| Python unit | pytest + pytest-asyncio | `python-api/tests/test_templates/` |
| Python API | httpx.AsyncClient + pytest | `python-api/tests/test_templates/` |
| Rust unit | cargo test | `rust-core/src/graph/keys.rs` (inline) |
| Frontend | vitest + @testing-library/react | `frontend/src/components/ingest/__tests__/` |

---

## Test Files

### `python-api/tests/test_templates/test_template_config.py`

```python
import pytest
from app.models.template import (
    TemplateConfig, EntitySchema, RelationSchema, FieldDef,
    FieldType, ExtractionConfig, IdentifierConfig
)

class TestTemplateConfigValidation:
    def test_graph_type_requires_entity_schema(self):
        """Graph type without entity_schema should raise ValidationError."""
        config = TemplateConfig(name="test", type="graph", domain="general")
        # entity_schema is required for graph type — must fail validation

    def test_graph_type_requires_relation_schema(self):
        """Graph type without relation_schema should raise ValidationError."""
        config = TemplateConfig(name="test", type="graph", domain="general",
                                entity_schema=EntitySchema(...))
        # relation_schema is required for graph type — must fail validation

    def test_graph_type_requires_identifiers(self):
        """Graph type without identifiers should raise ValidationError."""

    def test_model_type_does_not_require_entity_schema(self):
        """Model type should be valid without entity_schema."""

    def test_list_type_does_not_require_entity_schema(self):
        """List type should be valid without entity_schema."""

    def test_all_eight_types_accepted(self):
        """All 8 template types should be accepted by the type field."""
        for t in ["model", "list", "set", "graph", "hypergraph",
                  "temporal_graph", "spatial_graph", "spatio_temporal_graph"]:
            # Should not raise

    def test_invalid_type_rejected(self):
        """Unknown type value should raise ValidationError."""

    def test_duplicate_field_names_rejected(self):
        """Entity schema with duplicate field names should raise ValidationError."""

    def test_key_pattern_must_contain_placeholder(self):
        """Entity key pattern without {field} placeholder should raise ValidationError."""

    def test_merge_strategy_values(self):
        """All 7 merge strategy names should be accepted."""
        for s in ["exact", "keep_first", "keep_last", "field_overwrite",
                  "llm_balanced", "llm_prefer_first", "llm_prefer_last"]:
            # Should not raise for each as merge_strategy_nodes
```

### `python-api/tests/test_templates/test_template_gallery.py`

```python
import pytest
import yaml
from pathlib import Path
from app.services.template_gallery import TemplateGallery

class TestTemplateGallery:
    def test_load_all_general_templates(self, tmp_path):
        """Gallery should load all YAML files from presets dir."""
        # Create a mock presets dir with 2 YAML files
        general = tmp_path / "general"
        general.mkdir()
        (general / "graph.yaml").write_text(yaml.dump({
            "name": "graph", "type": "graph", "domain": "general",
            "language": ["en"], "description": "test",
            "entity_schema": {
                "fields": [{"name": "name", "type": "string", "description": "test", "required": True}],
                "key": "{name}", "display_label": "{name}"
            },
            "relation_schema": {
                "fields": [
                    {"name": "source", "type": "string", "description": "test", "required": True},
                    {"name": "target", "type": "string", "description": "test", "required": True},
                    {"name": "predicate", "type": "string", "description": "test", "required": True},
                ],
                "key": "{source}|{predicate}|{target}",
                "source_field": "source", "target_field": "target",
                "display_label": "{predicate}"
            },
            "identifiers": {
                "entity_key": "name",
                "relation_key": "{source}|{predicate}|{target}",
                "relation_source": "source",
                "relation_target": "target",
            }
        }))
        gallery = TemplateGallery(presets_dir=str(tmp_path))
        assert len(gallery._templates) == 1
        assert gallery.get("general/graph") is not None

    def test_get_nonexistent_returns_none(self, tmp_path):
        """Getting a template that doesn't exist should return None."""
        gallery = TemplateGallery(presets_dir=str(tmp_path))
        assert gallery.get("nonexistent/template") is None

    def test_get_without_domain_prefix(self, tmp_path):
        """Getting a template without domain prefix should default to 'general/'."""
        # Set up a general/graph.yaml template
        # gallery.get("graph") should be equivalent to gallery.get("general/graph")

    def test_malformed_yaml_skipped(self, tmp_path):
        """Malformed YAML files should be skipped, not crash the server."""
        general = tmp_path / "general"
        general.mkdir()
        (general / "bad.yaml").write_text("invalid: yaml: content: [")
        gallery = TemplateGallery(presets_dir=str(tmp_path))
        assert len(gallery._templates) == 0  # No crash

    def test_list_filter_by_domain(self, tmp_path):
        """list() with domain filter should return only matching templates."""

    def test_list_filter_by_type(self, tmp_path):
        """list() with type_filter should return only matching templates."""

    def test_safe_load_not_yaml_load(self):
        """Gallery must use yaml.safe_load() not yaml.load()."""
        import inspect
        source = inspect.getsource(TemplateGallery._load_file)
        assert "safe_load" in source
        assert "yaml.load(" not in source
```

### `python-api/tests/test_templates/test_template_factory.py`

```python
import pytest
from app.services.template_factory import TemplateFactory
from app.models.template import TemplateConfig

class TestKeyPatternCompiler:
    def test_simple_key(self):
        """Key '{name}' should extract the name field."""
        fn = TemplateFactory._compile_key_pattern("{name}")
        assert fn({"name": "Alice"}) == "Alice"

    def test_composite_key(self):
        """Key '{source}|{predicate}|{target}' should produce composite string."""
        fn = TemplateFactory._compile_key_pattern("{source}|{predicate}|{target}")
        result = fn({"source": "A", "predicate": "cited", "target": "B"})
        assert result == "A|cited|B"

    def test_temporal_key(self):
        """Key '{source}|{predicate}|{target}@{time}' should include time."""
        fn = TemplateFactory._compile_key_pattern("{source}|{predicate}|{target}@{time}")
        result = fn({"source": "A", "predicate": "cited", "target": "B", "time": "2024"})
        assert result == "A|cited|B@2024"

    def test_missing_field_produces_empty(self):
        """Missing field in key should produce empty string, not KeyError."""
        fn = TemplateFactory._compile_key_pattern("{source}|{predicate}|{target}")
        result = fn({"source": "A", "predicate": "cited"})
        assert result == "A|cited|"  # target is missing, empty string

    def test_key_deterministic(self):
        """Same input should always produce same output."""
        fn = TemplateFactory._compile_key_pattern("{name}")
        data = {"name": "Test"}
        assert fn(data) == fn(data)

class TestDisplayLabelRenderer:
    def test_entity_label(self):
        """Label '{name} ({entity_type})' should render correctly."""
        fn = TemplateFactory._compile_label_pattern("{name} ({entity_type})")
        result = fn({"name": "Alice", "entity_type": "Person"})
        assert result == "Alice (Person)"

    def test_missing_field_fallback(self):
        """Missing field in label should fall back gracefully."""
        fn = TemplateFactory._compile_label_pattern("{name} ({entity_type})")
        result = fn({"name": "Alice"})  # entity_type missing
        # Should not raise KeyError; should fall back

    def test_simple_label(self):
        """Label '{predicate}' should render the predicate field."""
        fn = TemplateFactory._compile_label_pattern("{predicate}")
        assert fn({"predicate": "cited"}) == "cited"
```

### `python-api/tests/test_templates/test_template_api.py`

```python
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
class TestTemplateAPI:
    async def test_list_templates(self, client: AsyncClient, auth_headers):
        """GET /templates should return 200 with template list."""
        resp = await client.get("/api/v1/templates", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        if data:
            assert "key" in data[0]
            assert "name" in data[0]
            assert "domain" in data[0]
            assert "type" in data[0]

    async def test_list_templates_filter_domain(self, client: AsyncClient, auth_headers):
        """GET /templates?domain=general should filter by domain."""
        resp = await client.get("/api/v1/templates?domain=general", headers=auth_headers)
        assert resp.status_code == 200
        for t in resp.json():
            assert t["domain"] == "general"

    async def test_get_template(self, client: AsyncClient, auth_headers):
        """GET /templates/general/graph should return 200 with metadata."""
        resp = await client.get("/api/v1/templates/general/graph", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "graph"
        assert data["domain"] == "general"

    async def test_get_template_no_prompts(self, client: AsyncClient, auth_headers):
        """Template response must NOT contain LLM prompt strings."""
        resp = await client.get("/api/v1/templates/general/graph", headers=auth_headers)
        data = resp.json()
        # Ensure no prompt-related keys in response
        assert "node_prompt" not in data
        assert "edge_prompt" not in data
        assert "node_prompt_extra" not in data

    async def test_get_nonexistent_template_404(self, client: AsyncClient, auth_headers):
        """GET /templates/nonexistent/name should return 404."""
        resp = await client.get("/api/v1/templates/nonexistent/name", headers=auth_headers)
        assert resp.status_code == 404

    async def test_validate_valid_template(self, client: AsyncClient, admin_headers):
        """POST /templates/validate with valid template should return valid=True."""
        resp = await client.post("/api/v1/templates/validate",
            json={"name": "test", "type": "model", "domain": "test"},
            headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["valid"] is True

    async def test_validate_invalid_template(self, client: AsyncClient, admin_headers):
        """POST /templates/validate with invalid template should return valid=False."""
        resp = await client.post("/api/v1/templates/validate",
            json={"name": "test", "type": "invalid_type"},
            headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["valid"] is False

    async def test_unauthenticated_rejected(self, client: AsyncClient):
        """Template endpoints should require authentication."""
        resp = await client.get("/api/v1/templates")
        assert resp.status_code == 401
```

### `rust-core/src/graph/keys.rs` — Inline Rust Tests

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_simple_key() {
        let compiler = KeyCompiler::new("{name}").unwrap();
        let mut fields = HashMap::new();
        fields.insert("name".to_string(), "Alice".to_string());
        assert_eq!(compiler.render(&fields), "Alice");
    }

    #[test]
    fn test_composite_key() {
        let compiler = KeyCompiler::new("{source}|{predicate}|{target}").unwrap();
        let mut fields = HashMap::new();
        fields.insert("source".to_string(), "A".to_string());
        fields.insert("predicate".to_string(), "cited".to_string());
        fields.insert("target".to_string(), "B".to_string());
        assert_eq!(compiler.render(&fields), "A|cited|B");
    }

    #[test]
    fn test_temporal_key() {
        let compiler = KeyCompiler::new("{source}|{predicate}|{target}@{time}").unwrap();
        let mut fields = HashMap::new();
        fields.insert("source".to_string(), "A".to_string());
        fields.insert("predicate".to_string(), "cited".to_string());
        fields.insert("target".to_string(), "B".to_string());
        fields.insert("time".to_string(), "2024".to_string());
        assert_eq!(compiler.render(&fields), "A|cited|B@2024");
    }

    #[test]
    fn test_missing_field_skipped() {
        let compiler = KeyCompiler::new("{source}|{predicate}|{target}").unwrap();
        let mut fields = HashMap::new();
        fields.insert("source".to_string(), "A".to_string());
        fields.insert("predicate".to_string(), "cited".to_string());
        // target missing — should render empty string
        assert_eq!(compiler.render(&fields), "A|cited|");
    }

    #[test]
    fn test_literal_only_pattern() {
        let compiler = KeyCompiler::new("static_key").unwrap();
        let fields = HashMap::new();
        assert_eq!(compiler.render(&fields), "static_key");
    }

    #[test]
    fn test_empty_pattern() {
        let compiler = KeyCompiler::new("").unwrap();
        let fields = HashMap::new();
        assert_eq!(compiler.render(&fields), "");
    }

    #[test]
    fn test_python_parity() {
        """Rust KeyCompiler must produce identical output to Python _compile_key_pattern()."""
        let pattern = "{source}|{predicate}|{target}@{time}";
        let compiler = KeyCompiler::new(pattern).unwrap();
        let mut fields = HashMap::new();
        fields.insert("source".to_string(), "EntityA".to_string());
        fields.insert("predicate".to_string(), "overruled".to_string());
        fields.insert("target".to_string(), "EntityB".to_string());
        fields.insert("time".to_string(), "2023-01-15".to_string());
        assert_eq!(compiler.render(&fields), "EntityA|overruled|EntityB@2023-01-15");
    }
}
```

### `frontend/src/components/ingest/__tests__/TemplatePicker.test.tsx`

```typescript
import { render, screen, fireEvent } from '@testing-library/react';
import { Provider } from 'react-redux';
import { TemplatePicker } from '../TemplatePicker';
import { setupStore } from '../../../store';

const mockTemplates = [
  { key: 'general/graph', name: 'graph', domain: 'general', type: 'graph', description: 'General graph' },
  { key: 'legal/case_law', name: 'case_law', domain: 'legal', type: 'graph', description: 'Case law' },
];

beforeEach(() => {
  global.fetch = jest.fn(() =>
    Promise.resolve({ ok: true, json: () => Promise.resolve(mockTemplates) })
  );
});

test('renders templates grouped by domain', async () => {
  render(<Provider store={setupStore()}><TemplatePicker /></Provider>);
  expect(await screen.findByText('General')).toBeInTheDocument();
  expect(await screen.findByText('Legal')).toBeInTheDocument();
});

test('selecting template updates Redux state', async () => {
  const store = setupStore();
  render(<Provider store={store}><TemplatePicker /></Provider>);
  const card = await screen.findByText('General graph');
  fireEvent.click(card);
  expect(store.getState().collections.createTemplate).toBe('general/graph');
});

test('no-template option available', async () => {
  render(<Provider store={setupStore()}><TemplatePicker /></Provider>);
  expect(await screen.findByText(/no template/i)).toBeInTheDocument();
});
```

---

## Mock Patterns

| Component | Mock |
|-----------|------|
| TemplateGallery | Use `tmp_path` fixture with minimal YAML files |
| Ollama Cloud API | `respx` mock for `POST /chat/completions` |
| LanceDB | In-memory LanceDB connection via `tmp_path` |
| HuggingFace embedder | Return `np.zeros(1024, dtype=np.float32)` |
| Frontend API | MSW handler for `GET /api/v1/templates` |
| Auth | Dev token `dev_token_1` header |

---

## Coverage Targets

| Layer | Target |
|-------|--------|
| `app/models/template.py` | 95% |
| `app/services/template_gallery.py` | 90% |
| `app/services/template_factory.py` | 90% |
| `rust-core/src/graph/keys.rs` | 95% |
| `app/routers/templates.py` | 85% |