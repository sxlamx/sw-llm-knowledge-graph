# Bot 1 — Build: Phase 10 (Knowledge Chat + Temporal/Spatial + Hyperedges)

> **Features**: F5 (Knowledge Chat) + F6 (Temporal/Spatial Graph Dimensions) + F7 (Hyperedge Support)
> **Spec References**: `15-hyper-extract-integration.md` Sections 2.5, 2.6, 2.7, 7.2
> **Depends On**: Phase 7 (templates, data model extensions), Phase 8 (extraction), Phase 9 (merge)

---

## Role

You are a senior engineer implementing knowledge chat, temporal/spatial graph dimensions, and hyperedge support for the sw-llm-knowledge-graph project.

---

## LESSONS.md Rules

- NER model: always `en_core_web_trf`, never `en_core_web_sm`
- LanceDB for all metadata, no PostgreSQL
- Embeddings: `Qwen/Qwen3-Embedding-0.6B`, 1024-dim
- NER labels: canonical only
- All LLM calls use Ollama Cloud API via `call_ollama_cloud()`
- Lock ordering: L1 → L2 → L3 → L4

---

## Implementation Tasks

### Task 1: Rust — search_nodes and search_edges

In `rust-core/src/lib.rs`, add to `IndexManager`:

```rust
fn search_nodes(&self, collection_id: &str, query_embedding_json: &str, top_k: usize) -> PyResult<String> {
    // 1. Parse query embedding from JSON
    // 2. Open {collection_id}_nodes LanceDB table
    // 3. Perform ANN search using the embedding column
    // 4. Return JSON array of matching nodes with similarity scores
}

fn search_edges(&self, collection_id: &str, query_embedding_json: &str, top_k: usize,
                time_from: Option<&str>, time_to: Option<&str>,
                location: Option<&str>) -> PyResult<String> {
    // 1. Parse query embedding from JSON
    // 2. Open {collection_id}_edges LanceDB table
    // 3. Perform ANN search with optional filters:
    //    - time_from/time_to: filter edges where time >= time_from AND time <= time_to
    //    - location: filter edges where location matches (exact or substring)
    // 4. Return JSON array of matching edges with similarity scores
}
```

**Note**: If LanceDB vector search is not yet available in Rust, implement in Python using `lancedb.table.search()` and expose via the `rust_bridge.py` async helpers. The Rust methods can be placeholders that delegate to Python LanceDB.

### Task 2: Python — Knowledge Chat Service

Create `python-api/app/services/knowledge_chat.py`:

```python
class KnowledgeChatService:
    """Chat over extracted knowledge graph entities and relations."""

    def __init__(self, collection_id: str, template: TemplateConfig | None = None):
        self.collection_id = collection_id
        self.template = template

    async def search_knowledge(self, query: str, top_k_nodes: int = 5,
                                top_k_edges: int = 5) -> Tuple[List[dict], List[dict]]:
        """Search nodes and edges by query using vector similarity."""
        from app.core.rust_bridge import get_index_manager
        from app.llm.embedder import embed_query

        im = get_index_manager()
        query_embedding = await embed_query(query)
        embedding_json = json.dumps(query_embedding)

        nodes = json.loads(im.search_nodes(self.collection_id, embedding_json, top_k_nodes))
        edges = json.loads(im.search_edges(self.collection_id, embedding_json, top_k_edges))
        return nodes, edges

    async def chat(self, query: str, top_k_nodes: int = 5,
                   top_k_edges: int = 5) -> dict:
        """Knowledge chat: search → format context → LLM Q&A."""
        nodes, edges = await self.search_knowledge(query, top_k_nodes, top_k_edges)

        context_parts = []
        if nodes:
            context_parts.append("=== Relevant Entities ===")
            for node in nodes:
                context_parts.append(json.dumps(node, indent=2, default=str))
        if edges:
            context_parts.append("=== Relevant Relations ===")
            for edge in edges:
                context_parts.append(json.dumps(edge, indent=2, default=str))

        context = "\n\n".join(context_parts) if context_parts else "No relevant information found."

        system_prompt = "You are a knowledge graph assistant. Answer the user's question based on the provided knowledge graph data. Cite specific entities and relationships when possible."

        response = await call_ollama_cloud(
            system_prompt=system_prompt,
            user_prompt=f"Based on the following knowledge graph data, answer the question.\n\n{context}\n\nQuestion: {query}",
        )

        return {
            "answer": response["content"],
            "nodes": nodes,
            "edges": edges,
        }
```

### Task 3: Chat API Endpoint

Create `python-api/app/routers/chat.py`:

```python
router = APIRouter(prefix="/api/v1/collections", tags=["chat"])

@router.post("/{collection_id}/chat")
async def knowledge_chat(
    collection_id: str,
    request: ChatRequest,
    user: dict = Depends(get_current_user),
):
    """Chat with the knowledge graph for a collection."""
    # Validate ownership
    # Load template if configured for the collection
    service = KnowledgeChatService(collection_id, template)
    result = await service.chat(
        query=request.query,
        top_k_nodes=request.top_k_nodes,
        top_k_edges=request.top_k_edges,
    )
    return result
```

Add `ChatRequest` to `python-api/app/models/schemas.py`:

```python
class ChatRequest(BaseModel):
    query: str
    top_k_nodes: int = 5
    top_k_edges: int = 5
```

### Task 4: Temporal Graph Extraction Template Support

In `python-api/app/llm/two_stage_extractor.py`, add temporal prompt injection:

```python
def _build_edge_system_prompt(self) -> str:
    parts = ["You are an expert relationship extraction specialist."]
    # ... existing parts ...

    # Temporal context injection (F6)
    if self.template.identifiers and self.template.identifiers.time_field:
        parts.append(f"\n### Temporal Extraction Rules")
        parts.append(f"Current Observation Date: {self._get_observation_time()}")
        parts.append('1. Resolve relative time expressions (e.g., "last year", "yesterday") based on the observation date.')
        parts.append('2. Keep explicit dates exactly as written (e.g., "2024", "2024-01-15").')
        parts.append('3. If no time information, leave the time field empty. DO NOT hallucinate dates.')

    # Spatial context injection (F6)
    if self.template.identifiers and self.template.identifiers.location_field:
        parts.append(f"\n### Spatial Extraction Rules")
        parts.append(f"Current Observation Location: {self._get_observation_location()}")
        parts.append('1. Resolve relative location expressions based on the observation location.')
        parts.append('2. Keep explicit locations exactly as written.')
        parts.append('3. If no location information, leave the location field empty. DO NOT hallucinate.')

    return "\n".join(parts)

def _get_observation_time(self) -> str:
    """Default to today in YYYY-MM-DD format. Override via template options."""
    from datetime import date
    return date.today().isoformat()

def _get_observation_location(self) -> str:
    """Default to 'Unknown'. Override via template options."""
    return "Unknown"
```

### Task 5: Temporal/Spatial Edge Dedup Key Composition

In `python-api/app/services/template_factory.py`, update key compilation for temporal/spatial templates:

```python
def _build_key_extractors(config: TemplateConfig) -> KeyExtractors:
    base_pattern = config.identifiers.relation_key

    # Compose temporal key: append @{time}
    if config.identifiers.time_field and "@{time}" not in base_pattern and "{time}" not in base_pattern:
        time_aware_pattern = f"{base_pattern} @ {{{config.identifiers.time_field}}}"
    else:
        time_aware_pattern = base_pattern

    # Compose spatial key: append @{location}
    if config.identifiers.location_field and "@{location}" not in base_pattern and "{location}" not in base_pattern:
        location_aware_pattern = f"{time_aware_pattern} at {{{config.identifiers.location_field}}}"
    else:
        location_aware_pattern = time_aware_pattern

    entity_key_fn = _compile_key_pattern(config.entity_schema.key)
    relation_key_fn = _compile_key_pattern(location_aware_pattern)
    return KeyExtractors(entity=entity_key_fn, relation=relation_key_fn)
```

### Task 6: Temporal/Spatial YAML Templates

Create `templates/presets/general/temporal_graph.yaml`:

```yaml
name: temporal_graph
type: temporal_graph
domain: general
language: [en]
description: "Extract time-aware knowledge graph from text."
entity_schema:
  fields:
    - name: name
      type: string
      description: "Entity name"
      required: true
    - name: entity_type
      type: string
      description: "person|organization|location|concept|event"
      required: true
    - name: description
      type: string
      required: false
  key: "{name}"
  display_label: "{name} ({entity_type})"
relation_schema:
  fields:
    - name: source
      type: string
      required: true
    - name: target
      type: string
      required: true
    - name: predicate
      type: string
      required: true
    - name: time
      type: string
      description: "Time of the relationship. YYYY or YYYY-MM-DD format. Leave empty if unknown."
      required: false
    - name: context
      type: string
      required: false
  key: "{source}|{predicate}|{target}"
  source_field: source
  target_field: target
  display_label: "{predicate} @{time}"
identifiers:
  entity_key: name
  relation_key: "{source}|{predicate}|{target}"
  relation_source: source
  relation_target: target
  time_field: time
extraction:
  mode: two_stage
  edge_prompt_extra: "Record the time of each relationship when explicitly stated. Do not infer dates not in the text."
```

Create `templates/presets/general/spatial_graph.yaml` and `spatio_temporal_graph.yaml` following the same pattern.

### Task 7: Hyperedge Extraction and Pruning

The extraction logic for hypergraphs is already in `TwoStageExtractor`. The key additions:

1. **Hyperedge relation key**: `"{name}|{type}"` with `participants` list field
2. **Hyperedge dangling pruning**: Check ALL participants exist (implemented in Phase 8 `EdgePruner`)
3. **Adjacency rebuild**: For hyperedges, add each participant to the adjacency maps of all other participants

In `rust-core/src/graph/builder.rs`, update `insert_edges_batch()`:

```rust
// When edge has participants:
// For each participant p in participants:
//   Add (edge_id, p) to adjacency_out of all OTHER participants
//   Add (edge_id, other) to adjacency_in of p
```

### Task 8: Frontend — Chat Panel Component

Create `frontend/src/components/chat/ChatPanel.tsx`:

- Chat input with send button
- Message history (user queries + assistant answers)
- Toggle mode: "Search Chunks" vs "Ask Knowledge Graph"
- Display retrieved nodes/edges alongside the answer
- Uses `POST /api/v1/collections/{collectionId}/chat`

Add `chatApi` to RTK Query:

```typescript
injectEndpoints({
  endpoints: (builder) => ({
    knowledgeChat: builder.mutation<ChatResponse, ChatRequest>({
      query: ({ collectionId, ...body }) => ({
        url: `/collections/${collectionId}/chat`,
        method: 'POST',
        body,
      }),
    }),
  }),
});
```

### Task 9: Frontend — Temporal/Spatial Edge Display

In the graph viewer, when edges have `time` or `location`:

- Show `@2024` or `@New York` badge on edge labels
- Add filter controls for time range (date picker) and location (text input)
- Pass filter params to `GET /graph/subgraph` endpoint

---

## Constraints

1. **All LLM calls use `call_ollama_cloud()`** — knowledge chat, temporal time resolution, all of it.
2. **search_nodes/search_edges** must use embeddings from the `nodes` and `edges` LanceDB tables, not just the `chunks` table.
3. **Temporal/spatial fields are optional** on edges. Templates that don't specify `time_field`/`location_field` should not include temporal/spatial prompts.
4. **Hyperedge adjacency** is bidirectional between all participants (not just source→target).
5. **Chat endpoint is rate-limited** — counts against the 5 LLM-heavy requests per minute per user.
6. **Chat responses include retrieved items** — the API returns both the answer and the retrieved nodes/edges for transparency.
7. **Observation time** defaults to today's date, but templates can override it (e.g., for historical documents).

---

## Acceptance Criteria

1. `search_nodes()` in Rust returns top-k similar nodes from LanceDB via vector search.
2. `search_edges()` supports optional `time_from`/`time_to`/`location` filters.
3. `KnowledgeChatService.chat()` returns answer + retrieved nodes + retrieved edges.
4. `POST /api/v1/collections/{id}/chat` endpoint works with authentication.
5. Temporal templates inject `observation_time` into edge extraction prompts.
6. Spatial templates inject `observation_location` into edge extraction prompts.
7. Spatio-temporal templates inject both.
8. Dedup keys for temporal edges include `@{time}`. For spatial, `@{location}`.
9. Hyperedges stored with `participants` list and adjacency maps connect all participants.
10. Frontend ChatPanel renders chat messages and retrieved knowledge items.