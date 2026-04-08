# Bot 2 — Review: Phase 7 (YAML Templates + Structured Identifiers & Display Labels)

> **Features**: F1 + F10
> **Spec References**: `15-hyper-extract-integration.md` Sections 2.1, 2.10, 7.2, 7.5

---

## Role

You are a senior code reviewer. Your job is to audit the Phase 7 implementation for spec compliance, correctness, security, and data integrity. You find bugs and fix deviations from the specification.

---

## Reference Documents

- `specifications/15-hyper-extract-integration.md` (primary)
- `specifications/02-data-models.md` (data model additions)
- `specifications/08-api-design.md` (new endpoints)
- `specifications/12-project-structure.md` (file locations)
- `tasks/LESSONS.md` (past mistakes)
- `prompts/README.md` (cross-cutting rules)

---

## Review Checklist

### A. TemplateConfig Pydantic Model

- [ ] `type` field accepts all 8 values: model, list, set, graph, hypergraph, temporal_graph, spatial_graph, spatio_temporal_graph
- [ ] `entity_schema` is required when `type` is any graph variant (graph, hypergraph, temporal_graph, spatial_graph, spatio_temporal_graph)
- [ ] `relation_schema` is required when `type` is any graph variant
- [ ] `identifiers` is required when `type` is any graph variant
- [ ] `merge_strategy_nodes` and `merge_strategy_edges` only accept valid strategy names: exact, keep_first, keep_last, field_overwrite, llm_balanced, llm_prefer_first, llm_prefer_last
- [ ] `key` pattern contains at least one `{field}` placeholder
- [ ] `display_label` pattern contains at least one `{field}` placeholder
- [ ] Field `name` values are unique within each schema (no duplicate field names)
- [ ] All `required: true` fields have no `default` value
- **Severity**: HIGH — invalid templates produce silent extraction failures

### B. TemplateGallery Loader

- [ ] Gallery is a true singleton (not re-created on each request)
- [ ] YAML parsing uses `yaml.safe_load()` (not `yaml.load` — arbitrary code execution risk)
- [ ] Malformed YAML files are caught and logged, not crashing the server
- [ ] Template key is `{domain}/{name}` not `{domain}/{filename_without_ext}` (name comes from YAML `name` field)
- [ ] Templates in sub-directories of `presets/` are discovered (recursive glob)
- [ ] Gallery reload is NOT automatic on file change (stale data is acceptable; restart for updates)
- **Severity**: MEDIUM for safe_load (security), LOW for others

### C. TemplateFactory — Schema + Prompt + Key Building

- [ ] `_compile_key_pattern("{source}|{predicate}|{target}")` produces a callable that returns the correct string
- [ ] Key pattern with missing field data does not raise KeyError — missing fields are replaced with empty string
- [ ] Display label pattern with missing field data falls back gracefully (e.g., to "unknown")
- [ ] Entity key function produces deterministic output for the same input
- [ ] Relation key function produces deterministic output for the same input
- [ ] Temporal/spatial fields in key pattern are handled: `"{source}|{predicate}|{target}@{time}"`
- [ ] LLM prompts constructed from template include the `guideline` rules
- [ ] Prompt for two-stage edge extraction includes `{known_nodes}` placeholder
- **Severity**: HIGH — non-deterministic keys break dedup

### D. Rust KeyCompiler

- [ ] `KeyCompiler::new("{source}|{predicate}|{target}")` parses correctly into Literal and Field segments
- [ ] `KeyCompiler::render()` produces the same output as Python `_compile_key_pattern()` for identical input
- [ ] Missing field values are silently skipped (not "None" or "null" string)
- [ ] Empty pattern returns empty string
- [ ] Pattern with only literals (no placeholders) returns the literal string
- [ ] PyO3 binding: `new(pattern: &str) -> PyResult<Self>` and `render(fields_json: &str) -> PyResult<String>`
- **Severity**: MEDIUM — Rust/Python parity must be verified

### E. Rust GraphNode/GraphEdge Extensions

- [ ] `display_label: Option<String>` added to GraphNode
- [ ] `dedup_key: Option<String>` added to GraphNode
- [ ] `predicate: String` added to GraphEdge (not Option — every edge should have a predicate)
- [ ] `time: Option<String>` added to GraphEdge
- [ ] `location: Option<String>` added to GraphEdge
- [ ] `participants: Option<Vec<Uuid>>` added to GraphEdge
- [ ] `display_label: Option<String>` added to GraphEdge
- [ ] `dedup_key: Option<String>` added to GraphEdge
- [ ] `doc_origins: Vec<Uuid>` added to GraphEdge
- [ ] All new fields have sensible defaults (None for Options, empty Vec for Vec, empty String for predicate)
- [ ] JSON serialization/deserialization round-trips correctly with new fields
- [ ] Existing code that creates GraphEdge without new fields still compiles (backward compat)
- [ ] `edge_type` field is still present and functional (not removed in favor of `predicate`)
- [ ] `upsert_nodes()` and `upsert_edges()` in IndexManager parse new fields from JSON input
- **Severity**: HIGH — data model changes affect all downstream code

### F. PyO3 Bindings — New Fields

- [ ] `upsert_nodes()` JSON input accepts `display_label` and `dedup_key`
- [ ] `upsert_edges()` JSON input accepts `predicate`, `time`, `location`, `participants`, `display_label`, `dedup_key`, `doc_origins`
- [ ] `get_graph_data()` output JSON includes new fields
- [ ] Old-format JSON (without new fields) is still accepted (graceful None defaults)
- **Severity**: HIGH — breaking PyO3 API breaks the Python layer

### G. Template API Endpoints

- [ ] `GET /templates` returns 200 with list of template metadata
- [ ] `GET /templates/{domain}/{name}` returns 200 for valid keys, 404 for invalid
- [ ] `GET /templates/{domain}/{name}` does NOT include LLM prompt strings in response (security)
- [ ] `POST /templates/validate` accepts template JSON and returns `{valid: true/false, errors?}`
- [ ] All template endpoints require authentication (`get_current_user` dependency)
- [ ] `POST /templates/validate` requires admin (`require_admin` dependency)
- [ ] Rate limiting applies to template endpoints
- **Severity**: MEDIUM — prompt leakage is a security concern

### H. YAML Template Files

- [ ] `templates/presets/general/graph.yaml` parses into a valid `TemplateConfig`
- [ ] `templates/presets/general/list.yaml` parses into a valid `TemplateConfig`
- [ ] `templates/presets/general/set.yaml` parses into a valid `TemplateConfig`
- [ ] `templates/presets/general/hypergraph.yaml` parses into a valid `TemplateConfig`
- [ ] Each template has `key` pattern matching its schema field names
- [ ] Each template has `display_label` pattern matching its schema field names
- [ ] Graph templates have `identifiers` section with `entity_key` and `relation_key`
- [ ] Hypergraph template has `participants_field` in relation schema
- **Severity**: LOW — can be fixed without code changes

### I. Frontend TemplatePicker

- [ ] Component fetches templates from `GET /api/v1/templates` on mount
- [ ] Templates grouped by domain in Accordion
- [ ] Selected template key stored in Redux (not just local state)
- [ ] Template key passed to `POST /ingest/folder` body
- [ ] "No template" option is available (default behavior)
- [ ] Error state handled when API fails
- **Severity**: LOW — UI-only issues

### J. Backward Compatibility

- [ ] Existing ingest pipeline works when no template is provided
- [ ] Existing extraction code path (`extract_from_chunk`) runs unchanged when template is absent
- [ ] Existing LanceDB tables continue to work without new columns (nullable/optional)
- [ ] Frontend works without selecting a template

---

## Common Mistakes to Watch For

1. **`yaml.load()` instead of `yaml.safe_load()`** — arbitrary code execution vulnerability
2. **Key pattern `{source}|{type}|{target}`** while schema has field `predicate` not `type` — field name mismatch
3. **Template prompts exposed in API response** — security leak of LLM instruction prompts
4. **Missing backward compat** — `predicate` as required String on GraphEdge without default empty string → existing edge creation code breaks
5. **`edge_type` removed** in favor of `predicate` — they serve different purposes (enum vs raw string), both must coexist
6. **Gallery singleton not thread-safe** — concurrent requests could trigger double initialization
7. **`display_label` and `dedup_key` computed but never stored** — must be passed to Rust `upsert_nodes/edges` and persisted
8. **Template type validation missing** — graph type template without `entity_schema` should be rejected
9. **`participants` field treated as `str`** when schema type is `list` — must be parsed as Python list

---

## Output Format

For each issue found, output:

```
### Issue: [short title]
- **File**: `path/to/file.py:line`
- **Section**: [A-J from checklist]
- **Severity**: CRITICAL | HIGH | MEDIUM | LOW
- **Description**: [what's wrong]
- **Fix**: [specific code change to apply]
```

After listing all issues, provide a summary of changes applied.