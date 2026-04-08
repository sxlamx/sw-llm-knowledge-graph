# Hyper-Extract Feature Integration Plan

> **Status**: Draft — Phase 4 scope addition  
> **Reference**: `references/Hyper-Extract-main/`  
> **Principle**: Draw architectural insight from Hyper-Extract; do NOT copy code. Re-implement concepts using our Rust + Python + React stack.

---

## 1. OODA Summary

### 1.1 Observe — What Hyper-Extract Does Well

| Capability | How Hyper-Extract Implements It | What We Currently Lack |
|---|---|---|
| **Declarative YAML templates** | 80+ YAML presets across 6 domains define entity schemas, relation schemas, extraction prompts, identifiers, and display labels. A `TemplateFactory` parses YAML → Pydantic models → AutoType instances. | No template system. LLM extraction is hard-coded to a single `extractor.py`. |
| **8 Auto-Types** | AutoModel, AutoList, AutoSet, AutoGraph, AutoHypergraph, AutoTemporalGraph, AutoSpatialGraph, AutoSpatioTemporalGraph — each a self-contained extraction + merge + search + serialize lifecycle. | We extract a single flat graph (entity → entity binary edges). No list/set model extraction, no hyperedges, no temporal/spatial dimensions. |
| **Two-stage extraction** | First extract nodes, then extract edges with known-node context fed to the LLM. Reduces hallucinated edges. `extraction_mode: two_stage` vs `one_stage` per template. | Our ingestion pipeline extracts nodes and edges in one pass. No context-informed edge extraction. |
| **LLM-powered entity/edge merging** | `ontomem.OMem` + `MergeStrategy` enum: KEEP_EXISTING, KEEP_INCOMING, MERGE_FIELD, LLM.BALANCED, LLM.PREFER_EXISTING, LLM.PREFER_INCOMING, LLM.CUSTOM_RULE. Dedup by key extractor, then merge conflicting fields via LLM. | Our entity resolution is 3-step deterministic (exact → Levenshtein → cosine). No LLM-based field-level merge. |
| **Incremental evolution (`feed_text`)** | Call `feed_text()` on an existing knowledge abstract to merge new documents incrementally. The `OMem` handles deduplication and merge. | Our ingestion pipeline processes collections but does not have a clean "add more documents to an existing extracted knowledge structure" API. |
| **Semantic search + chat over extracted knowledge** | Each AutoType builds a FAISS index over extracted items. `search(query, top_k)` and `chat(query, top_k)` retrieve context then generate answers. | We have hybrid vector+BM25+graph search over chunks, but no direct "chat over extracted entities/relations" mode. |
| **Graph visualization with search/chat callbacks** | `ontosight.view_graph()` renders an interactive graph. Click a node → semantic search or chat context. | We have react-force-graph-2d for raw graph view but no node-click → LLM chat integration. |
| **Structured identifiers & display labels** | Every template declares `entity_id`, `relation_id`, `relation_members` patterns (e.g., `{source}\|{type}\|{target}`), and display labels (e.g., `{name} ({type})`). | Our entity dedup uses BLAKE3 hash; no human-readable ID schemes. No display label templates. |
| **10+ extraction methods (RAG-based)** | GraphRAG, LightRAG, Hyper-RAG, HypergraphRAG, Cog-RAG, iText2KG, iText2KG*, KG-Gen, ATOM — each a different algorithmic strategy producing AutoType output. | We have a single extraction path (spaCy NER → LLM extraction). No RAG-based extraction methods. |
| **Domain templates** | Finance, Legal, Medical, TCM, Industry, General — each with specialized entity schemas, relation types, and extraction rules. | We have a generic ontology. No domain-specific extraction templates. |
| **CLI with `he parse/search/show/feed`** | Full CLI lifecycle: parse → search → show → feed. | We have a REST API only. No CLI. |

### 1.2 Orient — What We Already Have That Hyper-Extract Doesn't

| Our Strength | Hyper-Extract Gaps |
|---|---|
| Persistent Rust-backed storage (LanceDB + petgraph + Tantivy) | Hyper-Extract serializes to JSON + FAISS files on disk per extraction. No persistent database layer. |
| Full ingestion pipeline (PDF/DOCX/MD/HTML → chunks → NER → LLM → graph) | Hyper-Extract takes raw text strings as input. No file ingestion or chunking. |
| Multi-user auth, collections, document management | Single-user CLI/library. No multi-tenancy. |
| Hybrid search (vector + BM25 + graph proximity fusion) | FAISS similarity search only. No keyword or graph-based retrieval. |
| Real-time SSE/WebSocket progress streaming | No async progress reporting. |
| Production concurrency model (semaphores, lock ordering, WAL) | No concurrency control. Library calls block synchronously. |

### 1.3 Decide — Features to Incorporate

Based on the OODA analysis, the following features are most impactful and feasible to integrate into our existing architecture:

| # | Feature | Priority | Rationale |
|---|---|---|---|
| **F1** | Declarative YAML extraction templates | **P0** | Foundation that enables all other features. Domain-specific schemas + prompts define WHAT to extract and HOW. |
| **F2** | Two-stage extraction (nodes first, then edges with context) | **P0** | Dramatically reduces hallucinated edges. Core quality improvement. |
| **F3** | LLM-powered entity/edge field-level merging | **P0** | Current 3-step resolution is rigid. LLM merge allows intelligent conflict resolution (merge descriptions, combine aliases). |
| **F4** | Incremental document feeding (`feed_text` equivalent) | **P1** | Users need to add documents to existing collections and have the graph evolve, not rebuild. |
| **F5** | Knowledge chat (vector search + LLM Q&A over extracted entities/relations) | **P1** | Key user-facing feature. Currently we can search chunks but not "chat with the knowledge graph." |
| **F6** | Temporal and spatial graph dimensions | **P2** | Many domains (legal, financial) require time-aware edges. Extends our flat graph to time-aware. |
| **F7** | Hyperedge support (n-ary relations) | **P2** | Legal events, transactions, collaborations involve >2 parties. Currently we only support binary edges. |
| **F8** | Domain template library (finance, legal, medical, etc.) | **P2** | Depends on F1. Templates provide domain expertise out of the box. |
| **F9** | Extraction method registry (multiple algorithms) | **P3** | Advanced. Allow users to choose extraction strategy. Can be added later via plugin system. |
| **F10** | Structured identifiers & display label templates | **P1** | `{source}\|{type}\|{target}` style IDs improve dedup and display. Complements F3. |

---

## 2. Feature-by-Feature Implementation Plan

### F1: Declarative YAML Extraction Templates

**What Hyper-Extract does**: A YAML template defines `type`, `output` (entity fields, relation fields), `guideline` (extraction rules/prompts), `identifiers` (key patterns), `options`, and `display`. The `TemplateFactory` parses this into Pydantic schemas, prompt strings, key extractors, and instantiates the correct AutoType.

**How we will implement it (original design, not copying)**:

#### 2.1.1 YAML Template Schema

Create a YAML template format inspired by but different from Hyper-Extract's:

```yaml
# templates/presets/legal/case_law_graph.yaml
name: case_law_graph
type: graph  # graph | hypergraph | temporal_graph | list | set | model
language: [en]
domain: legal
description: "Extract case law entities and relationships from legal documents."

entity_schema:
  fields:
    - name: name
      type: string
      description: "Party name, case title, or statute designation."
      required: true
    - name: entity_type
      type: string
      description: "person|court|statute|case|organization|concept"
      required: true
    - name: description
      type: string
      description: "Brief description."
      required: false
  key: "{name}"
  display_label: "{name} ({entity_type})"

relation_schema:
  fields:
    - name: source
      type: string
      description: "Source entity key."
      required: true
    - name: target
      type: string
      description: "Target entity key."
      required: true
    - name: predicate
      type: string
      description: "cited|overruled|distinguished|affirmed|applied|interpreted|mentioned_by"
      required: true
    - name: context
      type: string
      description: "Contextual excerpt or summary."
      required: false
  key: "{source}|{predicate}|{target}"
  source_field: source
  target_field: target
  display_label: "{predicate}"

extraction:
  mode: two_stage  # one_stage | two_stage
  node_prompt_extra: "Focus on legal parties, statutes, and judicial bodies."
  edge_prompt_extra: "Only extract explicitly stated legal relationships. Use canonical predicates."

  merge_strategy:
    nodes: llm_balanced    # exact | llm_balanced | llm_prefer_existing | llm_prefer_incoming | field_overwrite
    edges: keep_existing    # exact | keep_existing | keep_incoming | field_overwrite

identifiers:
  entity_key: name
  relation_key: "{source}|{predicate}|{target}"
  relation_source: source
  relation_target: target
```

#### 2.1.2 Implementation Components

| Component | Location | Responsibility |
|---|---|---|
| `TemplateConfig` (Pydantic) | `python-api/app/models/template.py` | Parse and validate YAML templates |
| `TemplateGallery` | `python-api/app/services/template_gallery.py` | Load all `.yaml` from `templates/presets/`, index by `domain/name` |
| `TemplateFactory` | `python-api/app/services/template_factory.py` | Convert template config → Pydantic schemas + prompt strings + key extractors + merge config |
| `templates/presets/` | `templates/presets/{domain}/*.yaml` | YAML template files |
| API endpoints | `python-api/app/routers/templates.py` | `GET /templates`, `GET /templates/{domain}/{name}`, `POST /templates/custom` |
| Frontend template selector | `frontend/src/components/ingest/TemplatePicker.tsx` | Browse and select templates during collection creation |

#### 2.1.3 Key Differences from Hyper-Extract

- Our templates are stored in filesystem, not embedded in Python package.
- Template config is validated server-side before accepting custom templates.
- Templates integrate with our existing ingest pipeline, not standalone library calls.
- We use our existing merge strategies (see F3) rather than `ontomem`.

---

### F2: Two-Stage Extraction

**What Hyper-Extract does**: In `two_stage` mode, nodes are extracted first in a batch LLM call. Then edges are extracted with the known entity list injected into the prompt: `"Only create edges involving entities from this known list: {known_nodes}"`. After extraction, dangling edges (connecting to non-existent nodes) are pruned.

**How we will implement it (original design)**:

#### 2.2.1 Architecture

Modify `python-api/app/llm_pipeline/extractor.py` to support two extraction modes:

```
Current flow:
  chunks → LLM (extract entities + relations together) → merge into graph

New two-stage flow:
  chunks → LLM (extract entities only) → merge entities
         → LLM (extract relations with entity context) → prune dangling → merge into graph
```

#### 2.2.2 Implementation

| Component | Changes |
|---|---|
| `extractor.py` | Add `extraction_mode` parameter. When `two_stage`: (1) call `extract_entities(chunks)` using `node_schema` from template, (2) build `known_nodes` list, (3) call `extract_relations(chunks, known_nodes)` using `relation_schema` and `known_nodes` context injection. |
| Prompt construction | `build_node_prompt(template)` and `build_edge_prompt(template, known_nodes)` — generate LLM prompts from template `guideline` sections. |
| Dangling edge pruning | Reuse existing `_prune_dangling_edges()` logic in Rust `graph_engine` (skip edges whose source/target doesn't exist in node set). |
| Template integration | Template YAML specifies `extraction.mode: two_stage` or `one_stage`. Factory passes mode to extractor. |
| Fallback | If no template is selected, default to `one_stage` (current behavior). |

#### 2.2.3 Ingest Pipeline Changes

In `ingest_worker.py`, when `settings.extraction_mode == "two_stage"`:

1. After step 6 (NER tagging, which provides entity candidates), run LLM entity extraction → merge with NER entities.
2. Build entity context string from merged entity list.
3. Run LLM relation extraction with entity context.
4. Pass to graph construction as before.

---

### F3: LLM-Powered Entity/Edge Field-Level Merging

**What Hyper-Extract does**: Uses `ontomem.OMem` with merge strategies: when two entities share the same key, a `Merger` resolves field conflicts. `LLM.BALANCED` sends both versions to the LLM which synthesizes a merged version. `MERGE_FIELD` overwrites null fields, appends lists. `KEEP_EXISTING`/`KEEP_INCOMING` are simple priority strategies.

**How we will implement it (original design)**:

#### 2.3.1 Merge Strategy Enum

```python
# python-api/app/services/merge_strategy.py
class MergeStrategy(str, Enum):
    EXACT = "exact"               # Key-based dedup, drop duplicates (current behavior)
    KEEP_FIRST = "keep_first"     # Keep earliest, ignore conflicts
    KEEP_LAST = "keep_last"       # Keep newest, overwrite
    FIELD_OVERWRITE = "field_overwrite"  # Non-null fields overwrite nulls, lists append
    LLM_BALANCED = "llm_balanced"        # LLM synthesizes both, balanced
    LLM_PREFER_FIRST = "llm_prefer_first" # LLM synthesis, favor existing
    LLM_PREFER_LAST = "llm_prefer_last"   # LLM synthesis, favor incoming
```

#### 2.3.2 Merge Engine

```python
# python-api/app/services/entity_merger.py
class EntityMerger:
    def merge(self, existing: Entity, incoming: Entity, strategy: MergeStrategy) -> Entity:
        if strategy in (EXACT, KEEP_FIRST):
            return existing  # current behavior
        if strategy == KEEP_LAST:
            return incoming
        if strategy == FIELD_OVERWRITE:
            return self._field_overwrite(existing, incoming)
        if strategy in (LLM_BALANCED, LLM_PREFER_FIRST, LLM_PREFER_LAST):
            return self._llm_merge(existing, incoming, strategy)
```

The LLM merge prompt will be constructed from the template's entity schema fields, asking the LLM to reconcile differences field by field.

#### 2.3.3 Integration Points

| Current Code | Change |
|---|---|
| `rust-core/src/graph/builder.rs` — entity resolution | Keep the 3-step deterministic resolution as `EXACT` strategy. Add a new async code path that can delegate to the Python `EntityMerger` for LLM-based strategies. |
| `python-api/app/llm_pipeline/extractor.py` | After extraction, call `EntityMerger.merge()` with the template's configured strategy before writing to graph. |
| Template YAML | `extraction.merge_strategy.nodes` and `.edges` fields select the strategy. |

---

### F4: Incremental Document Feeding

**What Hyper-Extract does**: `feed_text()` on an existing knowledge abstract merges new extraction results into existing data using `_update_data_state()`, which adds new items and deduplicates/merges conflicts.

**How we will implement it (original design)**:

#### 2.4.1 API Endpoint

```
POST /api/v1/collections/{collection_id}/feed
Body: { "file_paths": [...], "template": "legal/case_law_graph" }
Response: { "job_id": "..." }
```

This reuses the existing ingest pipeline but:

1. Skips step 1 (file discovery) if file_paths are provided.
2. Runs steps 2-8 (extraction, NER, entity resolution) on new documents only.
3. In step 9 (entity resolution), uses the template's merge strategy to merge new entities/edges with existing ones in the collection's graph.
4. In step 10 (graph construction), upserts into existing LanceDB tables rather than replacing.
5. In step 11 (index update), triggers incremental index rebuild.

#### 2.4.2 Implementation

| Component | Change |
|---|---|
| `ingest_worker.py` | Add `feed_mode` that merges instead of replaces. Use `EntityMerger` for conflict resolution. |
| `rust-core` graph builder | Add `merge_into_collection(collection_id, new_nodes, new_edges, merge_strategy)` method. |
| API router | Add `/collections/{id}/feed` endpoint that creates an ingest job with `feed_mode=True`. |

---

### F5: Knowledge Chat (Vector Search + LLM Q&A over Extracted Knowledge)

**What Hyper-Extract does**: Each AutoType can `build_index()` on extracted items (FAISS), then `search(query, top_k)` and `chat(query, top_k)`. `chat()` retrieves relevant items, formats them as context, and sends a QA prompt to the LLM.

**How we will implement it (original design)**:

#### 2.5.1 Knowledge Chat Endpoint

```
POST /api/v1/collections/{collection_id}/chat
Body: { "query": "What are the key legal precedents cited?", "top_k_nodes": 5, "top_k_edges": 5 }
Response: { "answer": "...", "nodes": [...], "edges": [...] }
```

#### 2.5.2 Implementation

| Component | Change |
|---|---|
| `rust-core/src/search/engine.rs` | Add `search_nodes(query, top_k, collection_id)` and `search_edges(query, top_k, collection_id)` methods that search the `nodes` and `edges` LanceDB tables using vector similarity. |
| `python-api/app/services/knowledge_chat.py` | New service: (1) search nodes and edges, (2) format as context, (3) call LLM with QA prompt, (4) return answer + retrieved items. |
| `python-api/app/routers/chat.py` | New router for the `/chat` endpoint. |
| Frontend | New `ChatPanel` component in the collection view. Toggle between "Search Chunks" and "Ask Knowledge Graph" modes. |

---

### F6: Temporal and Spatial Graph Dimensions

**What Hyper-Extract does**: `AutoTemporalGraph` adds `time_in_edge_extractor`, injects `observation_time` into prompts for relative time resolution, and deduplicates edges by `{source}|{predicate}|{target}@{time}`. `AutoSpatialGraph` does the same for location. `AutoSpatioTemporalGraph` combines both.

**How we will implement it (original design)**:

#### 2.6.1 Edge Schema Extension

Add optional `time` and `location` fields to our existing edge schema:

```rust
// rust-core/src/models/graph.rs
pub struct GraphEdge {
    pub id: Uuid,
    pub source: Uuid,
    pub target: Uuid,
    pub edge_type: EdgeType,
    pub predicate: String,
    pub weight: f32,
    pub context: Option<String>,
    pub chunk_id: Option<Uuid>,
    pub doc_origins: Vec<Uuid>,
    pub time: Option<String>,       // NEW: temporal attribute (e.g., "2024", "2024-01-15")
    pub location: Option<String>,   // NEW: spatial attribute (e.g., "New York", "Room 101")
    pub created_at: i64,
}
```

LanceDB `edges` table gains `time` and `location` columns.

#### 2.6.2 Template Configuration

Template YAML specifies the temporal/spatial mode:

```yaml
# In the template's identifiers section:
identifiers:
  time_field: time        # optional, enables temporal dedup
  location_field: location  # optional, enables spatial dedup
```

The `TemplateFactory` detects this and generates `time_in_edge_extractor` and `location_in_edge_extractor` lambdas.

#### 2.6.3 Edge Deduplication Change

Current key: `{source}|{predicate}|{target}`  
Temporal key: `{source}|{predicate}|{target}@{time}`  
Spatial key: `{source}|{predicate}|{target}@{location}`  
Spatio-temporal key: `{source}|{predicate}|{target}@{time}|{location}`

The `EntityMerger` uses the template-configured key pattern for dedup.

#### 2.6.4 Search Extensions

- `GET /graph/path?time_from=2020&time_to=2024` — filter edges by time range.
- `GET /graph/subgraph?location=New+York` — filter edges by location.

---

### F7: Hyperedge Support (N-ary Relations)

**What Hyper-Extract does**: `AutoHypergraph` uses `nodes_in_edge_extractor` that returns a tuple (not just source/target). An edge can connect `N` nodes. The `participants` field in the relation schema is a `list[str]`. Dangling edge pruning checks ALL participants exist.

**How we will implement it (original design)**:

#### 2.7.1 Edge Schema Extension

Add an optional `participants` field (a list of node IDs) alongside the existing `source/target` binary pattern:

```rust
// rust-core/src/models/graph.rs
pub struct GraphEdge {
    // ... existing fields ...
    pub source: Option<Uuid>,        // None for hyperedges
    pub target: Option<Uuid>,        // None for hyperedges
    pub participants: Option<Vec<Uuid>>,  // For hyperedges: list of all participant node IDs
}
```

Binary edges use `source`+`target`. Hyperedges use `participants`. The API and storage layer handle both.

#### 2.7.2 Template Configuration

```yaml
type: hypergraph
relation_schema:
  fields:
    - name: participants
      type: list
      description: "List of entity names involved in this event."
      required: true
  key: "{name}|{type}"
  members: participants  # indicates n-ary relation
```

#### 2.7.3 Graph Display Changes

The frontend `ForceGraph` component must render hyperedges as hyperboxes or highlight multi-node connections. Cytoscape.js supports compound nodes for this.

---

### F8: Domain Template Library

**What Hyper-Extract does**: 80+ presets across `finance/`, `legal/`, `medicine/`, `tcm/`, `industry/`, `general/`.

**How we will implement it (original design)**:

#### 2.8.1 Template Structure

```
templates/
  presets/
    general/
      graph.yaml           # General-purpose knowledge graph
      list.yaml            # General-purpose entity list
      set.yaml             # General-purpose entity set
      temporal_graph.yaml  # General time-aware graph
      biography_graph.yaml # Person-focused temporal graph
      concept_graph.yaml   # Concept hierarchy graph
    legal/
      case_law_graph.yaml       # Case law entities & citations
      contract_graph.yaml       # Contract parties & obligations
      legislation_graph.yaml    # Statute hierarchy & amendments
    finance/
      company_graph.yaml        # Corporate entities & ownership
      transaction_temporal.yaml # Transaction flows with time
    medical/
      clinical_graph.yaml       # Patient-entity-event temporal graph
      drug_interaction.yaml     # Drug contraindication graph
    industry/
      supply_chain.yaml         # Supply chain spatio-temporal graph
      workflow.yaml             # Process/workflow graph
```

Each template is authored from scratch for our domain schemas, using Hyper-Extract's templates as **reference** for what fields are useful in each domain — but with our own field names, descriptions, and extraction rules adapted to our architecture.

#### 2.8.2 Template Authoring Guidelines

- Use our entity/edge model (not Hyper-Extract's Pydantic AutoType model).
- Include bilingual prompts where useful (English primary).
- Define `key` patterns matching our dedup logic.
- Set `merge_strategy` appropriate to the domain (e.g., legal = `llm_prefer_first` for authoritative sources).
- Every template must specify `extraction.mode` (default: `two_stage`).

---

### F9: Extraction Method Registry

**What Hyper-Extract does**: A `MethodRegistry` maps named methods (`graph_rag`, `light_rag`, etc.) to AutoType + algorithm classes. Each method wraps an extraction algorithm that produces AutoType output.

**How we will implement it (original design, lower priority)**:

```python
# python-api/app/services/extraction_registry.py
class ExtractionMethod(Protocol):
    name: str
    auto_type: str  # "graph", "hypergraph", etc.
    description: str
    def extract(self, text: str, template: TemplateConfig, ...) -> ExtractionResult: ...

REGISTRY: Dict[str, ExtractionMethod] = {
    "standard": StandardExtractor(),     # Current: NER + LLM
    "two_stage": TwoStageExtractor(),    # F2: nodes-then-edges
    "graph_rag": GraphRAGExtractor(),    # Future: community detection + summarization
    "light_rag": LightRAGExtractor(),    # Future: lightweight binary edges
}
```

Template YAML can specify `extraction.method: two_stage` or users can override via API.

This is **P3** and can be added in a later phase without blocking other features.

---

### F10: Structured Identifiers & Display Labels

**What Hyper-Extract does**: Every template declares:
- `entity_id`: name of the field used as dedup key
- `relation_id`: pattern like `{source}|{type}|{target}`
- `relation_members`: source/target field names (or `participants` for hyperedges)
- `entity_label`: display pattern like `{name} ({type})`
- `relation_label`: display pattern like `{type}`

These are compiled into lambda extractors at runtime.

**How we will implement it (original design)**:

#### 2.10.1 In Rust `graph_engine`

```rust
// Add to GraphNode and GraphEdge:
pub display_label: Option<String>,  // "{name} ({entity_type})" or "{predicate}"
```

The Rust engine doesn't need to compile lambdas — it stores the rendered label string. Label rendering happens in Python during extraction:

```python
# python-api/app/services/template_factory.py
def render_label(template: str, data: dict) -> str:
    """Replace {field} placeholders with actual values."""
    return template.format(**{k: v for k, v in data.items() if v is not None})
```

#### 2.10.2 Key Patterns for Dedup

When the template specifies `relation_key: "{source}|{predicate}|{target}"`, the extractor uses this pattern to compute a dedup key string. This replaces our current BLAKE3-hash-based dedup with human-readable, deterministic keys.

For temporal edges: `relation_key: "{source}|{predicate}|{target}@{time}"`  
For spatial edges: `relation_key: "{source}|{predicate}|{target}@{location}"`

---

## 3. Implementation Sequence

### Phase 4A (Templates + Two-Stage Extraction) — Weeks 1-4

| Week | Task | Depends On |
|---|---|---|
| 1 | F1: `TemplateConfig` Pydantic model, YAML parser, `TemplateGallery` loader | — |
| 1 | F10: `render_label()` and key pattern parser in `template_factory.py` | F1 |
| 2 | F1: API endpoints `GET /templates`, `GET /templates/{domain}/{name}` | F1 |
| 2 | F1: `templates/presets/general/graph.yaml`, `list.yaml`, `set.yaml` | F1 |
| 3 | F2: Two-stage extraction mode in `extractor.py` | F1 |
| 3 | F2: Dangling edge pruning integration | F2 |
| 4 | F1 + F2: End-to-end test with a template + two-stage extraction | F1, F2 |
| 4 | F10: Display labels stored on nodes/edges | F10 |

### Phase 4B (Merge Strategies + Incremental Feeding) — Weeks 5-8

| Week | Task | Depends On |
|---|---|---|
| 5 | F3: `MergeStrategy` enum and `EntityMerger` service | F1 |
| 5 | F3: `FIELD_OVERWRITE` and `KEEP_FIRST/LAST` strategies | F3 |
| 6 | F3: `LLM_BALANCED`, `LLM_PREFER_FIRST/LAST` strategies | F3 |
| 6 | F4: `POST /collections/{id}/feed` endpoint | F3 |
| 7 | F4: `feed_mode` in ingest worker with merge-into logic | F3, F4 |
| 7 | F4: Rust `merge_into_collection` method | F4 |
| 8 | Integration tests for merge strategies and feed | F3, F4 |

### Phase 4C (Knowledge Chat + Domain Templates) — Weeks 9-12

| Week | Task | Depends On |
|---|---|---|
| 9 | F5: `search_nodes` and `search_edges` in Rust search engine | — |
| 9 | F5: `knowledge_chat.py` service with LLM QA prompt | F5 |
| 10 | F5: `/chat` API endpoint + frontend `ChatPanel` component | F5 |
| 10 | F8: Author `legal/case_law_graph.yaml`, `finance/company_graph.yaml` | F1 |
| 11 | F8: Author `medical/clinical_graph.yaml`, `industry/supply_chain.yaml` | F1 |
| 11 | F8: Template picker UI in frontend during collection creation | F1, F8 |
| 12 | F8: Documentation for custom template authoring | F8 |

### Phase 4D (Temporal/Spatial + Hyperedges) — Weeks 13-16

| Week | Task | Depends On |
|---|---|---|
| 13 | F6: Add `time` and `location` fields to `GraphEdge` in Rust + LanceDB | — |
| 13 | F6: Update entity resolution to support temporal/spatial keys | F6, F3 |
| 14 | F6: Template YAML `time_field`/`location_field` parsing | F1, F6 |
| 14 | F6: Frontend display of time/location on edges | F6 |
| 15 | F7: Add `participants` field to `GraphEdge`, hyperedge extraction | F1 |
| 15 | F7: Frontend hyperedge rendering (Cytoscape compound nodes) | F7 |
| 16 | F6+F7: Author `temporal_graph.yaml`, `spatio_temporal.yaml`, `hypergraph.yaml` templates | F6, F7 |

### Phase 4E (Extraction Method Registry) — Weeks 17+

| Week | Task | Depends On |
|---|---|---|
| 17+ | F9: `ExtractionMethodRegistry` protocol and registration | F1 |
| 17+ | F9: Implement `standard` (current), `two_stage` method classes | F2 |
| 17+ | F9: API endpoint `GET /extraction-methods` | F9 |
| 17+ | F9: Template YAML `method` field support | F9 |

---

## 4. Template Design Reference

The following templates from Hyper-Extract will serve as **reference** (not direct copies) for our domain template authoring. We will study their field choices, prompt structures, and extraction rules, then author our own YAML templates:

| Hyper-Extract Template | What to Study | Our Adaptation |
|---|---|---|
| `general/base_graph.yaml` | Entity/relation field naming, identifier patterns, two-stage extraction rules | `general/graph.yaml` — adapted to our schema |
| `general/base_temporal_graph.yaml` | `time_field`, `observation_time` injection, `rules_for_time` prompt section | `general/temporal_graph.yaml` — use our edge.time field |
| `general/base_spatial_graph.yaml` | `location_field`, `observation_location` injection | `general/spatial_graph.yaml` — use our edge.location field |
| `general/base_spatio_temporal_graph.yaml` | Combined time+location | `general/spatio_temporal_graph.yaml` |
| `general/base_hypergraph.yaml` | `participants` list field, `relation_members: participants` | `general/hypergraph.yaml` — use our edge.participants field |
| `general/biography_graph.yaml` | Domain-specific temporal rules | `general/biography_graph.yaml` |
| `general/concept_graph.yaml` | Concept hierarchy, `is_a`/`part_of` relations | `general/concept_graph.yaml` |
| `finance/` presets | Financial entity types (company, stock, transaction) | `finance/company_graph.yaml`, `finance/transaction_temporal.yaml` |
| `legal/` presets | Legal entity types (court, statute, case) | `legal/case_law_graph.yaml`, `legal/contract_graph.yaml` |
| `medicine/` presets | Medical entity types (drug, disease, procedure) | `medical/clinical_graph.yaml` |

---

## 5. Specification Updates Required

The following specification documents need updates to incorporate these features:

| Spec | Updates Required |
|---|---|
| `02-data-models.md` | Add `time`, `location`, `participants` fields to edge model. Add `display_label` to nodes and edges. |
| `03-ingestion-pipeline.md` | Add two-stage extraction mode. Add template-driven extraction. Add merge strategy selection. |
| `04-ontology-engine.md` | Templates complement (not replace) the ontology. Template entity types should be validated against ontology. |
| `07-graph-engine.md` | Add hyperedge support. Add temporal/spatial edge filtering. Add merge-into graph methods. |
| `08-api-design.md` | Add `/templates`, `/collections/{id}/feed`, `/collections/{id}/chat` endpoints. |
| `09-frontend-design.md` | Add `TemplatePicker`, `ChatPanel`, temporal/spatial edge display, hyperedge rendering. |
| `13-development-roadmap.md` | Add Phase 4A-4E with timeline. |
| NEW: `15-hyper-extract-integration.md` | This document. |

---

## 6. Key Architectural Decisions

1. **Templates are YAML files on disk**, not code-embedded configurations. This allows easy customization and version control.

2. **Two-stage extraction is prompt-driven**, not algorithmically different. The same LLM is called twice with different schemas and prompts. This keeps the Rust core simple.

3. **Merge strategies are Python-side**. The Rust core performs exact dedup (current behavior). LLM-based merge is an async Python service call. This avoids blocking the Rust event loop with LLM calls.

4. **Hyperedges use `participants` list field**, not separate edge types. This extends our existing binary edge model rather than replacing it. Binary edges and hyperedges coexist.

5. **Temporal/spatial are edge attributes**, not separate node types. This is consistent with Hyper-Extract's design (time is an attribute of relations, not entities) and keeps our node model clean.

6. **Template factory is server-side** (Python), not client-side. Templates contain LLM prompts that should not be exposed to the frontend. The frontend only sees template metadata (name, description, domain, type).

7. **Domain templates are authored from scratch** using Hyper-Extract's presets as reference for field choices and prompt design patterns, not copied verbatim. This avoids licensing concerns and ensures our templates fit our data model.

8. **All LLM features use Ollama Cloud API**. Every LLM call — two-stage extraction, entity/edge merge, knowledge chat, node summaries, ontology generation, contextual prefix, document summaries — routes through the existing `settings.ollama_cloud_base_url` endpoint (OpenAI-compatible API). No local Ollama daemon, no external OpenAI API key. The `httpx.AsyncClient` pattern from `app/llm/extractor.py` is the canonical call pattern for all new LLM features.

---

## 7. Rust Feasibility Assessment

### 7.1 Current Rust Capabilities vs. Feature Requirements

| Feature | Can Rust Do It? | Should Rust Do It? | Recommendation |
|---|---|---|---|
| **F1: YAML template parsing** | Yes — `serde_yaml` can parse YAML into typed structs. Rust can validate schemas, resolve key patterns, and localize i18n strings. | **No.** Templates produce LLM prompts that must be consumed by Python-side LLM calls. Parsing in Rust would require duplicating the prompt-construction logic that lives in Python. The YAML → Pydantic → prompt pipeline is naturally Python. | **Python-side**. Rust is not needed here. |
| **F2: Two-stage extraction** | Partial. Rust can chunk text and build `known_nodes` lists. But the two LLM calls (extract nodes, then extract edges) are inherently async HTTP calls to Ollama Cloud. | **No.** Two-stage extraction is two LLM calls with prompt construction. This is entirely Python. Rust's chunking is already used. | **Python-side**. Rust chunking (already done) + Python LLM orchestration. |
| **F3: Entity/edge merging** | Rust already does exact + fuzzy (Levenshtein + cosine) resolution in `EntityResolver`. LLM-merge strategies require calling Ollama Cloud. | **Split.** Deterministic merge strategies (KEEP_FIRST, KEEP_LAST, FIELD_OVERWRITE) should be **Rust-side** for speed. LLM-based merge strategies must be **Python-side** (async HTTP call). | **Rust: deterministic merge. Python: LLM merge.** |
| **F4: Incremental feeding** | Rust's `upsert_nodes`/`upsert_edges` already support adding to existing graphs. The `merge_into_collection` logic (conflict resolution) is the new part. | **Split.** The graph upsert and adjacency rebuild is already Rust. Merge-strategy dispatch should call Python for LLM strategies. | **Rust: graph mutation. Python: merge orchestration.** |
| **F5: Knowledge chat** | Rust can do vector search over `nodes` and `edges` LanceDB tables (once `search_nodes`/`search_edges` are implemented). The final LLM Q&A call is Python. | **Split.** Rust handles the search/retrieval channel. Python handles the LLM chat synthesis. | **Rust: node/edge search. Python: LLM chat.** |
| **F6: Temporal/spatial dimensions** | Rust stores `time`/`location` as edge fields. Rust can filter edges by time range or location. Rust can compute composite dedup keys with temporal/spatial components. | **Yes.** This is pure data modeling + storage + query filtering. No LLM calls needed in Rust. | **Rust-side.** Schema extension, LanceDB columns, key computation, query filters. |
| **F7: Hyperedges** | Rust can store `participants: Vec<Uuid>` on `GraphEdge`. Rust can validate that all participants exist (dangling edge pruning). Rust can compute adjacency differently for hyperedges. | **Yes.** This is data modeling + graph structure. No LLM needed. | **Rust-side.** Schema extension, adjacency rebuild, dangling pruning. |
| **F8: Domain templates** | No code component — these are YAML files. | **No.** Authoring YAML files is not a Rust concern. | **Python-side (templates). Rust: no change.** |
| **F9: Extraction method registry** | The registry is a Python dict mapping method names to extractor classes. | **No.** This is Python-side plugin architecture. | **Python-side.** |
| **F10: Structured identifiers & labels** | Rust can store `display_label` and `dedup_key` strings on `GraphNode`/`GraphEdge`. Rust can compute composite keys from a pattern string (e.g., `"{source}\|{predicate}\|{target}@{time}"`). | **Yes.** Key computation and storage are pure data operations. | **Rust-side.** Key computation, label storage, dedup by key. |

### 7.2 Rust Implementation Plan — New Modules and Changes

#### 7.2.1 Data Model Extensions (`rust-core/src/models.rs`)

```rust
// ADD to GraphEdge:
pub predicate: String,           // NEW: explicit predicate string (was edge_type only)
pub time: Option<String>,        // NEW: temporal attribute (F6)
pub location: Option<String>,    // NEW: spatial attribute (F6)
pub participants: Option<Vec<Uuid>>,  // NEW: hyperedge participants (F7)
pub display_label: Option<String>,   // NEW: rendered label template (F10)
pub dedup_key: Option<String>,       // NEW: computed dedup key (F10)
pub doc_origins: Vec<Uuid>,          // NEW: multi-document provenance (F4)

// ADD to GraphNode:
pub display_label: Option<String>,   // NEW: rendered label template (F10)
pub dedup_key: Option<String>,       // NEW: computed dedup key (F10)
```

**Migration**: `predicate` field added to `GraphEdge`. Existing code uses `edge_type` (enum). New code stores the raw predicate string (e.g., `"cited"`) in `predicate` and maps to `EdgeType::Custom(predicate)` for backward compatibility. LanceDB `edges` table gains `predicate`, `time`, `location`, `participants`, `display_label`, `dedup_key` columns.

#### 7.2.2 New Module: `rust-core/src/graph/merge.rs` — Deterministic Merge Strategies

```rust
/// Deterministic merge strategies that Rust can handle without LLM calls.
/// LLM-based strategies are dispatched to Python via PyO3 callback.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum DeterministicMergeStrategy {
    KeepFirst,       // Preserve existing, ignore incoming
    KeepLast,        // Overwrite with incoming
    FieldOverwrite,  // Non-null incoming fields overwrite null existing; lists append
}

/// Merge two nodes using a deterministic strategy.
/// Returns the merged node (new allocation, does not mutate inputs).
pub fn merge_nodes_deterministic(
    existing: &GraphNode,
    incoming: &GraphNode,
    strategy: &DeterministicMergeStrategy,
) -> GraphNode;

/// Merge two edges using a deterministic strategy.
pub fn merge_edges_deterministic(
    existing: &GraphEdge,
    incoming: &GraphEdge,
    strategy: &DeterministicMergeStrategy,
) -> GraphEdge;
```

**Key design**: Rust handles `KeepFirst`, `KeepLast`, `FieldOverwrite` synchronously (microsecond-level). When the template specifies an LLM strategy (`llm_balanced`, etc.), Python calls Rust for exact dedup first (to detect the conflict), then calls the Ollama Cloud LLM, then writes the merged result back via `upsert_nodes`/`upsert_edges`.

#### 7.2.3 New Method: `merge_into_collection` on `IndexManager` (PyO3)

```rust
/// Incremental merge of new nodes/edges into an existing collection graph.
/// Uses DeterministicMergeStrategy for conflict resolution.
/// For LLM-based strategies, Python should call resolve_conflicts() first,
/// then upsert the merged results.
#[pymethods]
impl IndexManager {
    /// Detect conflicts between new and existing nodes.
    /// Returns JSON: [{existing_id, incoming_id, dedup_key, field_diffs}]
    fn detect_node_conflicts(
        &self,
        collection_id: &str,
        new_nodes_json: &str,
    ) -> PyResult<String>;

    /// Detect conflicts between new and existing edges.
    fn detect_edge_conflicts(
        &self,
        collection_id: &str,
        new_edges_json: &str,
    ) -> PyResult<String>;

    /// Merge new nodes into collection using deterministic strategy.
    fn merge_nodes_into_collection(
        &self,
        collection_id: &str,
        new_nodes_json: &str,
        strategy: &str,  // "keep_first" | "keep_last" | "field_overwrite"
    ) -> PyResult<String>;  // Returns merge report JSON

    /// Merge new edges into collection using deterministic strategy.
    fn merge_edges_into_collection(
        &self,
        collection_id: &str,
        new_edges_json: &str,
        strategy: &str,
    ) -> PyResult<String>;

    /// Prune dangling edges (edges whose source/target/participants
    /// don't exist in the node set).
    fn prune_dangling_edges(
        &self,
        collection_id: &str,
    ) -> PyResult<usize>;  // Returns count of pruned edges
}
```

#### 7.2.4 New Method: `search_nodes` / `search_edges` on `IndexManager` (PyO3)

```rust
#[pymethods]
impl IndexManager {
    /// Vector similarity search over nodes table.
    /// Returns JSON array of matching nodes with scores.
    fn search_nodes(
        &self,
        collection_id: &str,
        query_embedding_json: &str,
        top_k: usize,
    ) -> PyResult<String>;

    /// Vector similarity search over edges table.
    /// Returns JSON array of matching edges with scores.
    fn search_edges(
        &self,
        collection_id: &str,
        query_embedding_json: &str,
        top_k: usize,
        time_from: Option<&str>,    // F6: temporal filter
        time_to: Option<&str>,      // F6: temporal filter
        location: Option<&str>,     // F6: spatial filter
    ) -> PyResult<String>;
}
```

**Note**: These require embeddings stored in the `nodes` and `edges` LanceDB tables. Currently only `chunks` has embeddings. The Rust `IndexManager` will need to open `{collection_id}_nodes` and `{collection_id}_edges` LanceDB tables and perform ANN search. This is structurally identical to the existing chunk vector search done in Python via LanceDB.

#### 7.2.5 Key Pattern Compiler (`rust-core/src/graph/keys.rs`)

```rust
/// Compile a template key pattern like "{source}|{predicate}|{target}@{time}"
/// into a function that extracts the key from a GraphEdge.
pub struct KeyCompiler;

impl KeyCompiler {
    /// Compile a key pattern for nodes.
    /// Pattern uses field names from GraphNode as placeholders.
    pub fn compile_node_key(pattern: &str) -> Result<Box<dyn Fn(&GraphNode) -> String>, String>;

    /// Compile a key pattern for edges.
    /// Supports: {source}, {target}, {predicate}, {time}, {location}, {participants}
    pub fn compile_edge_key(pattern: &str) -> Result<Box<dyn Fn(&GraphEdge) -> String>, String>;

    /// Compile an edge-to-participants extractor for hyperedge validation.
    /// Returns a function that extracts all participant node IDs from an edge.
    pub fn compile_participants_extractor(
        source_field: Option<&str>,
        target_field: Option<&str>,
        participants_field: Option<&str>,
    ) -> Result<Box<dyn Fn(&GraphEdge) -> Vec<Uuid>>, String>;
}
```

**Implementation approach**: Parse the pattern string at compile time into a sequence of literal segments and field-name segments. At runtime, look up field values by name on the struct using a pre-built `HashMap<&str, Box<dyn Fn(&T) -> String>>` field accessor map. This avoids `format!` macro limitations and gives O(n) key rendering.

#### 7.2.6 Dangling Edge Pruning for Hyperedges

Existing `build_graph_edges()` only creates edges where source+target exist. For hyperedges, validation must check **all** participants:

```rust
/// Prune edges that reference non-existent nodes.
/// For binary edges: checks source and target.
/// For hyperedges: checks ALL participants.
pub fn prune_dangling_edges(graph: &mut KnowledgeGraph) -> usize {
    let valid_node_ids: HashSet<Uuid> = graph.nodes.keys().copied().collect();
    let mut pruned = 0;

    // Collect dangling edge IDs
    let dangling: Vec<Uuid> = graph.edges.iter()
        .filter(|(_, edge)| {
            if let Some(participants) = &edge.participants {
                // Hyperedge: ALL participants must exist
                participants.iter().any(|p| !valid_node_ids.contains(p))
            } else {
                // Binary edge: source and target must exist
                !valid_node_ids.contains(&edge.source) || !valid_node_ids.contains(&edge.target)
            }
        })
        .map(|(id, _)| *id)
        .collect();

    // Remove dangling edges and rebuild adjacency
    for id in &dangling {
        graph.edges.remove(id);
    }
    pruned = dangling.len();

    // Rebuild adjacency maps
    graph.rebuild_adjacency();

    pruned
}
```

### 7.3 Python-Rust Integration Pattern

The integration follows the established pattern: **Python orchestrates, Rust executes**.

```
┌──────────────────────────────────────────────────────────┐
│                        PYTHON                            │
│                                                          │
│  1. Load YAML template → TemplateConfig                 │
│  2. Build LLM prompts from template                     │
│  3. Call Ollama Cloud API:                               │
│     - Stage 1: Extract entities (LLM)                    │
│     - Stage 2: Extract relations with context (LLM)     │
│     - LLM merge (if strategy requires it)                │
│     - Knowledge chat Q&A (LLM)                           │
│  4. Call Rust for data operations:                      │
│     - detect_node_conflicts() → conflict report          │
│     - merge_nodes_into_collection(strategy) → merge      │
│     - upsert_nodes() / upsert_edges()                    │
│     - prune_dangling_edges()                             │
│     - search_nodes() / search_edges() → retrieval       │
│     - compute dedup keys, render display labels          │
│  5. Write to LanceDB (nodes, edges, chunks)             │
│                                                          │
└──────────────────────┬───────────────────────────────────┘
                       │ PyO3 (in-process, no IPC)
┌──────────────────────▼───────────────────────────────────┐
│                        RUST                              │
│                                                          │
│  - GraphNode / GraphEdge with new fields                 │
│  - DeterministicMergeStrategy (KeepFirst/Last/Overwrite) │
│  - KeyCompiler: pattern → dedup key function             │
│  - EntityResolver: exact + fuzzy resolution (existing)   │
│  - KnowledgeGraph: nodes, edges, adjacency (existing)    │
│  - prune_dangling_edges() for binary + hyperedges       │
│  - search_nodes() / search_edges() via LanceDB ANN      │
│  - WAL + batch writes (existing)                         │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### 7.4 Ollama Cloud API Usage for All LLM Features

All new LLM calls follow the existing pattern from `app/llm/extractor.py`:

```python
import httpx

OLLAMA_CLOUD_URL = settings.ollama_cloud_base_url  # e.g., "https://api.ollama.com/v1"
MODEL = settings.ollama_cloud_model                # e.g., "llama3.2"

async def call_ollama_cloud(system_prompt: str, user_prompt: str, response_format: dict = None) -> dict:
    """Generic Ollama Cloud API call (OpenAI-compatible)."""
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
    }
    if response_format:
        payload["response_format"] = response_format

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{OLLAMA_CLOUD_URL}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {settings.ollama_cloud_api_key}"},
        )
        resp.raise_for_status()
        return resp.json()
```

**LLM Call Points for Each Feature:**

| Feature | LLM Call Purpose | Endpoint Called |
|---|---|---|
| F2: Two-stage extraction | 1st call: extract entities. 2nd call: extract relations with entity context. | `/chat/completions` |
| F3: LLM merge | Send both entity versions + field schema → LLM synthesizes merged version. | `/chat/completions` with `response_format: {type: "json_object"}` |
| F5: Knowledge chat | Search nodes/edges → format context → LLM generates answer. | `/chat/completions` |
| F2: Edge prompt injection | `known_nodes` list injected into edge extraction prompt. | (part of Stage 2 call) |
| F6: Temporal/spatial prompts | `observation_time` / `observation_location` injected into prompts. | (part of extraction call) |

**Cost control**: All LLM calls go through `cost_tracker.create_tracker()` with per-job budget caps (existing mechanism from `app/services/cost_tracker.py`).

### 7.5 Feature Implementation Matrix — Rust vs. Python Responsibility

| Feature | Rust Responsibility | Python Responsibility |
|---|---|---|
| **F1: YAML templates** | None | Parse YAML, validate schemas, build prompt strings, TemplateGallery/Factory |
| **F2: Two-stage extraction** | None (chunking already done) | Build node/edge prompts, call Ollama Cloud (2 calls), merge results |
| **F3: Entity/edge merging** | `DeterministicMergeStrategy` (KeepFirst/Last/FieldOverwrite), conflict detection, key-based dedup | LLM-merge strategies (LLM_BALANCED, LLM_PREFER_FIRST/LAST), prompt construction, call Ollama Cloud |
| **F4: Incremental feeding** | `merge_nodes_into_collection()`, `merge_edges_into_collection()`, `upsert_nodes/edges`, `prune_dangling_edges()` | Orchestrate feed pipeline, call Rust for merge, call Python/LLM for conflicts |
| **F5: Knowledge chat** | `search_nodes()`, `search_edges()` (LanceDB ANN) | Format context, call Ollama Cloud Q&A, return answer + items |
| **F6: Temporal/spatial** | Add `time`/`location` fields, compute composite dedup keys, filter edges by time/location range | Inject `observation_time`/`observation_location` into prompts |
| **F7: Hyperedges** | Add `participants` field, `prune_dangling_edges()` with full-participant check, rebuild adjacency with hyperedges | Extract participants from LLM output, resolve participant names to UUIDs |
| **F8: Domain templates** | None | Author YAML files, TemplateGallery loads them |
| **F9: Extraction method registry** | None | Python protocol + dict registry |
| **F10: Identifiers & labels** | `KeyCompiler` (pattern → key function), store `display_label`/`dedup_key` on nodes/edges | Render labels from template patterns in extraction pipeline, pass to Rust via `upsert_nodes/edges` |

### 7.6 What Rust Cannot Do (and Why)

| Capability | Why Not Rust | Workaround |
|---|---|---|
| Call Ollama Cloud API | Rust's async HTTP (reqwest) + TLS + JSON parsing adds significant dependency weight. The PyO3 bridge already provides clean Python async. The GIL is released during Rust compute, so Python async LLM calls don't block Rust. | Python makes all LLM calls. Rust handles data operations. |
| Parse YAML template configs | `serde_yaml` can parse YAML, but template → prompt construction requires string templating, i18n resolution, and dynamic Pydantic model generation. These are naturally Python (Jinja2-like, Pydantic `create_model`). | Python parses templates. Rust receives the compiled results (schemas, keys, labels) as typed parameters. |
| Generate dynamic Pydantic models | `create_model()` is a Python runtime feature. Rust has no equivalent for generating Python classes dynamically. | Python-side only. |
| Run LLM-based merge logic | Requires calling Ollama Cloud and interpreting structured JSON output. The merge prompt must reference the template's field schema, which is Python-side. | Python-side. Rust detects the conflict and reports it; Python resolves via LLM; result is written back via Rust `upsert_*`. |