# Bot 1 — Build: Phase 11 (Domain Template Library + Extraction Method Registry)

> **Features**: F8 (Domain Template Library) + F9 (Extraction Method Registry)
> **Spec References**: `15-hyper-extract-integration.md` Sections 2.8, 2.9
> **Depends On**: Phase 7 (templates), Phase 8 (two-stage extraction)

---

## Role

You are a senior engineer creating domain-specific extraction templates and a pluggable extraction method registry for the sw-llm-knowledge-graph project.

---

## LESSONS.md Rules

- NER model: always `en_core_web_trf`, never `en_core_web_sm`
- LanceDB for all metadata, no PostgreSQL
- All LLM calls use Ollama Cloud API via `call_ollama_cloud()`

---

## Implementation Tasks

### Task 1: Domain Templates — Legal

Create `templates/presets/legal/case_law_graph.yaml`:
- Type: `graph`
- Entity fields: `name` (party/case/statute), `entity_type` (person|court|statute|case|organization|concept), `description`
- Relation fields: `source`, `target`, `predicate` (cited|overruled|distinguished|affirmed|applied|interpreted), `context`
- Extraction mode: `two_stage`
- Merge strategy: `llm_prefer_first` for nodes (authoritative sources), `keep_first` for edges

Create `templates/presets/legal/contract_graph.yaml`:
- Type: `graph`
- Entity fields: `name`, `entity_type` (party|obligation|right|clause|asset), `description`
- Relation fields: `source`, `target`, `predicate` (owes|provides|governs|terminates|assigned_to), `context`

### Task 2: Domain Templates — Finance

Create `templates/presets/finance/company_graph.yaml`:
- Type: `graph`
- Entity fields: `name`, `entity_type` (company|person|product|market|currency), `description`
- Relation fields: `source`, `target`, `predicate` (owns|invests_in|supplies|compete_with|acquired)

Create `templates/presets/finance/transaction_temporal.yaml`:
- Type: `temporal_graph`
- Entity fields: `name`, `entity_type` (company|person|asset|currency), `description`
- Relation fields: `source`, `target`, `predicate`, `time`, `context`
- Identifiers: `time_field: time`

### Task 3: Domain Templates — Medical

Create `templates/presets/medical/clinical_graph.yaml`:
- Type: `temporal_graph`
- Entity fields: `name`, `entity_type` (patient|condition|procedure|medication|facility|lab_result), `description`
- Relation fields: `source`, `target`, `predicate` (diagnosed_with|treated_with|refers_to|causes|contraindicates), `time`, `context`
- Extraction: `node_prompt_extra: "Focus on clinical entities: conditions, procedures, medications, and their relationships."`, `edge_prompt_extra: "Only extract explicitly stated clinical relationships. Use standard medical terminology."`

Create `templates/presets/medical/drug_interaction.yaml`:
- Type: `graph`
- Entity fields: `name`, `entity_type` (drug|condition|enzyme|receptor), `description`
- Relation fields: `source`, `target`, `predicate` (interacts_with|inhibits|metabolized_by|contraindicated_with), `context`

### Task 4: Domain Templates — Industry

Create `templates/presets/industry/supply_chain.yaml`:
- Type: `spatio_temporal_graph`
- Entity fields: `name`, `entity_type` (supplier|product|warehouse|port|vehicle), `description`
- Relation fields: `source`, `target`, `predicate` (supplies|stores_at|ships_via|delivers_to), `time`, `location`, `context`
- Identifiers: `time_field: time`, `location_field: location`

Create `templates/presets/industry/workflow.yaml`:
- Type: `graph`
- Entity fields: `name`, `entity_type` (step|decision|input|output|resource), `description`
- Relation fields: `source`, `target`, `predicate` (followed_by|depends_on|produces|requires), `condition` (optional — for decision branches)

### Task 5: Domain Templates — General (Extended)

Create `templates/presets/general/biography_graph.yaml`:
- Type: `temporal_graph`
- Entity fields: `name`, `entity_type` (person|organization|location|achievement|education), `description`
- Relation fields: `source`, `target`, `predicate` (born_in|educated_at|worked_at|achieved|appointed_to), `time`, `context`

Create `templates/presets/general/concept_graph.yaml`:
- Type: `graph`
- Entity fields: `name`, `entity_type` (concept|theory|principle|method|tool), `definition`
- Relation fields: `source`, `target`, `predicate` (is_a|part_of|causes|related_to|contradicts), `context`

### Task 6: Extraction Method Registry

Create `python-api/app/services/extraction_registry.py`:

```python
from typing import Protocol, Dict, Type
from dataclasses import dataclass

@dataclass
class MethodInfo:
    name: str
    auto_type: str  # "graph", "hypergraph", etc.
    description: str

class ExtractionMethod(Protocol):
    name: str
    auto_type: str
    description: str

    async def extract(self, chunk_text: str, template: TemplateConfig,
                      **kwargs) -> Tuple[List[dict], List[dict]]:
        """Extract entities and relations from text using this method."""
        ...

class StandardExtractor:
    """Standard single-stage extraction (existing behavior)."""
    name = "standard"
    auto_type = "graph"
    description = "Single-stage entity and relation extraction."

    async def extract(self, chunk_text: str, template: TemplateConfig,
                      **kwargs) -> Tuple[List[dict], List[dict]]:
        from app.llm.extractor import extract_from_chunk
        result = await extract_from_chunk(chunk_text)
        return result.get("entities", []), result.get("relationships", [])

class TwoStageExtractor:
    """Two-stage extraction: entities first, then relations with context."""
    name = "two_stage"
    auto_type = "graph"
    description = "Two-stage extraction: entities first, then edges with entity context."

    async def extract(self, chunk_text: str, template: TemplateConfig,
                      **kwargs) -> Tuple[List[dict], List[dict]]:
        from app.llm.two_stage_extractor import TwoStageExtractor as TSE
        extractor = TSE(template)
        return await extractor.extract_two_stage(chunk_text)


class ExtractionRegistry:
    """Registry of available extraction methods."""
    _methods: Dict[str, ExtractionMethod] = {}

    @classmethod
    def register(cls, method: ExtractionMethod) -> None:
        cls._methods[method.name] = method

    @classmethod
    def get(cls, name: str) -> ExtractionMethod | None:
        return cls._methods.get(name)

    @classmethod
    def list(cls) -> list[MethodInfo]:
        return [MethodInfo(name=m.name, auto_type=m.auto_type, description=m.description)
                for m in cls._methods.values()]

# Auto-register built-in methods
ExtractionRegistry.register(StandardExtractor())
ExtractionRegistry.register(TwoStageExtractor())
```

### Task 7: Extraction Method API Endpoint

In `python-api/app/routers/templates.py`, add:

```python
@router.get("/extraction-methods")
async def list_extraction_methods(
    user: dict = Depends(get_current_user),
):
    """List available extraction methods."""
    methods = ExtractionRegistry.list()
    return [{"name": m.name, "auto_type": m.auto_type, "description": m.description}
            for m in methods]
```

### Task 8: Template Authoring Guide

Create `templates/AUTHORING_GUIDE.md`:

- Template structure explanation (name, type, entity_schema, relation_schema, extraction, identifiers)
- Field type reference (string, integer, float, boolean, list)
- Key pattern syntax (`{field_name}` placeholders)
- Template type decision tree (model/list/set for no-relations, graph for binary, hypergraph for n-ary, temporal/spatial for time/location)
- Custom template validation via `POST /templates/validate`
- Best practices: max 5 fields per schema, bilingual descriptions, explicit predicate lists

### Task 9: Frontend — Method Selector

In `frontend/src/components/ingest/TemplatePicker.tsx`, add extraction method selector:

- Dropdown showing available methods (fetched from `GET /api/v1/templates/extraction-methods`)
- Auto-selects `two_stage` when template type is hypergraph or temporal
- Selection stored in Redux alongside template key

---

## Constraints

1. **Domain templates are authored from scratch** — use Hyper-Extract's presets as reference for field choices but write our own YAML with our schema format.
2. **Every template must validate** against `TemplateConfig` — test with `POST /templates/validate` before committing.
3. **Template prompts should NOT contain code** — only natural language instructions for the LLM.
4. **extraction.mode** in every template must be explicitly set (no implicit default at template level).
5. **Legal and medical templates** must emphasize precision ("only extract explicitly stated relationships") to avoid hallucinated edges.
6. **ExtractionRegistry** is extensible — future methods (graph_rag, light_rag) can be registered without modifying existing code.
7. **Method auto_type** must match the template type — a "hypergraph" method should not be used with a "graph" template.

---

## Acceptance Criteria

1. 12+ YAML template files exist in `templates/presets/` across 5 domains (general, legal, finance, medical, industry).
2. Every YAML template parses into `TemplateConfig` without errors (verified by `TemplateGallery` load).
3. `ExtractionRegistry` has 2 built-in methods: `standard` and `two_stage`.
4. `GET /templates/extraction-methods` returns list of available methods.
5. Template Picker shows domain templates grouped by domain and method selector dropdown.
6. Legal templates use `llm_prefer_first` merge strategy for nodes and `keep_first` for edges.
7. Temporal templates include `time_field` in identifiers and temporal prompt rules.
8. Spatial templates include `location_field` in identifiers and spatial prompt rules.
9. `AUTHORING_GUIDE.md` documents template structure, types, and best practices.
10. Method auto_type compatibility is checked before extraction (e.g., hypergraph method with graph template raises error).