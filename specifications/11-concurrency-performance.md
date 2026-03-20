# 11 — Concurrency and Performance

## 1. Overview

This specification details the complete concurrency architecture of the Rust core engine — the most
performance-critical part of the system. The design achieves three goals simultaneously:

1. **Multiple concurrent searches** — 100 simultaneous search operations, none blocking another
2. **Non-blocking index updates** — writes never pause in-flight searches
3. **Predictable latency** — P95 < 800ms regardless of background work

The key insight is that each storage layer has a different concurrency model that we exploit:
- **LanceDB**: MVCC at the storage level — reads on old snapshots while writes commit atomically
- **petgraph in-memory**: `Arc<RwLock<_>>` — many concurrent readers, brief exclusive writes
- **Tantivy**: `IndexReader` is lock-free clone; `IndexWriter` requires exclusive `Mutex`

---

## 2. Full Concurrency Architecture

```
HTTP Requests (FastAPI/uvicorn — AsyncIO event loop)
        │
        │  PyO3 FFI — py.allow_threads() releases GIL
        ▼
Tokio Multi-Thread Runtime (8 worker threads)
        │
        │
┌───────┴──────────────────────────────────────────────────────────────────┐
│                           IndexManager                                    │
│                                                                           │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │  state: AtomicU8         (lock-free state reads, CAS transitions) │    │
│  │  pending_writes: AtomicU64 (lock-free write count tracking)       │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                                                                           │
│  ┌──────────────────┐  ┌─────────────────┐  ┌──────────────────────┐    │
│  │  search_semaphore│  │ write_semaphore  │  │  llm_semaphore       │    │
│  │  Semaphore(100)  │  │  Semaphore(1)   │  │  Semaphore(20)       │    │
│  │  Max concurrent  │  │  Serializes all │  │  Max concurrent LLM  │    │
│  │  searches        │  │  batch writes   │  │  API calls           │    │
│  └──────────────────┘  └─────────────────┘  └──────────────────────┘    │
│                                                                           │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │              Storage Handles (per collection)                      │  │
│  │                                                                    │  │
│  │  tables: Arc<RwLock<HashMap<String, Arc<Table>>>>                  │  │
│  │  │                                                                 │  │
│  │  │  Search reads:  tables.read() → clone Arc<Table> → release     │  │
│  │  │                 LanceDB MVCC: 0 additional locks for reads      │  │
│  │  │  Index writes:  tables.read() → clone Arc<Table> → release     │  │
│  │  │                 (write_semaphore serializes the add() call)     │  │
│  │  │  Index swap:    tables.write() → HashMap::insert() → release   │  │
│  │  │                 (held only for pointer swap, microseconds)      │  │
│  │                                                                    │  │
│  │  graphs: Arc<RwLock<HashMap<String, Arc<RwLock<KnowledgeGraph>>>>>│  │
│  │  │                                                                 │  │
│  │  │  BFS/Dijkstra: graphs.read() → clone Arc → release             │  │
│  │  │               graph.read() — many concurrent OK               │  │
│  │  │  Node insert:  graphs.read() → clone Arc → release             │  │
│  │  │               graph.write() — brief, batch only               │  │
│  │                                                                    │  │
│  │  tantivy: Arc<RwLock<HashMap<String, Arc<TantivyHandle>>>>        │  │
│  │  │                                                                 │  │
│  │  │  BM25 search:  tantivy.read() → clone Arc → release            │  │
│  │  │               handle.reader.searcher() — lock-free            │  │
│  │  │  Index write:  handle.writer.lock() — Mutex (serialized)       │  │
│  │  │               commits batched every 500ms                     │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                           │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │              Caches                                                │  │
│  │  query_embedding_cache: Arc<Mutex<LruCache<...>>>  capacity=1000   │  │
│  │  graph_neighbor_cache:  Arc<Mutex<LruCache<...>>>  capacity=500    │  │
│  │  (cleared on each successful index write)                          │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                           │
│  wal: Arc<Mutex<WalWriter>>  (append-only, flushed per write)            │
└───────────────────────────────────────────────────────────────────────────┘
        │
        ├──► Rayon Thread Pool (CPU-bound work)
        │    - RecordBatch construction
        │    - Ontology validation batches
        │    - BFS parallel hop processing
        │    - Entity resolution batches
        │
        └──► Background tokio tasks
             - IVF-PQ shadow index build (non-blocking)
             - Tantivy batch committer (500ms interval)
             - Graph pruning (hourly)
             - WAL checkpoint (on restart)
```

---

## 3. Tokio Runtime Configuration

```rust
// rust-core/src/lib.rs (or main.rs if using Rust HTTP server)

#[tokio::main(flavor = "multi_thread", worker_threads = 8)]
async fn main() -> Result<()> {
    // Configure Tokio runtime
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(8)
        .enable_all()
        // Stack size per worker thread (default 2MB is fine)
        .thread_stack_size(2 * 1024 * 1024)
        .thread_name("kg-worker")
        .build()?;

    runtime.block_on(async_main())
}
```

**CPU-bound work** is always offloaded to `tokio::task::spawn_blocking` or Rayon to prevent
starving the Tokio I/O executor:

```rust
// Pattern: Rayon for CPU parallelism (does not block Tokio)
let result = tokio::task::spawn_blocking(move || {
    // CPU-intensive work here runs in a dedicated blocking thread pool
    build_record_batch_from_chunks(chunks)
}).await??;

// Alternative: direct Rayon parallel iterator
use rayon::prelude::*;
let validated: Vec<_> = entities
    .par_iter()
    .map(|e| ontology.validate_entity(e))
    .collect();
```

---

## 4. Concurrency Primitives Reference Table

| Operation | Primitive | Where | Rationale |
|-----------|-----------|-------|-----------|
| LanceDB table handle access | `Arc<RwLock<HashMap<String, Arc<Table>>>>` | `IndexManager.tables` | Multiple concurrent searches; exclusive only for HashMap mutation (index swap) |
| LanceDB batch write | `Semaphore(1)` (`write_semaphore`) | `IndexManager` | Serialize batch inserts; LanceDB handles internal MVCC |
| In-memory graph reads | `Arc<RwLock<KnowledgeGraph>>` read guard | Per collection | BFS/Dijkstra traversal: many concurrent readers, zero blocking |
| In-memory graph writes | `Arc<RwLock<KnowledgeGraph>>` write guard | Per collection | Brief (ms) for HashMap inserts; held only after LanceDB write succeeds |
| Tantivy search | None (IndexReader is `Clone`) | `TantivyHandle.reader` | Completely lock-free; each searcher is an independent snapshot |
| Tantivy index write | `Arc<Mutex<IndexWriter>>` | `TantivyHandle.writer` | Tantivy requires exclusive writer; compensated by batching |
| Ingest job queue | `tokio::sync::mpsc::channel(256)` | `JobManager` | Non-blocking job dispatch; MPSC allows multiple producers |
| Concurrent search limit | `tokio::sync::Semaphore(100)` | `IndexManager` | Back-pressure without rejection; callers queue rather than fail |
| Concurrent LLM calls | `tokio::sync::Semaphore(20)` | Ingest pipeline | Respect OpenAI rate limits; exponential backoff on 429 |
| Index state transitions | `Arc<AtomicU8>` + `compare_exchange` | `IndexManager.state` | Lock-free state reads; CAS prevents concurrent compaction launches |
| Write count tracking | `Arc<AtomicU64>` | `IndexManager.pending_writes` | Lock-free accumulation; triggers compaction threshold check |
| LRU embedding cache | `Arc<Mutex<LruCache<...>>>` | `IndexManager` | Fine-grained, short-lived lock; read after hash lookup |
| LRU graph cache | `Arc<Mutex<LruCache<...>>>` | `IndexManager` | Same as above; separate cache to avoid cross-type eviction |
| WAL writes | `Arc<Mutex<WalWriter>>` | `IndexManager.wal` | Append-only; lock held only for `writeln!` + `flush()` |
| Ontology hot-reload | `Arc<RwLock<Ontology>>` | `OntologyManager` | Reload is rare; reads never blocked by each other |
| Collection ownership | `Arc<DashMap<Uuid, Uuid>>` | Optional fast-path | Lock-free concurrent reads for collection→user mapping |

---

## 5. Lock Ordering (Deadlock Prevention)

**RULE: Locks must ALWAYS be acquired in the following order. Never reverse.**

```
Level 1 — Atomic, lock-free (no ordering concern):
  IndexManager.state           (AtomicU8)
  IndexManager.pending_writes  (AtomicU64)
  KnowledgeGraph.version       (AtomicU64, inside KnowledgeGraph)

Level 2 — Outer HashMap locks (BRIEF — clone Arc, then release):
  IndexManager.tables          (RwLock<HashMap<String, Arc<Table>>>)
  IndexManager.graphs          (RwLock<HashMap<String, Arc<RwLock<KnowledgeGraph>>>>)
  IndexManager.tantivy_indexes (RwLock<HashMap<String, Arc<TantivyHandle>>>)
  IndexManager.ontology_managers (RwLock<HashMap<...>>)

Level 3 — Per-collection operational locks:
  KnowledgeGraph               (RwLock<KnowledgeGraph>, cloned Arc from Level 2)
  TantivyHandle.writer         (Mutex<IndexWriter>, from cloned Arc at Level 2)
  OntologyManager.ontology     (RwLock<Ontology>, from cloned Arc at Level 2)

Level 4 — Leaf utility locks (never acquire another lock while holding):
  IndexManager.query_embedding_cache  (Mutex<LruCache>)
  IndexManager.graph_neighbor_cache   (Mutex<LruCache>)
  IndexManager.wal                    (Mutex<WalWriter>)
```

**The correct pattern for all Level 2 → Level 3 transitions:**

```rust
// CORRECT — clone the Arc before releasing Level 2, then acquire Level 3
async fn correct_pattern(index_manager: &IndexManager, collection_id: &str) {
    // Step 1: acquire Level 2 read lock
    let inner_arc = {
        let outer_map = index_manager.graphs.read().await;   // Level 2 acquired
        outer_map.get(collection_id)
            .expect("collection exists")
            .clone()                                          // clone Arc<RwLock<KG>>
    };  // Level 2 released here (before acquiring Level 3)

    // Step 2: acquire Level 3 lock on the cloned handle
    let graph = inner_arc.read().await;   // Level 3 acquired
    // ... use graph ...
    drop(graph);                           // Level 3 released
}

// INCORRECT — holding Level 2 while acquiring Level 3 (deadlock risk)
async fn incorrect_pattern(index_manager: &IndexManager, collection_id: &str) {
    let outer_map = index_manager.graphs.read().await;   // Level 2 held
    let graph_arc = outer_map.get(collection_id).unwrap();
    let graph = graph_arc.read().await;  // Level 3 acquired WHILE Level 2 held — DEADLOCK RISK
    // ...
}
```

**Why this matters**: If a write path acquires Level 2 write and then Level 3 write (in that order),
and a read path acquires Level 3 read (via a cached Arc) while waiting for Level 2 write to drain,
a deadlock cannot occur because Level 2 is always released before Level 3 is acquired.

---

## 6. Performance Targets

| Metric | Target | Achieved Via |
|--------|--------|-------------|
| Search latency P50 | < 200ms | Parallel 3-way search, embedding LRU cache, petgraph in-memory traversal |
| Search latency P95 | < 800ms | Tokio timeout, bounded semaphore (no pile-up), IVF-PQ ANN |
| Search latency P99 | < 1500ms | System-level timeout on slow LLM embed calls |
| Graph render — 5000 nodes | 60 FPS | Canvas rendering (not SVG), Web Worker for layout |
| Ingest throughput | ≤ 3s per 10 pages | Parallel chunk processing, batched LLM (100 chunks/call) |
| IVF-PQ index rebuild | Non-blocking | Shadow table protocol — searches run against live table |
| Concurrent searches | 100 simultaneous | `search_semaphore(100)`, MVCC LanceDB, lock-free readers |
| In-memory graph (100K nodes) | < 200MB RAM | petgraph HashMap adjacency (approx. 40 bytes/edge overhead) |
| Write throughput | > 1000 vectors/sec | RecordBatch batching (512 rows/batch), Arrow zero-copy |
| Tantivy BM25 search | < 30ms | On-disk inverted index, `IndexReader` is lock-free clone |

---

## 7. Atomic Index Swap Protocol (Detailed)

This is the mechanism that allows index compaction to happen without any search downtime.

```
Timeline:

t=0  Active table: "collection123_chunks" (IVF-PQ index, 50K vectors)
     → 12 searches in flight, each holds Arc clone of the live table
     → New ingest completes, pending_writes reaches 12,000

t=1  compare_exchange(Active → Compacting) succeeds
     tokio::spawn(rebuild_ivf_pq_index("collection123")) → background task

t=2  Background task: creates "collection123_chunks_building" shadow table
     Copies all 62K vectors (stream, no lock held)

t=3  Background task: builds new IVF-PQ index on shadow table
     Cost: ~30 seconds CPU time
     → During this entire period:
        - Live table is unchanged
        - All 12+ searches complete normally against live table
        - New searches continue acquiring live table Arc
        - New ingest writes accumulate (write_semaphore serializes them to live table)

t=33 Background task: shadow table IVF-PQ index build complete
     Background task: runs verification query against shadow table ✓

t=33 Atomic swap:
     tables.write().await           ← write lock on HashMap (acquired)
     tables.insert("collection123_chunks", Arc::new(shadow_table))  ← pointer swap
     tables write lock released     ← held ~50 microseconds

t=33+ε  Old table Arc: still alive (in-flight searches from t=3 hold clones)
         → Freed when last in-flight search task completes (ref count → 0)

t=34  All new searches get the new shadow table (better IVF-PQ index)
      pending_writes reset to 0
      state: Compacting → Active
```

```rust
async fn rebuild_ivf_pq_index(
    index_manager: Arc<IndexManager>,
    collection_id: Uuid,
) -> Result<()> {
    let live_key = format!("{}_chunks", collection_id);
    let shadow_key = format!("{}_chunks_building", collection_id);

    // 1. Create shadow table (no lock held)
    let shadow = index_manager.db.create_empty_table(&shadow_key, chunks_schema()).await?;

    // 2. Stream-copy data from live table to shadow (no app lock; LanceDB MVCC snapshot)
    {
        let live_table = {
            let tables = index_manager.tables.read().await;
            tables.get(&live_key).ok_or(IndexError::TableNotFound(live_key.clone()))?.clone()
        };
        stream_copy_table(&live_table, &shadow).await?;
    }

    // 3. Build IVF-PQ index (CPU-intensive, no app locks held)
    shadow.create_index(
        &["embedding"],
        lancedb::index::Index::IvfPq(IvfPqIndexConfig {
            num_partitions: 256,
            num_sub_vectors: 96,
            max_iterations: 50,
        }),
    ).await?;

    // 4. Verify index works
    shadow.vector_search(vec![0.0f32; 1536])?.limit(1).execute().await?;

    // 5. Atomic swap (write lock held < 1ms)
    {
        let mut tables = index_manager.tables.write().await;
        tables.insert(live_key, Arc::new(shadow));
    } // old Arc not freed yet — in-flight searches still hold clones

    // 6. Cleanup and state transition
    index_manager.db.drop_table(&shadow_key).await.ok();
    index_manager.pending_writes.store(0, Ordering::Release);
    index_manager.state.store(IndexState::Active as u8, Ordering::Release);

    // 7. Invalidate search caches (index changed)
    index_manager.query_embedding_cache.lock().await.clear();
    index_manager.graph_neighbor_cache.lock().await.clear();

    tracing::info!("IVF-PQ rebuild complete for collection {}", collection_id);
    Ok(())
}
```

---

## 8. Batch Write Optimization

Writing individual rows to LanceDB is expensive. All writes are accumulated in a buffer and
flushed as a single `RecordBatch`:

```rust
pub struct WriteBatch {
    pub chunks: Vec<ChunkRecord>,
    pub flush_trigger: FlushTrigger,
}

pub enum FlushTrigger {
    SizeThreshold,    // 512 rows
    TimeThreshold,    // 1 second
    ForcedFlush,      // explicit call
}

impl WriteBatch {
    pub async fn flush_to_lancedb(
        &self,
        table: &Table,
    ) -> Result<usize> {
        // Build Arrow RecordBatch — zero-copy from Vecs
        let ids = StringArray::from(self.chunks.iter().map(|c| c.id.to_string()).collect::<Vec<_>>());
        let texts = StringArray::from(self.chunks.iter().map(|c| c.text.as_str()).collect::<Vec<_>>());
        let embeddings = {
            let flat: Vec<f32> = self.chunks.iter().flat_map(|c| c.embedding.iter().copied()).collect();
            let values = Float32Array::from(flat);
            FixedSizeListArray::try_new(
                Arc::new(Field::new("item", DataType::Float32, true)),
                1536,
                Arc::new(values),
                None,
            )?
        };

        let batch = RecordBatch::try_new(
            Arc::new(chunks_schema()),
            vec![Arc::new(ids), Arc::new(texts), Arc::new(embeddings), /* ... */],
        )?;

        // Single batched write — dramatically lower per-row overhead
        table.add(Box::pin(futures::stream::once(async move { Ok(batch) }))).await?;
        Ok(self.chunks.len())
    }
}
```

**Impact**: A batch of 512 chunks writes ~3× faster than 512 individual row inserts because:
1. Arrow RecordBatch is columnar — each column is a contiguous memory buffer
2. LanceDB writes entire columns in one pass
3. IPC and lock overhead amortized over batch

---

## 9. LRU Cache Design

### Query Embedding Cache

```
Key:   SHA-256(query_text) as hex string
Value: CachedEmbedding { embedding: Vec<f32>[1536], cached_at: Instant }
Capacity: 1,000 entries  →  ~6 MB (1536 × 4 bytes × 1000)
TTL:   5 minutes
```

Cache eviction: LRU (least recently used) evicts on capacity overflow. TTL is checked on read.

**Expected hit rate**: In typical use, users re-run the same or similar queries frequently.
With 1000 entries and 5-minute TTL, expected hit rate > 60% for interactive search sessions.

### Graph Neighborhood Cache

```
Key:   GraphCacheKey { node_id: Uuid, depth: u32, edge_types_hash: u64, topics_hash: u64 }
Value: CachedSubGraph { subgraph: SubGraph, cached_at: Instant, graph_version: u64 }
Capacity: 500 entries
TTL:   2 minutes (shorter because graph changes more frequently than embeddings)
```

Cache validity check: both TTL AND graph version must match. If the graph version has incremented
since caching, the entry is treated as a miss regardless of TTL.

---

## 10. Search Timeout and Circuit Breaker

```rust
// 800ms P95 SLA with per-channel timeouts
pub async fn hybrid_search_with_timeout(
    &self,
    params: SearchParams,
) -> Result<Vec<SearchResult>> {
    let overall_timeout = Duration::from_millis(params.timeout_ms.unwrap_or(800));

    tokio::time::timeout(overall_timeout, async {
        let _permit = self.search_semaphore.acquire().await?;

        // Each channel has its own sub-timeout
        let vector_timeout = Duration::from_millis(600);
        let keyword_timeout = Duration::from_millis(200);
        let graph_timeout = Duration::from_millis(300);

        let (vector_res, keyword_res, graph_res) = tokio::join!(
            tokio::time::timeout(vector_timeout, self.vector_search_channel(&params)),
            tokio::time::timeout(keyword_timeout, self.keyword_search_channel(&params)),
            tokio::time::timeout(graph_timeout, self.graph_proximity_channel(&params)),
        );

        // Graceful degradation: partial results are better than no results
        let vector_results = vector_res.unwrap_or_else(|_| {
            tracing::warn!("Vector search timed out, returning empty");
            Ok(vec![])
        }).unwrap_or_default();

        let keyword_results = keyword_res.unwrap_or_else(|_| {
            tracing::warn!("Keyword search timed out, returning empty");
            Ok(vec![])
        }).unwrap_or_default();

        let graph_results = graph_res.unwrap_or_else(|_| {
            tracing::warn!("Graph search timed out, returning empty");
            Ok(vec![])
        }).unwrap_or_default();

        Ok(fuse_scores(vector_results, keyword_results, graph_results, ScoreWeights::default()))
    })
    .await
    .map_err(|_| SearchError::Timeout { timeout_ms: params.timeout_ms.unwrap_or(800) })?
}
```

---

## 11. Rayon CPU Parallelism

Rayon is used for CPU-bound work that does not need async I/O. It uses a separate thread pool
from Tokio, preventing CPU work from starving the I/O executor.

```rust
// Parallel ontology validation of a batch of extracted entities
use rayon::prelude::*;

pub fn validate_batch_sync(
    entities: &[ExtractedEntity],
    ontology: &Ontology,
    rules: &[Box<dyn ValidationRule>],
) -> (Vec<ExtractedEntity>, Vec<(ExtractedEntity, ValidationError)>) {
    entities
        .par_iter()
        .cloned()
        .partition_map(|entity| {
            let result = rules.iter().try_for_each(|rule| rule.validate_entity(&entity, ontology));
            match result {
                Ok(()) => rayon::iter::Either::Left(entity),
                Err(e) => rayon::iter::Either::Right((entity, e)),
            }
        })
}

// Parallel graph BFS hop expansion
pub fn parallel_hop(
    adjacency_out: &HashMap<Uuid, Vec<(Uuid, Uuid)>>,
    frontier: &[Uuid],
    min_weight: f32,
    edges: &HashMap<Uuid, GraphEdge>,
) -> Vec<Uuid> {
    frontier
        .par_iter()
        .filter_map(|node_id| adjacency_out.get(node_id))
        .flatten()
        .filter_map(|(edge_id, target_id)| {
            edges.get(edge_id)
                .filter(|e| e.weight >= min_weight)
                .map(|_| *target_id)
        })
        .collect::<std::collections::HashSet<_>>()  // deduplicate
        .into_iter()
        .collect()
}
```

---

## 12. Monitoring and Metrics

### Prometheus Metrics

```rust
// Expose at GET /metrics (Prometheus text format)

use metrics::{counter, gauge, histogram};

// Recorded per search request
pub fn record_search_metrics(latency_ms: f64, mode: &str, collection_id: &str) {
    histogram!("kg.search.latency_ms", latency_ms,
        "mode" => mode.to_string(),
        "collection_id" => collection_id.to_string()
    );
    counter!("kg.search.total", 1);
}

// Recorded in IndexManager background task (every 10 seconds)
pub fn record_index_metrics(index_manager: &IndexManager) {
    let state = index_manager.state.load(Ordering::Relaxed);
    let pending = index_manager.pending_writes.load(Ordering::Relaxed);
    let search_available = index_manager.search_semaphore.available_permits();
    let write_available = index_manager.write_semaphore.available_permits();

    gauge!("kg.index.state", state as f64);
    gauge!("kg.index.pending_writes", pending as f64);
    gauge!("kg.search.available_permits", search_available as f64);
    gauge!("kg.write.available_permits", write_available as f64);
}

// Cache metrics
pub fn record_cache_metrics(
    embed_cache_size: usize,
    graph_cache_size: usize,
    cache_hits: u64,
    cache_misses: u64,
) {
    gauge!("kg.cache.embedding.size", embed_cache_size as f64);
    gauge!("kg.cache.graph.size", graph_cache_size as f64);
    let hit_ratio = cache_hits as f64 / (cache_hits + cache_misses).max(1) as f64;
    gauge!("kg.cache.hit_ratio", hit_ratio);
}
```

### OpenTelemetry Tracing

```rust
use tracing::{instrument, info_span};
use opentelemetry::trace::TraceContextExt;

#[instrument(skip(self, params), fields(collection_id = %params.collection_id))]
pub async fn hybrid_search(&self, params: SearchParams) -> Result<Vec<SearchResult>> {
    let span = info_span!("hybrid_search",
        query_len = params.query.len(),
        mode = %params.mode,
    );
    let _enter = span.enter();

    // ... search logic ...

    tracing::info!(
        latency_ms = start.elapsed().as_millis(),
        result_count = results.len(),
        "Search completed"
    );
    Ok(results)
}
```

---

## 13. Memory Management

### Arc Reference Counting

The primary memory management strategy relies on `Arc` reference counting:

- `Arc<Table>` (LanceDB): dropped when the last in-flight search releases its clone.
  During index swap, the old table stays alive until all searches complete.
- `Arc<RwLock<KnowledgeGraph>>`: graph stays in memory as long as the collection is loaded.
  Collections are loaded lazily on first access and evicted after configurable idle time.

### Memory Pressure Handling

```rust
const MAX_LOADED_COLLECTIONS: usize = 10;  // configurable

pub async fn evict_lru_collection(&self) -> Option<Uuid> {
    // If more than MAX_LOADED_COLLECTIONS are loaded, evict the least recently accessed
    let mut graphs = self.graphs.write().await;
    if graphs.len() <= MAX_LOADED_COLLECTIONS { return None; }

    // Find collection with no active searches (semaphore at max permits)
    // and oldest last_access timestamp
    let evict_id = self.find_idle_collection().await?;
    graphs.remove(&evict_id.to_string());

    let mut tables = self.tables.write().await;
    tables.retain(|k, _| !k.starts_with(&evict_id.to_string()));

    tracing::info!("Evicted collection {} from memory", evict_id);
    Some(evict_id)
}
```

### Estimated Memory Footprint

| Component | Estimation | Basis |
|-----------|-----------|-------|
| petgraph per collection (100K nodes, 500K edges) | ~180 MB | ~40B/node + ~200B/edge + adjacency |
| LanceDB Table handle | ~1 MB | Arrow buffer metadata only; data is mmap'd |
| Tantivy IndexReader | ~50 MB | RAM cache for frequently accessed blocks |
| Embedding LRU cache | ~6 MB | 1000 × 1536 × 4 bytes |
| Graph neighbor LRU | ~100 MB | 500 × avg_subgraph_nodes × 400 bytes |
| **Total per collection** | **~340 MB** | |
| **Total (10 collections)** | **~3.4 GB** | Plus OS/stack overhead |
