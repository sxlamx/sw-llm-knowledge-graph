# Template Authoring Guide

This guide explains how to author custom extraction templates for the
sw-llm-knowledge-graph system.  Templates are **YAML files** stored under
`templates/presets/{domain}/`.  The `TemplateGallery` loads them
automatically at startup.

---

## Quick Start

A minimal graph template:

```yaml
name: my_template
type: graph
domain: custom
description: "Short description of what this template extracts"

entity_schema:
  fields:
    - name: name
      type: string
      description: "Entity canonical name"
      required: true
    - name: entity_type
      type: string
      description: "person | organization | concept"
      required: true
  key: "{name}"
  display_label: "{name} ({entity_type})"

relation_schema:
  fields:
    - name: source
      type: string
      description: "Source entity name"
      required: true
    - name: target
      type: string
      description: "Target entity name"
      required: true
    - name: predicate
      type: string
      description: "Relationship type"
      required: true
  key: "{source}|{predicate}|{target}"
  source_field: source
  target_field: target
  display_label: "{predicate}"

extraction:
  mode: two_stage
  method: standard
  merge_strategy_nodes: exact
  merge_strategy_edges: exact

identifiers:
  entity_key: "{name}"
  relation_key: "{source}|{predicate}|{target}"
  relation_source: source
  relation_target: target
```

Validate it before deploying:

```bash
curl -X POST http://localhost:8000/api/v1/templates/validate \
  -H 'Content-Type: application/json' \
  -d @my_template.yaml
```

---

## Choosing a Template Type

Use this decision tree to pick the right type:

1. **Are you extracting a list of items with no relationships between them?** → `list`
2. **Are you extracting unique items (no duplicates) with no relationships?** → `set`
3. **Are you extracting a single structured record (e.g., a parsed form)?** → `model`
4. **Are you extracting entities with binary relationships (source → target)?**
   - Do relationships have a time dimension (e.g., "cited in 2020")? → `temporal_graph`
   - Do relationships have a location dimension (e.g., "occurred in NYC")? → `spatial_graph`
   - Both time and location? → `spatio_temporal_graph`
   - Neither? → `graph`
5. **Are you extracting multi-party events (>2 participants)?** → `hypergraph`

---

## Top-Level Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Unique template name (lowercase, underscores) |
| `type` | enum | yes | `model`, `list`, `set`, `graph`, `hypergraph`, `temporal_graph`, `spatial_graph`, `spatio_temporal_graph` |
| `language` | list | no | Language codes, default `["en"]` |
| `domain` | string | no | Domain category, default `"general"` |
| `description` | string | no | Human-readable description |
| `entity_schema` | object | yes* | Entity field definitions |
| `relation_schema` | object | yes* | Relation field definitions |
| `extraction` | object | no | Extraction settings |
| `identifiers` | object | yes* | Key patterns for dedup |

\* Required for all graph-type templates (`graph`, `hypergraph`,
`temporal_graph`, `spatial_graph`, `spatio_temporal_graph`).

---

## `entity_schema`

Defines the fields extracted for each entity node.

```yaml
entity_schema:
  fields:
    - name: <field_name>
      type: string | integer | float | boolean | list
      description: "What this field contains"
      required: true | false
      default: <optional default value>
  key: "{field1}"
  display_label: "{field1} ({field2})"
```

- **`key`** — Pattern used as the dedup key.  Must contain at least one
  `{field}` placeholder.  Entities with the same rendered key are
  considered duplicates.
- **`display_label`** — Pattern for human-readable display.  Must
  contain at least one `{field}` placeholder.

---

## `relation_schema`

Defines the fields extracted for each relationship edge.

```yaml
relation_schema:
  fields:
    - name: source
      type: string
      description: "Source entity"
      required: true
    - name: target
      type: string
      description: "Target entity"
      required: true
    - name: predicate
      type: string
      description: "Relationship type"
      required: true
    - name: time
      type: string
      description: "When the event occurred"
      required: false
    - name: location
      type: string
      description: "Where the event occurred"
      required: false
    - name: participants
      type: list
      description: "All participants (for hyperedges)"
      required: true   # only for hypergraph
    - name: context
      type: string
      description: "Surrounding text"
      required: false
  key: "{source}|{predicate}|{target}"
  source_field: source
  target_field: target
  display_label: "{predicate}"
  participants_field: participants   # only for hypergraph
```

- **`source_field`** / **`target_field`** — Name of the fields that
  reference the source and target entities.  For hypergraphs, set
  both to `participants` and add `participants_field`.
- **`participants_field`** — Required for `hypergraph` type.  Indicates
  the field holding a list of all participant entity names.

### Temporal / Spatial Keys

For temporal graphs, include a `time` field and adjust the key pattern:

```yaml
key: "{source}|{predicate}|{target}@{time}"
```

For spatial graphs, include a `location` field:

```yaml
key: "{source}|{predicate}|{target}@{location}"
```

For spatio-temporal graphs:

```yaml
key: "{source}|{predicate}|{target}@{time}|{location}"
```

Trailing separators from empty fields are automatically stripped.  For
example, if `time` is empty, `{source}|{predicate}|{target}@{time}`
renders as `A|cited|B` (no trailing `@`).

---

## `extraction`

Controls how extraction is performed.

```yaml
extraction:
  mode: two_stage      # one_stage | two_stage
  method: standard     # standard | two_stage | graph_rag | light_rag
  node_prompt_extra: "Additional instructions for entity extraction."
  edge_prompt_extra: "Additional instructions for relation extraction."
  merge_strategy_nodes: exact
  merge_strategy_edges: exact
```

### `mode`

| Mode | Description |
|------|-------------|
| `one_stage` | Extract entities and relations in a single LLM call (legacy) |
| `two_stage` | Extract entities first, then extract relations with entity context (recommended) |

### `method`

| Method | Implemented | Description |
|--------|-------------|-------------|
| `standard` | yes | Single-pass NER + LLM extraction |
| `two_stage` | yes | Two-stage nodes-then-edges extraction |
| `graph_rag` | no | Community detection + summarization (future) |
| `light_rag` | no | Lightweight binary-edge extraction (future) |

### `merge_strategy_nodes` / `merge_strategy_edges`

| Strategy | Deterministic | Description |
|----------|---------------|-------------|
| `exact` | yes | Drop exact duplicates (default) |
| `keep_first` | yes | Keep earliest, ignore conflicts |
| `keep_last` | yes | Overwrite with newest |
| `field_overwrite` | yes | Non-null incoming overwrites null existing; lists append |
| `llm_balanced` | no | LLM synthesizes both versions, balanced |
| `llm_prefer_first` | no | LLM synthesis, favor existing data |
| `llm_prefer_last` | no | LLM synthesis, favor incoming data |

**Domain recommendations:**
- **Legal / medical**: `llm_prefer_first` for nodes (authoritative
  sources), `keep_first` for edges (do not infer unstated
  relationships).
- **Finance**: `field_overwrite` for nodes (accumulate data),
  `field_overwrite` for edges.
- **General**: `exact` (fast, no LLM overhead).

---

## `identifiers`

Key patterns used for dedup at the storage layer.

```yaml
identifiers:
  entity_key: "{name}"
  relation_key: "{source}|{predicate}|{target}"
  relation_source: source
  relation_target: target
  time_field: time         # optional, for temporal_graph
  location_field: location # optional, for spatial_graph
```

- `entity_key` / `relation_key` — Must be compatible with the
  `entity_schema.key` / `relation_schema.key` patterns but can differ
  (e.g., narrower for simpler dedup).
- `relation_source` / `relation_target` — Field names used to resolve
  entity references.
- `time_field` / `location_field` — When set, the dedup key
  automatically includes time/location components.

---

## Rules

1. **Prompts must be natural language only** — no code, no JSON schemas
   in `node_prompt_extra` or `edge_prompt_extra`.  The system builds the
   structured prompt automatically from the schema fields.
2. **`extraction.mode` must be explicitly set** in every template.  Do
   not rely on the default.
3. **Legal and medical templates must emphasize precision** — include
   phrases like "only extract explicitly stated relationships" in
   `edge_prompt_extra`.
4. **`key` patterns must contain at least one `{field}` placeholder** —
   empty key patterns are rejected by validation.
5. **Field names must be unique** within `entity_schema.fields` and
   within `relation_schema.fields`.
6. **Graph-type templates require** `entity_schema`, `relation_schema`,
   and `identifiers`.
7. **Hypergraph templates must set** `participants_field` in
  `relation_schema`.
8. **Temporal templates should include** `time_field` in `identifiers`.
9. **Spatial templates should include** `location_field` in
  `identifiers`.

---

## File Location

Place templates in `templates/presets/{domain}/{name}.yaml`.  The file
name should match the template `name` field.  The `domain` directory
must match the `domain` field.

Examples:
- `templates/presets/legal/case_law_graph.yaml`
- `templates/presets/finance/company_graph.yaml`
- `templates/presets/medical/drug_interaction.yaml`

---

## Validation

Use `POST /api/v1/templates/validate` to check a template before
deploying.  The endpoint accepts the same YAML structure as JSON and
returns:

```json
{"valid": true}
```

or

```json
{"valid": false, "errors": "..."}
```