# Bot 3 — Test: Phase 11 (Domain Template Library + Extraction Method Registry)

> **Features**: F8 + F9

---

## Test Files

### `python-api/tests/test_templates/test_template_loading.py`

```python
import pytest
from pathlib import Path
from app.services.template_gallery import TemplateGallery
from app.models.template import TemplateConfig

PRESETS_DIR = Path("templates/presets")

class TestTemplateLoading:
    def test_all_yaml_files_parse(self):
        """Every YAML file in presets/ should parse into a valid TemplateConfig."""
        gallery = TemplateGallery(presets_dir=str(PRESETS_DIR))
        errors = []
        for key, config in gallery._templates.items():
            try:
                TemplateConfig(**config.dict() if hasattr(config, 'dict') else config.model_dump())
            except Exception as e:
                errors.append(f"{key}: {e}")
        assert len(errors) == 0, f"Template parsing errors: {errors}"

    def test_template_count_minimum(self):
        """At least 12 templates should load from presets/."""
        gallery = TemplateGallery(presets_dir=str(PRESETS_DIR))
        assert len(gallery._templates) >= 12

    def test_all_domains_present(self):
        """general, legal, finance, medical, industry domains should have templates."""
        gallery = Gallery(presets_dir=str(PRESETS_DIR))
        domains = set(t.domain for t in gallery._templates.values())
        for expected in ["general", "legal", "finance", "medical", "industry"]:
            assert expected in domains, f"Missing domain: {expected}"

    def test_graph_templates_have_entity_schema(self):
        """All graph-type templates must have entity_schema."""
        gallery = Gallery(presets_dir=str(PRESETS_DIR))
        for key, config in gallery._templates.items():
            if config.type in ("graph", "hypergraph", "temporal_graph", "spatial_graph", "spatio_temporal_graph"):
                assert config.entity_schema is not None, f"{key}: missing entity_schema"
                assert config.relation_schema is not None, f"{key}: missing relation_schema"

    def test_hypergraph_has_participants_field(self):
        """Hypergraph templates must have participants_field."""
        gallery = Gallery(presets_dir=str(PRESETS_DIR))
        for key, config in gallery._templates.items():
            if config.type == "hypergraph":
                assert config.relation_schema.participants_field is not None, \
                    f"{key}: hypergraph missing participants_field"

    def test_temporal_has_time_field(self):
        """Temporal templates must have time_field in identifiers."""
        gallery = Gallery(presets_dir=str(PRESETS_DIR))
        for key, config in gallery._templates.items():
            if config.type in ("temporal_graph", "spatio_temporal_graph"):
                assert config.identifiers is not None, f"{key}: missing identifiers"
                assert config.identifiers.time_field is not None, f"{key}: missing time_field"

    def test_spatial_has_location_field(self):
        """Spatial templates must have location_field in identifiers."""
        gallery = Gallery(presets_dir=str(PRESETS_DIR))
        for key, config in gallery._templates.items():
            if config.type in ("spatial_graph", "spatio_temporal_graph"):
                assert config.identifiers is not None
                assert config.identifiers.location_field is not None

    def test_all_templates_have_extraction_mode(self):
        """Every template must explicitly set extraction.mode."""
        gallery = Gallery(presets_dir=str(PRESETS_DIR))
        for key, config in gallery._templates.items():
            assert config.extraction.mode in ("one_stage", "two_stage"), \
                f"{key}: invalid extraction mode '{config.extraction.mode}'"

    def test_legal_templates_precision_prompts(self):
        """Legal templates should include precision instructions."""
        gallery = Gallery(presets_dir=str(PRESETS_DIR))
        for key, config in gallery._templates.items():
            if config.domain == "legal":
                assert config.extraction.edge_prompt_extra, f"{key}: legal template missing edge_prompt_extra"
                assert "explicitly" in config.extraction.edge_prompt_extra.lower() or \
                       "only" in config.extraction.edge_prompt_extra.lower()
```

### `python-api/tests/test_templates/test_extraction_registry.py`

```python
import pytest
from app.services.extraction_registry import ExtractionRegistry, StandardExtractor, TwoStageExtractor

class TestExtractionRegistry:
    def test_register_and_get(self):
        """Register a method, retrieve it by name."""
        # Standard and TwoStage already registered at import time
        method = ExtractionRegistry.get("standard")
        assert method is not None
        assert method.name == "standard"

    def test_get_nonexistent_returns_none(self):
        """Getting a non-existent method should return None."""
        assert ExtractionRegistry.get("nonexistent") is None

    def test_list_methods(self):
        """list() should return MethodInfo for all registered methods."""
        methods = ExtractionRegistry.list()
        names = [m.name for m in methods]
        assert "standard" in names
        assert "two_stage" in names

    def test_standard_extractor_auto_type(self):
        """StandardExtractor auto_type should be 'graph'."""
        method = ExtractionRegistry.get("standard")
        assert method.auto_type == "graph"

    def test_two_stage_extractor_auto_type(self):
        """TwoStageExtractor auto_type should be 'graph'."""
        method = ExtractionRegistry.get("two_stage")
        assert method.auto_type == "graph"

    def test_method_info_fields(self):
        """MethodInfo should have name, auto_type, description."""
        methods = ExtractionRegistry.list()
        for m in methods:
            assert m.name
            assert m.auto_type
            assert m.description
```

### `python-api/tests/test_templates/test_api_endpoints.py`

```python
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
class TestTemplateAndMethodAPIs:
    async def test_list_templates(self, client: AsyncClient, auth_headers):
        """GET /templates should return templates from all domains."""
        resp = await client.get("/api/v1/templates", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 12  # minimum template count

    async def test_filter_by_domain(self, client: AsyncClient, auth_headers):
        """GET /templates?domain=legal should return only legal templates."""
        resp = await client.get("/api/v1/templates?domain=legal", headers=auth_headers)
        assert resp.status_code == 200
        for t in resp.json():
            assert t["domain"] == "legal"

    async def test_filter_by_type(self, client: AsyncClient, auth_headers):
        """GET /templates?type=graph should return only graph templates."""
        resp = await client.get("/api/v1/templates?type=graph", headers=auth_headers)
        assert resp.status_code == 200
        for t in resp.json():
            assert t["type"] == "graph"

    async def test_get_legal_template(self, client: AsyncClient, auth_headers):
        """GET /templates/legal/case_law_graph should return the case law template."""
        resp = await client.get("/api/v1/templates/legal/case_law_graph", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "case_law_graph"
        assert data["domain"] == "legal"

    async def test_get_medical_template(self, client: AsyncClient, auth_headers):
        """GET /templates/medical/clinical_graph should return the clinical template."""
        resp = await client.get("/api/v1/templates/medical/clinical_graph", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "temporal_graph"

    async def test_template_no_prompts_exposed(self, client: AsyncClient, auth_headers):
        """Template API should NOT expose LLM prompt strings."""
        resp = await client.get("/api/v1/templates/general/graph", headers=auth_headers)
        data = resp.json()
        assert "node_prompt" not in data
        assert "edge_prompt" not in data

    async def test_extraction_methods_endpoint(self, client: AsyncClient, auth_headers):
        """GET /templates/extraction-methods should return built-in methods."""
        resp = await client.get("/api/v1/templates/extraction-methods", headers=auth_headers)
        assert resp.status_code == 200
        methods = resp.json()
        names = [m["name"] for m in methods]
        assert "standard" in names
        assert "two_stage" in names

    async def test_validate_valid_template(self, client: AsyncClient, admin_headers):
        """POST /templates/validate with valid template should return valid=True."""
        resp = await client.post("/api/v1/templates/validate",
            json={"name": "test", "type": "model", "domain": "test"},
            headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["valid"] is True

    async def test_validate_invalid_template(self, client: AsyncClient, admin_headers):
        """POST /templates/validate with invalid type should return valid=False."""
        resp = await client.post("/api/v1/templates/validate",
            json={"name": "test", "type": "invalid_type", "domain": "test"},
            headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["valid"] is False
```

### Template Validation Integration Tests

```python
import yaml
import pytest
from pathlib import Path

PRESETS_DIR = Path("templates/presets")

class TestTemplateYAMLValidity:
    @pytest.mark.parametrize("yaml_file", sorted(PRESETS_DIR.rglob("*.yaml")))
    def test_yaml_parses(self, yaml_file):
        """Every YAML file should parse without syntax errors."""
        with open(yaml_file) as f:
            data = yaml.safe_load(f)
        assert data is not None
        assert "name" in data
        assert "type" in data

    @pytest.mark.parametrize("yaml_file", sorted(PRESETS_DIR.rglob("*.yaml")))
    def test_template_config_validates(self, yaml_file):
        """Every YAML file should validate as a TemplateConfig."""
        with open(yaml_file) as f:
            data = yaml.safe_load(f)
        config = TemplateConfig(**data)
        assert config.name
        assert config.type in ("model", "list", "set", "graph", "hypergraph",
                              "temporal_graph", "spatial_graph", "spatio_temporal_graph")

    @pytest.mark.parametrize("yaml_file", sorted(PRESETS_DIR.rglob("*.yaml")))
    def test_graph_templates_have_schemas(self, yaml_file):
        """Graph-type templates must have entity_schema and relation_schema."""
        with open(yaml_file) as f:
            data = yaml.safe_load(f)
        if data.get("type") in ("graph", "hypergraph", "temporal_graph",
                                 "spatial_graph", "spatio_temporal_graph"):
            assert "entity_schema" in data, f"{yaml_file}: graph type missing entity_schema"
            assert "relation_schema" in data, f"{yaml_file}: graph type missing relation_schema"

    @pytest.mark.parametrize("yaml_file", sorted(PRESETS_DIR.rglob("*.yaml")))
    def test_field_count_reasonable(self, yaml_file):
        """Entity and relation schemas should have <= 5 fields each."""
        with open(yaml_file) as f:
            data = yaml.safe_load(f)
        if "entity_schema" in data:
            assert len(data["entity_schema"]["fields"]) <= 5, \
                f"{yaml_file}: too many entity fields"
        if "relation_schema" in data:
            assert len(data["relation_schema"]["fields"]) <= 5, \
                f"{yaml_file}: too many relation fields"
```

---

## Coverage Targets

| Module | Target |
|--------|--------|
| `app/services/extraction_registry.py` | 95% |
| `app/services/template_gallery.py` (new templates) | 90% |
| `app/routers/templates.py` (methods endpoint) | 85% |
| Template YAML validity | 100% (all files) |