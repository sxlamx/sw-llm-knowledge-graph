# Bot 1 — Build: Phase 4 — Hybrid Search

## Your Role

You are implementing the hybrid search pipeline for `sw-llm-knowledge-graph`. This combines
vector (ANN), BM25 keyword, and graph proximity into a single fused ranking using parallel
Tokio channels with graceful degradation.

---

## Project Context

- **Search**: 3 parallel channels: vector (LanceDB ANN) + BM25 (Tantivy) + graph proximity (BFS)
- **Score fusion**: `0.6 * vector + 0.3 * keyword + 0.1 * graph` (configurable weights)
- **Performance target**: P50 < 200ms, P95 < 800ms (see `specifications/11-concurrency-performance.md`)
- **Over-fetch**: Each channel fetches 2× the requested limit for fusion deduplication

**Read these specs before writing any code:**
- `specifications/06-search-engine.md` — FULL spec: fusion weights, caching, timeouts, normalization
- `specifications/11-concurrency-performance.md` — concurrency model, performance targets
- `specifications/03-ingestion-pipeline.md` section 6 — embedding model (Qwen3, 1024-dim, query prompt)

---

## LESSONS.md Rules

1. **Tantivy batch committer**: Use Python asyncio task calling `flush_tantivy()` every 500ms via
   `run_in_executor` — NOT a persistent Rust Tokio background task from PyO3 methods
2. **Graceful degradation**: If one channel times out, return partial results from other channels
   rather than failing the entire search

---

## Implementation Tasks

### 1. Tantivy BM25 index (`rust-core/src/storage/search_engine.rs`)

Setup:
```rust
pub struct TantivyIndex {
    writer: Arc<Mutex<IndexWriter>>,   // Level 4 lock
    reader: IndexReader,
    schema: Schema,
}
```

Schema fields: `chunk_id (TEXT, STORED)`, `collection_id (TEXT, INDEXED)`, `text (TEXT, INDEXED, STORED)`, `doc_id (TEXT, STORED)`

- `index_chunk(chunk_id, collection_id, text, doc_id)` — add to writer buffer (no immediate commit)
- `flush()` — commit staged documents; called by Python every 500ms
- `search(query: &str, collection_id: &str, limit: usize) -> Vec<TantivyResult>` — BM25 search with collection filter

BM25 score normalization (sigmoid-like, maps to [0, 1]):
```rust
fn normalize_bm25(score: f32) -> f32 {
    score / (score + 1.0)
}
```

### 2. SearchEngine struct (`rust-core/src/storage/search_engine.rs`)

```rust
pub struct SearchEngine {
    lancedb: Arc<Database>,
    tantivy: Arc<TantivyIndex>,
    graph: Arc<RwLock<KnowledgeGraph>>,
    // LRU caches
    embedding_cache: Arc<Mutex<LruCache<String, Vec<f32>>>>,    // 1000 entries, 5min TTL
    neighborhood_cache: Arc<Mutex<LruCache<Uuid, (Vec<Uuid>, u64)>>>,  // 500 entries, version+TTL
    search_semaphore: Arc<Semaphore>,  // 100 permits
}
```

### 3. Hybrid search orchestration

```rust
pub async fn hybrid_search(
    &self,
    query: str,
    query_embedding: Vec<f32>,
    collection_id: Uuid,
    limit: usize,
    weights: SearchWeights,  // {vector: 0.6, keyword: 0.3, graph: 0.1}
    topic_filter: Option<Vec<String>>,
) -> Result<Vec<SearchResult>> {
    let _permit = self.search_semaphore.acquire().await?;
    let over_fetch = limit * 2;

    // Run 3 channels concurrently
    let (vector_results, keyword_results, graph_results) = tokio::join!(
        self.vector_search_channel(query_embedding.clone(), collection_id, over_fetch, topic_filter.clone()),
        self.keyword_search_channel(query, collection_id, over_fetch),
        self.graph_proximity_channel(query_embedding, collection_id, over_fetch),
    );

    // Fuse results with weighted scores
    fuse_results(vector_results, keyword_results, graph_results, weights, limit)
}
```

Per-channel timeouts:
- Vector: 600ms
- Keyword: 200ms
- Graph proximity: 300ms

On timeout: channel returns empty Vec (graceful degradation — other channels still contribute).

### 4. Score fusion (`fuse_results`)

```rust
fn fuse_results(
    vector: Vec<(String, f32)>,     // (chunk_id, score)
    keyword: Vec<(String, f32)>,    // (chunk_id, score)
    graph: Vec<(String, f32)>,      // (chunk_id, score)
    weights: SearchWeights,
    limit: usize,
) -> Vec<SearchResult> {
    // 1. Build score map per chunk_id
    // 2. final_score = w.vector * v_score + w.keyword * k_score + w.graph * g_score
    // 3. Sort by final_score descending
    // 4. Return top `limit` results
}
```

### 5. Graph proximity channel

Graph proximity finds top-K entity nodes semantically close to the query embedding, then
performs BFS from those seeds and returns chunk IDs that mention those nodes:

```rust
async fn graph_proximity_channel(
    &self,
    query_embedding: Vec<f32>,
    collection_id: Uuid,
    limit: usize,
) -> Vec<(String, f32)> {
    // 1. Find top-5 entity nodes by embedding similarity to query
    // 2. BFS from each seed, max 2 hops
    // 3. Collect chunk_ids from edges of type Mentions
    // 4. Score = seed similarity score * hop_decay (1.0 / (hop + 1))
}
```

### 6. Topic pre-filter

When `topic_filter` is provided, apply LanceDB predicate to chunks table before vector search:
```rust
// LanceDB where clause: "array_has_any(topics, ['machine learning', 'neural networks'])"
let filter = format!("array_has_any(topics, [{}])", topic_list_sql);
```

### 7. LRU caches

- **Embedding cache**: Key = query text (first 100 chars), value = `Vec<f32>`, 1000 entries, 5-min TTL
  - Cache hit: return immediately without calling embedder
  - Invalidated by: nothing (query embeddings are stateless)

- **Neighborhood cache**: Key = node UUID, value = `(neighbor_ids, graph_version)`, 500 entries, 2-min TTL
  - Invalidated by: graph version mismatch (check `KnowledgeGraph.version` on cache read)

### 8. Python search service (`python-api/app/core/search_service.py`)

```python
async def hybrid_search(
    query: str,
    collection_id: str,
    mode: str = "hybrid",   # hybrid | vector | keyword | graph
    limit: int = 20,
    topics: list[str] | None = None,
) -> list[dict]:
    if mode == "vector":
        embedding = await embed_query(query)
        return await rust_vector_search_async(embedding, collection_id, limit)
    elif mode == "keyword":
        return await rust_keyword_search_async(query, collection_id, limit)
    elif mode == "hybrid":
        embedding = await embed_query(query)
        return await rust_hybrid_search_async(embedding, query, collection_id, limit, topics)
    elif mode == "graph":
        # Graph-only traversal from query entity match
        ...
```

### 9. Tantivy flush loop (`python-api/app/main.py`)

```python
async def tantivy_flush_loop():
    """Call Rust flush_tantivy() every 500ms to commit staged documents."""
    while True:
        await asyncio.sleep(0.5)
        if RUST_AVAILABLE:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, search_engine.flush_tantivy)

# In FastAPI lifespan:
asyncio.create_task(tantivy_flush_loop())
```

---

## Constraints

- Per-channel timeouts must be independent — one timeout does NOT cancel other channels
- Score normalization must map all channel scores to [0, 1] range before fusion
- Tantivy writer committer runs in Python asyncio (NOT persistent Rust Tokio task from PyO3)
- Over-fetch factor is 2× (not configurable at this stage)

---

## Acceptance Criteria

1. `POST /search` with `mode: "hybrid"` returns fused results from multiple channels
2. `POST /search` with `mode: "keyword"` returns non-empty BM25 results (not stub `[]`)
3. Keyword search uses Tantivy — verify `flush_tantivy()` is called periodically
4. Individual channel timeout does not fail entire search (graceful degradation)
5. Topic filter applied to vector channel when `topics` param provided
6. P50 search latency < 200ms (measured with Criterion benchmark)
7. LRU embedding cache returns hit on repeated identical query (no model re-call)
8. Neighborhood cache invalidates on graph version change
