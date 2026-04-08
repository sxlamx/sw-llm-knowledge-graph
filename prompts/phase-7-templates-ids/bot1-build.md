# Bot 1 — Build: Phase 7 (YAML Templates + Structured Identifiers & Display Labels)

> **Features**: F1 (Declarative YAML Extraction Templates) + F10 (Structured Identifiers & Display Labels)
> **Spec References**: `15-hyper-extract-integration.md` Sections 2.1, 2.10, 7.2
> **Cross-cutting**: All rules from `tasks/LESSONS.md` and `prompts/README.md`

---

## Role

You are a senior engineer implementing the template-driven extraction system and structured identifier/key framework for the sw-llm-knowledge-graph project. You write production-quality code that follows specifications exactly. You do not skip steps or leave placeholders.

---

## Project Context

This is a Rust (PyO3) + Python (FastAPI) + React knowledge graph application. The existing system extracts entities/relations via a single hardcoded LLM prompt. This phase introduces a **declarative YAML template system** so users can define what to extract, how to extract it, and how to identify/deduplicate/display the results.

**All LLM calls use Ollama Cloud API** (`settings.ollama_cloud_base_url`, OpenAI-compatible). No local Ollama, no OpenAI API key.

---

## LESSONS.md Rules (Must Follow)

- NER model: always `en_core_web_trf`, never `en_core_web_sm`
- Storage: LanceDB for all metadata, no PostgreSQL
- Embeddings: `Qwen/Qwen3-Embedding-0.6B`, 1024-dim
- NER labels: canonical only (`ORGANIZATION` not `ORG`, `LOCATION` not `GPE`)
- PyO3 names: Python imports must match Rust `#[pymodule]` block
- Lock ordering: Level 1 (atomic) → Level 2 (outer HashMap) → Level 3 (per-collection) → Level 4 (leaf)
- `crate-type = ["cdylib", "rlib"]` in Cargo.toml

---

## Implementation Tasks

### Task 1: YAML Template Config — Pydantic Models

Create `python-api/app/models/template.py`:

```python
class FieldType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    LIST = "list"

class FieldDef(BaseModel):
    name: str
    type: FieldType
    description: str
    required: bool = True
    default: Any = None

class EntitySchema(BaseModel):
    fields: List[FieldDef]
    key: str                              # e.g., "{name}"
    display_label: str                    # e.g., "{name} ({entity_type})"

class RelationSchema(BaseModel):
    fields: List[FieldDef]
    key: str                              # e.g., "{source}|{predicate}|{target}"
    source_field: str                     # e.g., "source"
    target_field: str                     # e.g., "target"
    display_label: str                    # e.g., "{predicate}"
    participants_field: Optional[str] = None  # for hypergraphs

class ExtractionConfig(BaseModel):
    mode: Literal["one_stage", "two_stage"] = "two_stage"
    node_prompt_extra: str = ""
    edge_prompt_extra: str = ""
    merge_strategy_nodes: str = "exact"
    merge_strategy_edges: str = "exact"

class IdentifierConfig(BaseModel):
    entity_key: str                        # field name for entity dedup
    relation_key: str                     # pattern for relation dedup
    relation_source: str                  # field name for source
    relation_target: str                 # field name for target
    time_field: Optional[str] = None      # F6: temporal key component
    location_field: Optional[str] = None  # F6: spatial key component

class TemplateConfig(BaseModel):
    name: str
    type: Literal["model", "list", "set", "graph", "hypergraph",
                  "temporal_graph", "spatial_graph", "spatio_temporal_graph"]
    language: List[str] = ["en"]
    domain: str = "general"
    description: str = ""
    entity_schema: Optional[EntitySchema] = None     # required for graph types
    relation_schema: Optional[RelationSchema] = None # required for graph types
    extraction: ExtractionConfig = ExtractionConfig()
    identifiers: Optional[IdentifierConfig] = None   # required for graph types
```

### Task 2: Template Gallery — YAML Loader

Create `python-api/app/services/template_gallery.py`:

```python
class TemplateGallery:
    """Singleton that loads all .yaml templates from templates/presets/."""

    _instance: Optional["TemplateGallery"] = None

    def __init__(self, presets_dir: str = "templates/presets"):
        self.presets_dir = Path(presets_dir)
        self._templates: Dict[str, TemplateConfig] = {}
        self._load_all()

    @classmethod
    def get_instance(cls) -> "TemplateGallery":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load_all(self) -> None:
        """Walk presets_dir, parse each .yaml, index by '{domain}/{name}'."""
        for yaml_file in sorted(self.presets_dir.rglob("*.yaml")):
            domain = yaml_file.parent.name
            config = self._load_file(yaml_file)
            key = f"{domain}/{config.name}"
            self._templates[key] = config

    def _load_file(self, path: Path) -> TemplateConfig:
        with open(path) as f:
            raw = yaml.safe_load(f)
        return TemplateConfig(**raw)

    def get(self, path: str) -> Optional[TemplateConfig]:
        """Get template by 'domain/name' key. Default domain is 'general'."""
        if "/" not in path:
            path = f"general/{path}"
        return self._templates.get(path)

    def list(self, domain: Optional[str] = None,
             type_filter: Optional[str] = None) -> List[TemplateConfig]:
        """List templates, optionally filtered by domain or type."""
        results = list(self._templates.values())
        if domain:
            results = [t for t in results if t.domain == domain]
        if type_filter:
            results = [t for t in results if t.type == type_filter]
        return results
```

### Task 3: Template Factory — Schema + Prompt + Key Builder

Create `python-api/app/services/template_factory.py`:

This converts a `TemplateConfig` into runtime artifacts:

```python
class TemplateFactory:
    """Converts TemplateConfig → Pydantic schemas + prompt strings + key extractors."""

    @staticmethod
    def create(config: TemplateConfig, language: str = "en") -> TemplateArtifacts:
        schema = TemplateFactory._build_schemas(config)
        prompts = TemplateFactory._build_prompts(config, language)
        keys = TemplateFactory._build_key_extractors(config)
        display = TemplateFactory._build_display_renderers(config)
        return TemplateArtifacts(
            config=config,
            entity_schema=schema.entity,
            relation_schema=schema.relation,
            node_prompt=prompts.node,
            edge_prompt=prompts.edge,
            entity_key_fn=keys.entity,
            relation_key_fn=keys.relation,
            entity_label_fn=display.entity,
            relation_label_fn=display.relation,
        )
```

**Key pattern compiler** — converts `"{source}|{predicate}|{target}"` into a callable:

```python
def _compile_key_pattern(pattern: str) -> Callable[[dict], str]:
    """Compile a key pattern like '{source}|{predicate}|{target}' into a function."""
    # Parse pattern into literal and placeholder segments
    # At runtime, format from the data dict
    def extractor(data: dict) -> str:
        return pattern.format(**{k: v for k, v in data.items() if v is not None})
    return extractor
```

**Label renderer** — same pattern but for display:

```python
def _compile_label_pattern(pattern: str) -> Callable[[dict], str]:
    def renderer(data: dict) -> str:
        try:
            return pattern.format(**data)
        except KeyError:
            return str(data.get("name", data.get("label", "unknown")))
    return renderer
```

### Task 4: Rust — KeyCompiler Module

Create `rust-core/src/graph/keys.rs`:

```rust
/// Compile a key pattern like "{source}|{predicate}|{target}@{time}"
/// into a function that extracts the key from field values.
pub struct KeyCompiler {
    segments: Vec<KeySegment>,
}

enum KeySegment {
    Literal(String),
    Field(String),
}

impl KeyCompiler {
    /// Parse a pattern string into compiled segments.
    pub fn new(pattern: &str) -> Result<Self, String> {
        // Parse "{source}|{predicate}|{target}" into segments
        // Validate that all placeholder names are valid field names
    }

    /// Render the key from a field-value map.
    pub fn render(&self, fields: &HashMap<String, String>) -> String {
        let mut result = String::new();
        for seg in &self.segments {
            match seg {
                KeySegment::Literal(s) => result.push_str(s),
                KeySegment::Field(name) => {
                    if let Some(val) = fields.get(name) {
                        result.push_str(val);
                    }
                    // Missing fields silently skipped
                }
            }
        }
        result
    }
}
```

Register in `rust-core/src/lib.rs` as a PyO3 class with `new(pattern: &str)` and `render(fields_json: &str) -> String`.

### Task 5: Rust — Extend GraphNode and GraphEdge

In `rust-core/src/models.rs`:

```rust
// ADD to GraphNode:
pub display_label: Option<String>,
pub dedup_key: Option<String>,

// ADD to GraphEdge:
pub predicate: String,                    // NEW: explicit predicate string
pub time: Option<String>,                 // NEW: temporal attribute
pub location: Option<String>,             // NEW: spatial attribute
pub participants: Option<Vec<Uuid>>,       // NEW: hyperedge participants
pub display_label: Option<String>,         // NEW: rendered label
pub dedup_key: Option<String>,            // NEW: computed dedup key
pub doc_origins: Vec<Uuid>,               // NEW: multi-document provenance
```

Update `insert_nodes_batch()` and `insert_edges_batch()` in `KnowledgeGraph` to handle the new fields. Update `SerializableGraph` JSON round-tripping.

### Task 6: PyO3 — Expose KeyCompiler and New Fields

In `rust-core/src/lib.rs`:

- Add `KeyCompiler` as a PyO3 class: `new(pattern: &str)`, `render(fields_json: &str) -> String`
- Update `upsert_nodes()` and `upsert_edges()` in `IndexManager` to parse `display_label`, `dedup_key`, `predicate`, `time`, `location`, `participants`, `doc_origins` from the JSON input
- Add `compute_dedup_key(collection_id: &str, entity_or_edge_json: &str, key_pattern: &str) -> PyResult<String>` convenience method

### Task 7: Template API Endpoints

Create `python-api/app/routers/templates.py`:

```python
@router.get("/templates")
async def list_templates(
    domain: Optional[str] = None,
    type_filter: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """List available extraction templates."""
    gallery = TemplateGallery.get_instance()
    templates = gallery.list(domain=domain, type_filter=type_filter)
    return [{"key": f"{t.domain}/{t.name}", "name": t.name,
             "domain": t.domain, "type": t.type, "description": t.description}
            for t in templates]

@router.get("/templates/{domain}/{name}")
async def get_template(
    domain: str, name: str,
    user: dict = Depends(get_current_user),
):
    """Get full template configuration (metadata only, no LLM prompts)."""
    gallery = TemplateGallery.get_instance()
    config = gallery.get(f"{domain}/{name}")
    if not config:
        raise HTTPException(404, f"Template {domain}/{name} not found")
    return _sanitize_template(config)  # Strip prompts from API response

@router.post("/templates/validate")
async def validate_template(
    template: dict,
    user: dict = Depends(require_admin),
):
    """Validate a custom template configuration."""
    try:
        TemplateConfig(**template)
        return {"valid": True}
    except ValidationError as e:
        return {"valid": False, "errors": str(e)}
```

Register in `python-api/app/main.py`.

### Task 8: General-Purpose Template YAML Files

Create the following template files:

`templates/presets/general/graph.yaml`:
- Type: `graph`
- Entity fields: `name` (str, key), `entity_type` (str), `description` (str, optional)
- Relation fields: `source` (str), `target` (str), `predicate` (str), `context` (str, optional)
- Key: entity=`{name}`, relation=`{source}|{predicate}|{target}`
- Display: entity=`{name} ({entity_type})`, relation=`{predicate}`
- Extraction: two_stage

`templates/presets/general/list.yaml`:
- Type: `list`
- Fields: `item` (str), `type` (str, optional), `description` (str, optional)
- Key: `{item}`
- Display: `{item}`

`templates/presets/general/set.yaml`:
- Type: `set`
- Fields: `name` (str, key), `entity_type` (str), `description` (str, optional)
- Key: `{name}`
- Display: `{name}`

`templates/presets/general/hypergraph.yaml`:
- Type: `hypergraph`
- Entity fields: same as graph
- Relation fields: `name` (str), `type` (str), `participants` (list), `description` (str, optional)
- Key: entity=`{name}`, relation=`{name}|{type}`
- Members: `participants`
- Display: entity=`{name} ({entity_type})`, relation=`{name}`

### Task 9: Frontend Template Picker

Create `frontend/src/components/ingest/TemplatePicker.tsx`:

- Fetches `GET /api/v1/templates` on mount
- Groups templates by domain in an Accordion
- Each template shows name, type badge, description
- Selected template key stored in Redux `collections.createTemplate`
- Passes template key to `POST /ingest/folder` body

### Task 10: Update Ingest Pipeline to Accept Template

In `python-api/app/pipeline/ingest_worker.py` and `python-api/app/routers/ingest.py`:

- Add optional `template` field to `IngestFolderRequest`
- When template is provided, load `TemplateConfig` via `TemplateGallery`
- Pass `TemplateConfig` through to `_extract_graph()` functions
- Default behavior (no template) remains unchanged

---

## Constraints

1. **No copying Hyper-Extract code**. Re-implement concepts using our data model and architecture.
2. **All LLM calls use Ollama Cloud API** via `httpx.AsyncClient` to `settings.ollama_cloud_base_url`. Never use OpenAI SDK or local Ollama.
3. **YAML templates are server-side**. The frontend only sees metadata (name, domain, type, description), never LLM prompts.
4. **Key patterns are rendered in Python** during extraction, then stored as `dedup_key` strings on nodes/edges in Rust. Rust does not need to parse templates at runtime.
5. **Display labels are rendered in Python** during extraction, then stored as `display_label` strings in Rust.
6. **TemplateConfig validation** must reject templates with missing required fields (e.g., graph type without `entity_schema`).
7. **Backward compatibility**: If no template is provided, the existing extraction pipeline runs unchanged.
8. **New Rust fields on GraphEdge** (`predicate`, `time`, `location`, `participants`, `display_label`, `dedup_key`, `doc_origins`) must be optional/nullable so existing data is not broken.

---

## Acceptance Criteria

1. `TemplateConfig` Pydantic model validates all 8 template types (model, list, set, graph, hypergraph, temporal_graph, spatial_graph, spatio_temporal_graph).
2. `TemplateGallery` loads all `.yaml` files from `templates/presets/` and indexes by `domain/name`.
3. `TemplateFactory` converts a `TemplateConfig` into Pydantic schemas, prompt strings, key extractor functions, and display label renderer functions.
4. Rust `KeyCompiler` parses a pattern string and renders keys from a field-value JSON map.
5. Rust `GraphNode` and `GraphEdge` have `display_label`, `dedup_key` fields and they serialize/deserialize correctly through PyO3.
6. `GET /api/v1/templates` returns list of templates grouped by domain.
7. `GET /api/v1/templates/{domain}/{name}` returns template metadata without LLM prompts.
8. `POST /api/v1/templates/validate` accepts and validates a custom template JSON body.
9. `templates/presets/general/graph.yaml`, `list.yaml`, `set.yaml`, `hypergraph.yaml` are valid YAML that parse into `TemplateConfig` without errors.
10. Frontend `TemplatePicker` renders template cards grouped by domain, selection stored in Redux, passed to ingest API.