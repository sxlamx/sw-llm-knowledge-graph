# Bot 1 — Build: Phase 3 — Knowledge Graph Engine

## Your Role

You are a senior engineer implementing the knowledge graph engine for `sw-llm-knowledge-graph`.
This covers the ontology system, entity resolution, graph construction, BFS/Dijkstra traversal,
and the Python graph API endpoints. The graph is populated from NER tags extracted in Phase 2.

---

## Project Context

- **Graph population method**: NER tags from `ner_tagger.py` (always-on) drive the primary graph.
  LLM entity/relation extraction is optional (gated behind `settings.enable_contextual_prefix`).
- **Storage**: Hot layer = in-memory `petgraph::StableGraph` (directed, weighted);
  Cold layer = LanceDB `{collection_id}_nodes` and `{collection_id}_edges` tables
- **Canonical NER labels**: ORGANIZATION (not ORG), LOCATION (not GPE/LOC), PERSON, DATE, MONEY, PERCENT, LAW
  See `SPACY_TO_CANONICAL` in `ner_tagger.py`

**Read these specs before writing any code:**
- `specifications/04-ontology-engine.md` — entity type hierarchy, relationship constraints, validator
- `specifications/07-graph-engine.md` — entity resolution algorithm, merge strategy, graph ops, pruning
- `specifications/14-ner-pipeline.md` — canonical label names (SPACY_TO_CANONICAL), legal labels
- `specifications/02-data-models.md` — nodes/edges LanceDB table schemas

---

## LESSONS.md Rules (Non-Negotiable)

1. **Canonical labels in graph**: Nodes are stored with canonical labels (`ORGANIZATION`, `LOCATION`),
   not spaCy shorthand (`ORG`, `GPE`). All NodeType comparisons and node inserts must use canonical names.
2. **Two-phase write**: Always write to LanceDB first (durable), THEN acquire brief write lock on
   in-memory `KnowledgeGraph` for update. Never write petgraph without writing LanceDB first.
3. **Graph traversal return types**: `find_shortest_path` returns `Vec<PathStep>` (alternating
   Node/Edge items, not Vec<Uuid>). `bfs_reachable` INCLUDES the seed node in its returned HashSet.
4. **SPACY_TO_CANONICAL mapping**: Use canonical names consistently everywhere — NodeType enum,
   LanceDB node records, API responses, and frontend node color mapping all use canonical names.

---

## Implementation Tasks (in dependency order)

### 1. Ontology types (`rust-core/src/ontology/types.rs`)

```rust
pub struct EntityTypeDef {
    pub name: String,
    pub parent: Option<String>,
    pub description: String,
    pub attributes: Vec<AttributeDef>,
    pub aliases: Vec<String>,
}

pub struct RelationshipTypeDef {
    pub name: String,
    pub domain: String,            // source entity type
    pub range: String,             // target entity type
    pub inverse: Option<String>,
    pub description: String,
}

pub struct Ontology {
    pub version: String,
    pub entity_types: HashMap<String, EntityTypeDef>,
    pub relationship_types: HashMap<String, RelationshipTypeDef>,
}
```

Default ontology entity types (matching SPACY_TO_CANONICAL canonical names):
- PERSON, ORGANIZATION, LOCATION, DATE, MONEY, PERCENT, LAW, CONCEPT, EVENT, DOCUMENT
- Legal: LEGISLATION_TITLE, LEGISLATION_REFERENCE, STATUTE_SECTION, COURT_CASE, JURISDICTION,
  LEGAL_CONCEPT, DEFINED_TERM, COURT, JUDGE, LAWYER, PETITIONER, RESPONDENT, WITNESS, CASE_CITATION

### 2. Ontology validator (`rust-core/src/ontology/validator.rs`)

```rust
pub trait ValidationRule: Send + Sync {
    fn check(&self, entity: &ExtractedEntity, ontology: &Ontology) -> Result<(), String>;
}

pub struct OntologyValidator {
    rules: Vec<Box<dyn ValidationRule>>,
}
```

Built-in rules:
- `KnownEntityTypeRule` — entity_type must be in `ontology.entity_types`
- `ConfidenceThresholdRule` — confidence >= 0.3
- `DomainRangeRule` — relationship source/target types match ontology definition

`validate_extraction_result(result, ontology) -> ValidationReport` — classify each entity/rel as valid or dropped.

### 3. Entity resolver (`rust-core/src/graph/builder.rs`)

```rust
pub struct EntityResolver {
    levenshtein_threshold: usize,   // default 3
    embedding_threshold: f32,       // default 0.92
}

impl EntityResolver {
    pub fn resolve(candidate, existing_nodes, candidate_embedding) -> Resolution
}
```

Resolution algorithm (3 steps):
1. **Exact match**: normalize name (lowercase, trim) → match against `label` + `aliases`
2. **Levenshtein**: `strsim::levenshtein(a, b) < threshold` AND same entity_type
3. **Cosine similarity**: candidate embedding vs node embedding > 0.92 threshold

Merge strategy when merging:
- Keep earliest `id` (canonical)
- Union `aliases` lists
- Average `confidence`
- Keep longer `description`
- Merge `properties` maps (newer values win)

### 4. Graph construction from NER (`python-api/app/pipeline/build_graph_from_ner.py`)

This is the primary graph building script for Phase 2 (NER-based, no LLM required):

```python
async def build_graph_from_ner(collection_id: str) -> dict:
    """
    Build knowledge graph nodes and edges from NER-tagged chunks.

    Algorithm:
    1. Load all chunks with ner_tags from LanceDB {collection_id}_chunks
    2. Group all NerTag objects by canonical label
    3. For each tag: create or merge into existing entity node
    4. For each chunk: create co-occurrence edges between all entity pairs in same chunk
    5. Write nodes batch to {collection_id}_nodes (LanceDB first)
    6. Write edges batch to {collection_id}_edges (LanceDB first)
    7. Update in-memory KnowledgeGraph via Rust bridge (brief write lock)
    8. Return {added_nodes, merged_nodes, added_edges}
    """
```

Node schema: `id`, `collection_id`, `label`, `entity_type` (canonical), `description=""`
  `aliases=[]`, `confidence=1.0` (NER confidence), `metadata={}`, `created_at`

Edge schema: `id`, `collection_id`, `source_id`, `target_id`, `predicate="co_occurrence"`,
  `weight=1.0`, `context=chunk_text[:200]`, `chunk_id`, `created_at`

### 5. Graph traversal (`rust-core/src/graph/traversal.rs`)

**BFS**: `bfs_reachable(graph, start: Uuid, max_hops: usize, min_weight: f32) -> HashSet<Uuid>`
- Includes seed node in result (seed popped from frontier → added to visited)
- Prunes edges with weight < min_weight
- Returns all reachable node IDs within max_hops

**BFS subgraph**: `bfs_subgraph(graph, start, max_hops, min_weight) -> SubGraph`
- Returns nodes + edges for the visited subgraph

**Dijkstra**: `find_shortest_path(graph, start, end) -> Vec<PathStep>`
- Returns `Vec<PathStep>` where PathStep is `Node(GraphNode)` or `Edge(GraphEdge)` (alternating)
- Edge cost = `1.0 / weight` (higher weight = lower cost = preferred)
- Returns empty Vec if no path

### 6. Graph export (`rust-core/src/graph/export.rs`)

- `export_json(graph: &KnowledgeGraph) -> String` — serialize nodes + edges as JSON
- `export_graphml(graph: &KnowledgeGraph) -> String` — GraphML XML format

### 7. Graph API endpoints (`python-api/app/routers/graph.py`)

- `GET /graph/subgraph?collection_id=xxx&node_id=yyy&depth=2` — BFS subgraph
- `GET /graph/nodes/{id}` — single node with 1-hop neighbors
- `GET /graph/path?collection_id=xxx&from_id=aaa&to_id=bbb` — Dijkstra path
- `GET /graph/export?collection_id=xxx&format=json` — full graph export
- `PUT /graph/nodes/{id}` — edit node label/description
- `POST /graph/edges` — add manual edge
- `DELETE /graph/edges/{id}` — delete edge
- `GET /graph/nodes/{id}/summary` — LLM summary (graceful fallback to static when Ollama unavailable)

**Node summary fallback**: When `settings.ollama_cloud_base_url` is not configured or LLM fails,
return static summary: `"{label} is a {entity_type} entity. {description}"`.

### 8. Ontology API (`python-api/app/routers/ontology.py`)

- `GET /ontology?collection_id=xxx` — return current ontology (default or collection-specific)
- `PUT /ontology?collection_id=xxx` — update ontology from JSON body

---

## Constraints

- Node `entity_type` field must always use canonical labels (ORGANIZATION, LOCATION, not ORG, GPE)
- LanceDB write before petgraph write — never reverse
- BFS seed node IS included in result set
- Node summary endpoint MUST NOT return 502 when LLM is unavailable — use fallback
- Entity resolution threshold: cosine >= 0.92 (not 0.8 or 0.9)

---

## Acceptance Criteria

1. `build_graph_from_ner("collection-id")` populates `{collection_id}_nodes` and `_edges` tables
2. Entity nodes use canonical `entity_type` values (ORGANIZATION not ORG)
3. `bfs_subgraph(start, max_hops=2)` returns subgraph including seed node
4. `find_shortest_path(a, b)` returns alternating Node/Edge PathStep sequence
5. `GET /graph/nodes/{id}/summary` returns JSON with `summary` field, never 502
6. `PUT /graph/nodes/{id}` updates label and logs feedback
7. `GET /graph/export?format=json` returns all nodes and edges in collection
8. OntologyValidator drops entities with unknown type or confidence < 0.3
9. EntityResolver merges "Apple Inc" and "Apple" (Levenshtein distance 4 > threshold, cosine > 0.92)
10. Dijkstra returns empty path for disconnected nodes (no panic)
