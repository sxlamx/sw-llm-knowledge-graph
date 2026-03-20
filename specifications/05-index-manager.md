# 05 — Index Manager

## 1. Overview

The `IndexManager` is the central concurrency-control component of the Rust core engine. It owns
all storage handles and enforces safe concurrent access via a carefully designed locking hierarchy.
It is the single source of truth for the state of the LanceDB vector index, the in-memory
petgraph knowledge graph, and the Tantivy BM25 index.

The design priorities are:
1. **Multiple concurrent reads** — searches must never block each other
2. **Non-blocking index updates** — writes use background tasks and atomic swaps
3. **Strict lock ordering** — prevents deadlocks by always acquiring locks in the same order
4. **Recovery safety** — WAL ensures in-memory graph can be rebuilt after crash

---

## 2. Index State Machine

The index transitions through well-defined states. Transitions are atomic via `AtomicU8`.

```
                  ┌─────────────┐
                  │UNINITIALIZED│
                  └──────┬──────┘
                         │ startup / load from LanceDB
                         ▼
                  ┌─────────────┐
               ┌─►│  BUILDING   │
               │  └──────┬──────┘
               │         │ load complete
               │         ▼
  new data ◄───┤   ┌──────────┐  ◄──────────┐
  triggers     │   │  ACTIVE  │             │
  rebuild      │   └──────┬───┘             │
               │          │ compact trigger  │
               │          ▼                 │
               │   ┌────────────┐           │ compact success
               │   │ COMPACTING │───────────┘
               │   └──────┬─────┘
               │          │ compact failed
               │          ▼
               │   ┌──────────────┐
               └───│   DEGRADED   │  (searches continue on stale index)
                   └──────────────┘
                         │ manual recovery trigger
                         └───────────────────► BUILDING
```

```rust
// rust-core/src/index_manager.rs

#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IndexState {
    Uninitialized = 0,
    Building      = 1,
    Active        = 2,
    Compacting    = 3,
    Degraded      = 4,
}

impl IndexState {
    pub fn from_u8(v: u8) -> Self {
        match v {
            0 => Self::Uninitialized,
            1 => Self::Building,
            2 => Self::Active,
            3 => Self::Compacting,
            4 => Self::Degraded,
            _ => Self::Degraded,
        }
    }
}
```

---

## 3. IndexManager Struct

```rust
// rust-core/src/index_manager.rs

use std::collections::HashMap;
use std::sync::atomic::{AtomicU8, AtomicU64, Ordering};
use std::sync::Arc;
use tokio::sync::{RwLock, Semaphore, Mutex};
use lancedb::{Database, Table};
use lru::LruCache;
use uuid::Uuid;

pub struct IndexManager {
    /// LanceDB connection — the Database type is internally Arc'd and Send+Sync.
    /// Multiple Table handles can be held concurrently.
    pub db: Arc<Database>,

    /// Per-collection table handles, keyed by "{collection_id}_{table_name}".
    /// RwLock protects the HashMap itself (insert/remove), not the Table contents.
    /// LanceDB Tables are MVCC-safe for concurrent reads.
    pub tables: Arc<RwLock<HashMap<String, Arc<Table>>>>,

    /// In-memory knowledge graph per collection.
    /// Key: collection_id as string.
    pub graphs: Arc<RwLock<HashMap<String, Arc<RwLock<KnowledgeGraph>>>>>,

    /// Tantivy BM25 index per collection.
    /// Key: collection_id as string.
    pub tantivy_indexes: Arc<RwLock<HashMap<String, Arc<TantivyHandle>>>>,

    /// Current index lifecycle state.
    /// AtomicU8 allows lock-free reads of state.
    pub state: Arc<AtomicU8>,

    /// Count of vectors written since last IVF-PQ rebuild.
    /// Used to trigger background compaction.
    pub pending_writes: Arc<AtomicU64>,

    /// Exclusive write semaphore (permits = 1).
    /// Ensures only one LanceDB batch write is in flight at a time.
    pub write_semaphore: Arc<Semaphore>,

    /// Bounded search semaphore (permits = 100).
    /// Limits concurrent search operations to prevent resource exhaustion.
    pub search_semaphore: Arc<Semaphore>,

    /// LRU cache for query embeddings.
    /// Key: SHA-256 hex of query string.
    /// Value: Vec<f32> (1536-dim embedding).
    /// TTL enforced via timestamp stored alongside value.
    pub query_embedding_cache: Arc<Mutex<LruCache<String, CachedEmbedding>>>,

    /// LRU cache for graph neighborhood queries.
    /// Key: (node_id, depth, topics_hash).
    /// Value: SubGraph.
    pub graph_neighbor_cache: Arc<Mutex<LruCache<GraphCacheKey, CachedSubGraph>>>,

    /// WAL (Write-Ahead Log) writer for graph mutations.
    pub wal: Arc<Mutex<WalWriter>>,

    /// Ontology manager per collection.
    pub ontology_managers: Arc<RwLock<HashMap<String, OntologyManager>>>,

    /// Entity resolver (shared, stateless).
    pub entity_resolver: Arc<EntityResolver>,
}

pub struct TantivyHandle {
    /// Exclusive writer — Tantivy requires only one active writer.
    pub writer: Arc<Mutex<tantivy::IndexWriter>>,
    /// Clone-able reader — no lock required.
    pub reader: tantivy::IndexReader,
    /// Schema reference for query building.
    pub schema: tantivy::schema::Schema,
}

#[derive(Debug, Clone, Hash, PartialEq, Eq)]
pub struct GraphCacheKey {
    pub node_id: Uuid,
    pub depth: u32,
    pub edge_types_hash: u64,
    pub topics_hash: u64,
}

pub struct CachedEmbedding {
    pub embedding: Vec<f32>,
    pub cached_at: std::time::Instant,
}

pub struct CachedSubGraph {
    pub subgraph: SubGraph,
    pub cached_at: std::time::Instant,
    pub graph_version: u64,
}
```

---

## 4. Locking Strategy

### 4.1 Search Path (Read Operations)

```
HTTP Search Request
        │
        ▼
acquire search_semaphore permit (max 100 concurrent)
        │
        ▼
[All three run concurrently via tokio::join!]
        │
        ├─► 1. LanceDB vector search
        │       └─► tables.read() → clone Arc<Table> → release read lock immediately
        │           └─► table.search(embedding).execute().await
        │               (LanceDB MVCC: zero additional locking needed)
        │
        ├─► 2. Tantivy BM25 search
        │       └─► tantivy_indexes.read() → clone Arc<TantivyHandle> → release
        │           └─► handle.reader.searcher() → execute (no lock, reader is Clone)
        │
        └─► 3. Graph BFS traversal
                └─► graphs.read() → clone Arc<RwLock<KnowledgeGraph>> → release outer lock
                    └─► graph_rw.read() → traverse adjacency_out (concurrent reads OK)
                        └─► release graph read lock
        │
        ▼
merge results, release search_semaphore permit
```

Key insight: the outer `RwLock<HashMap>` is held only long enough to clone the inner `Arc<Table>`
or `Arc<RwLock<KnowledgeGraph>>`. The actual search operation happens without holding any HashMap
lock, enabling true read concurrency.

```rust
pub async fn search_vector(
    &self,
    collection_id: &Uuid,
    embedding: Vec<f32>,
    limit: usize,
    topics_filter: Option<Vec<String>>,
) -> Result<Vec<LanceSearchResult>> {
    // 1. Acquire search permit (non-blocking if under limit)
    let _permit = self.search_semaphore.acquire().await?;

    // 2. Get table handle — hold read lock only briefly
    let table_key = format!("{}_chunks", collection_id);
    let table = {
        let tables = self.tables.read().await;  // acquire read lock
        tables.get(&table_key)
            .ok_or(IndexError::TableNotFound(table_key.clone()))?
            .clone()                            // clone Arc<Table>
    };                                          // read lock released here

    // 3. Execute vector search — no lock held
    let mut query = table
        .vector_search(embedding)?
        .limit(limit)
        .distance_type(lancedb::DistanceType::Cosine);

    if let Some(topics) = topics_filter {
        let filter_expr = format!(
            "array_has_any(topics, ARRAY[{}])",
            topics.iter().map(|t| format!("'{}'", t)).collect::<Vec<_>>().join(", ")
        );
        query = query.filter(filter_expr);
    }

    let results = query.execute().await?
        .collect::<Vec<_>>()
        .await?;

    Ok(parse_lance_results(results))
}
```

### 4.2 Write Path (Index Update)

```
Write Operation (e.g., insert 512 new chunks after ingestion)
        │
        ▼
acquire write_semaphore (permits = 1 → serializes all writes)
        │
        ▼
1. Build Arrow RecordBatch (zero-copy, on Rayon thread pool)
        │
        ▼
2. LanceDB batch insert:
   tables.read() → clone Arc<Table> → release read lock
   table.add(record_batch_stream).await  (MVCC: new data is not visible until committed)
        │
        ▼
3. Update scalar indexes on LanceDB table (if needed)
        │
        ▼
4. Check if IVF-PQ rebuild needed (pending_writes threshold)
   If yes → spawn background compaction task (does NOT block write path)
        │
        ▼
5. Acquire write lock on KnowledgeGraph (BRIEF — just pointer updates)
   graph_rw.write() → insert_nodes_batch + insert_edges_batch
   Release write lock                  (version counter auto-incremented)
        │
        ▼
6. Append to WAL (write-ahead log)
        │
        ▼
7. Update Tantivy index:
   tantivy_handles.read() → clone Arc<TantivyHandle> → release read lock
   handle.writer.lock() → writer.add_document() × N → writer.commit()
   Release Mutex
        │
        ▼
release write_semaphore
        │
        ▼
invalidate LRU caches (query_embedding_cache and graph_neighbor_cache)
```

```rust
pub async fn batch_insert_chunks(
    &self,
    collection_id: &Uuid,
    chunks: Vec<ChunkRecord>,
) -> Result<()> {
    // Serialize all writes
    let _write_permit = self.write_semaphore.acquire().await?;

    // Build Arrow RecordBatch on Rayon (CPU-bound, avoids blocking Tokio)
    let record_batch = tokio::task::spawn_blocking(move || {
        build_chunks_record_batch(chunks)
    }).await??;

    // Insert into LanceDB (MVCC — does not block searches)
    let table_key = format!("{}_chunks", collection_id);
    let table = {
        let tables = self.tables.read().await;
        tables.get(&table_key).ok_or(IndexError::TableNotFound(table_key))?.clone()
    };
    let new_vector_count = record_batch.num_rows();
    table.add(record_batch_to_stream(record_batch)).await?;

    // Update pending write counter and maybe trigger background rebuild
    let prev = self.pending_writes.fetch_add(new_vector_count as u64, Ordering::AcqRel);
    if prev + new_vector_count as u64 > INDEX_REBUILD_THRESHOLD {
        self.maybe_trigger_compaction(collection_id).await;
    }

    // Append to WAL before updating in-memory graph (crash safety)
    {
        let mut wal = self.wal.lock().await;
        wal.append_batch_insert(collection_id, new_vector_count)?;
    }

    // Invalidate caches — new data might affect search results
    {
        let mut cache = self.query_embedding_cache.lock().await;
        cache.clear();
    }
    {
        let mut cache = self.graph_neighbor_cache.lock().await;
        cache.clear();
    }

    Ok(())
}
```

### 4.3 Graph Write Path

The graph write lock is held for the absolute minimum time — only the actual HashMap mutation,
not the LanceDB write that precedes it.

```rust
pub async fn upsert_graph_nodes(
    &self,
    collection_id: &Uuid,
    nodes: Vec<GraphNode>,
    edges: Vec<GraphEdge>,
) -> Result<()> {
    // 1. Write to LanceDB FIRST (persistent layer)
    //    (LanceDB write itself is under write_semaphore, see batch_insert above)
    self.upsert_nodes_to_lancedb(collection_id, &nodes).await?;
    self.upsert_edges_to_lancedb(collection_id, &edges).await?;

    // 2. Update in-memory graph (brief write lock)
    let graph_handle = {
        let graphs = self.graphs.read().await;   // read lock on outer HashMap
        graphs.get(&collection_id.to_string())
            .ok_or(IndexError::GraphNotFound)?
            .clone()                              // clone Arc<RwLock<KnowledgeGraph>>
    };                                            // outer read lock released

    {
        let mut graph = graph_handle.write().await;  // exclusive graph write lock
        graph.insert_nodes_batch(nodes);             // O(n) HashMap inserts
        graph.insert_edges_batch(edges);             // O(n) HashMap inserts
        // version counter incremented inside insert methods
    }                                                // graph write lock released

    Ok(())
}
```

---

## 5. Atomic Index Swap (Shadow Table Protocol)

When the IVF-PQ index needs to be rebuilt (e.g., after 10,000 new vectors), a shadow table is
built in the background without blocking any searches. Then the table reference is atomically
swapped.

```
Background Compaction Task (tokio::spawn)
        │
        ▼
1. Create shadow table: "{collection_id}_chunks_building" in LanceDB
        │
        ▼
2. Copy all data from live table to shadow table (streaming, MVCC snapshot)
        │
        ▼
3. Build IVF-PQ index on shadow table:
   table.create_index(&["embedding"], Index::IvfPq(ivf_pq_config)).await
   (This is CPU/GPU intensive — runs entirely without holding any app locks)
        │
        ▼
4. Verify index is queryable (run test query on shadow table)
        │
        ▼
5. Atomic swap:
   let mut tables = self.tables.write().await;   // acquire write lock on HashMap
   let old_table = tables.insert(table_key, Arc::new(shadow_table));  // atomic pointer swap
   drop(tables);                                  // release write lock immediately
        │
        ▼
6. Drop old table handle (Arc refcount → 0 once all in-flight searches release their clones)
        │
        ▼
7. Delete "{collection_id}_chunks_building" from LanceDB
        │
        ▼
8. Reset pending_writes counter to 0
   state.store(IndexState::Active as u8, Ordering::Release)
```

During step 3 (the expensive IVF-PQ build), all searches continue against the LIVE table via their
cloned `Arc<Table>`. The write lock in step 5 is held only for the duration of a HashMap insert
(microseconds), not for the entire index build.

```rust
async fn rebuild_ivf_pq_index(&self, collection_id: &Uuid) -> Result<()> {
    let shadow_key = format!("{}_chunks_building", collection_id);
    let live_key = format!("{}_chunks", collection_id);

    tracing::info!("Starting shadow index build for collection {}", collection_id);

    // Step 1-2: Create shadow table and copy data
    let shadow_table = self.db.create_empty_table(&shadow_key, chunks_schema()).await?;
    self.copy_table_to_shadow(&live_key, &shadow_key).await?;

    // Step 3: Build IVF-PQ index (expensive, no app locks held)
    let ivf_pq_config = IvfPqIndexConfig {
        num_partitions: 256,
        num_sub_vectors: 96,
        max_iterations: 50,
    };
    shadow_table
        .create_index(&["embedding"], lancedb::index::Index::IvfPq(ivf_pq_config))
        .await?;

    // Step 4: Verify
    let test_query = vec![0.0f32; 1536];
    shadow_table.vector_search(test_query)?.limit(1).execute().await?;

    // Step 5: Atomic swap (write lock held only briefly)
    {
        let mut tables = self.tables.write().await;
        tables.insert(live_key, Arc::new(shadow_table));
    }
    // Old table Arc dropped when all current search tasks finish (ref count → 0)

    // Step 6-8: Cleanup
    self.db.drop_table(&shadow_key).await.ok();
    self.pending_writes.store(0, Ordering::Release);
    self.state.store(IndexState::Active as u8, Ordering::Release);

    tracing::info!("Shadow index swap complete for collection {}", collection_id);
    Ok(())
}
```

---

## 6. Concurrent Search Design

### Search Semaphore

```rust
const MAX_CONCURRENT_SEARCHES: usize = 100;

// In IndexManager::new():
let search_semaphore = Arc::new(Semaphore::new(MAX_CONCURRENT_SEARCHES));
```

When the semaphore is at capacity (100 concurrent searches), new search requests block at
`semaphore.acquire()` until a slot is available. This provides natural back-pressure rather than
crashing under load.

### Per-Search Timeout

Each search has a configurable timeout (default 800ms P95 SLA):

```rust
pub async fn hybrid_search(&self, params: SearchParams) -> Result<Vec<SearchResult>> {
    let timeout = params.timeout_ms.unwrap_or(800);

    tokio::time::timeout(
        Duration::from_millis(timeout),
        self.hybrid_search_inner(params),
    )
    .await
    .map_err(|_| IndexError::SearchTimeout { timeout_ms: timeout })?
}
```

### Non-Blocking Search Task Dispatch

HTTP handler dispatches each search as a tokio task:

```rust
// python-api/app/routers/search.py (via PyO3 bridge)
async def search(request: SearchRequest, user: User = Depends(get_current_user)):
    # run_in_executor releases Python GIL during Rust search
    results = await asyncio.get_event_loop().run_in_executor(
        _executor,
        index_manager.hybrid_search,
        request.model_dump(),
    )
    return SearchResponse(results=results)
```

---

## 7. BM25 (Tantivy) Locking Design

Tantivy requires exclusive access to its `IndexWriter`. Reads via `IndexReader` are completely
lock-free (the reader is `Clone`).

```rust
pub struct TantivyHandle {
    /// Arc<Mutex<IndexWriter>> — only one writer active at a time.
    /// Commits are batched: every 500ms or 100 documents, whichever comes first.
    pub writer: Arc<Mutex<tantivy::IndexWriter>>,

    /// Clone-able reader. Multiple concurrent searches use separate Searcher instances.
    pub reader: tantivy::IndexReader,

    pub schema: tantivy::schema::Schema,
}

/// Background task: commit Tantivy writes in batches
pub async fn tantivy_batch_committer(handle: Arc<TantivyHandle>) {
    let mut interval = tokio::time::interval(Duration::from_millis(500));
    loop {
        interval.tick().await;
        let writer = handle.writer.lock().await;
        if writer.num_docs() > 0 {
            writer.commit().expect("Tantivy commit failed");
            drop(writer);
            handle.reader.reload().expect("Tantivy reader reload failed");
        }
    }
}

/// Search — no lock needed
pub fn tantivy_keyword_search(
    handle: &TantivyHandle,
    query_text: &str,
    limit: usize,
) -> Vec<TantivyResult> {
    let searcher = handle.reader.searcher();  // clone-able, no lock
    let query_parser = QueryParser::for_index(
        searcher.index(),
        vec![handle.schema.get_field("text").unwrap()],
    );
    let query = query_parser.parse_query(query_text).unwrap();
    let top_docs = searcher.search(&query, &TopDocs::with_limit(limit)).unwrap();
    top_docs.iter()
        .map(|(score, doc_addr)| {
            let doc = searcher.doc(*doc_addr).unwrap();
            TantivyResult { score: *score, doc }
        })
        .collect()
}
```

---

## 8. Graph Index Locking

```
┌────────────────────────────────────────────────────────────────────────┐
│              In-Memory Graph Locking (per collection)                  │
│                                                                        │
│   Arc<RwLock<KnowledgeGraph>>                                         │
│          │                                                             │
│          ├─► read() [many concurrent] ──► BFS traversal               │
│          │                               Dijkstra path finding        │
│          │                               Subgraph extraction          │
│          │                               Graph export                 │
│          │                                                             │
│          └─► write() [exclusive, brief] ──► insert_nodes_batch()      │
│                                             insert_edges_batch()      │
│                                             delete_node()             │
│                                             merge_nodes()             │
└────────────────────────────────────────────────────────────────────────┘
```

Graph version counter for cache invalidation:

```rust
// Version is AtomicU64 inside KnowledgeGraph
// Read operations check the cached version matches current version
pub fn is_cache_valid(cached: &CachedSubGraph, graph: &KnowledgeGraph) -> bool {
    let current_version = graph.version.load(Ordering::Acquire);
    cached.graph_version == current_version
        && cached.cached_at.elapsed() < Duration::from_secs(120)  // 2min TTL
}
```

---

## 9. Lock Ordering (Deadlock Prevention)

**CRITICAL**: All code must acquire locks in this strict order to prevent deadlocks.
Never acquire a lock at a lower level before a lock at a higher level.

```
Level 1 (AtomicU8, lock-free):
  IndexManager.state
  KnowledgeGraph.version

Level 2 (outer HashMaps, acquire briefly, release before inner):
  IndexManager.tables (RwLock<HashMap<String, Arc<Table>>>)
  IndexManager.graphs (RwLock<HashMap<String, Arc<RwLock<KnowledgeGraph>>>>)
  IndexManager.tantivy_indexes (RwLock<HashMap<String, Arc<TantivyHandle>>>)
  IndexManager.ontology_managers (RwLock<HashMap<...>>)

Level 3 (inner per-collection locks):
  KnowledgeGraph (RwLock) — per collection
  TantivyHandle.writer (Mutex<IndexWriter>) — per collection

Level 4 (utility locks, fine-grained):
  IndexManager.query_embedding_cache (Mutex<LruCache>)
  IndexManager.graph_neighbor_cache (Mutex<LruCache>)
  IndexManager.wal (Mutex<WalWriter>)
```

**Rules:**
1. Never hold a Level 2 lock when trying to acquire a Level 3 lock in a different collection.
2. Always clone the `Arc<...>` from a Level 2 map, release the Level 2 lock, THEN operate on
   the cloned handle.
3. Never hold a Level 3 lock and then attempt to acquire a Level 2 lock.
4. Level 4 locks are leaf locks — never acquire another lock while holding a Level 4 lock.

**Correct pattern:**

```rust
// CORRECT: Clone inner Arc, release outer lock, then operate
let graph_arc = {
    let outer = self.graphs.read().await;   // Level 2 read lock
    outer.get(key).unwrap().clone()         // clone Arc
};                                          // Level 2 lock released here
let graph = graph_arc.read().await;         // Level 3 read lock

// INCORRECT: Never do this
let outer = self.graphs.read().await;       // Level 2 lock held
let graph = outer.get(key).unwrap().read().await;  // Level 3 lock while Level 2 held
// If another thread is in write path (acquiring Level 2 write, then Level 3 write)
// this can deadlock.
```

---

## 10. Failure Recovery (WAL)

### Write-Ahead Log

The WAL is an append-only log file that records every graph mutation before it is applied to the
in-memory petgraph. On startup, the WAL is replayed to recover any mutations that completed on
disk (LanceDB) but were not yet reflected in memory.

```rust
pub struct WalEntry {
    pub sequence: u64,
    pub timestamp: u64,  // Unix microseconds
    pub collection_id: Uuid,
    pub operation: WalOperation,
}

pub enum WalOperation {
    InsertNodes { node_ids: Vec<Uuid> },
    InsertEdges { edge_ids: Vec<Uuid> },
    DeleteNode { node_id: Uuid },
    DeleteEdge { edge_id: Uuid },
    MergeNodes { canonical_id: Uuid, merged_id: Uuid },
}

pub struct WalWriter {
    file: std::io::BufWriter<std::fs::File>,
    sequence: u64,
}

impl WalWriter {
    pub fn append(&mut self, collection_id: Uuid, op: WalOperation) -> std::io::Result<()> {
        self.sequence += 1;
        let entry = WalEntry {
            sequence: self.sequence,
            timestamp: current_unix_micros(),
            collection_id,
            operation: op,
        };
        // Write as newline-delimited JSON for simplicity and debuggability
        let json = serde_json::to_string(&entry)?;
        writeln!(self.file, "{}", json)?;
        self.file.flush()?;  // ensure durability before returning
        Ok(())
    }
}
```

### Startup Recovery Protocol

```rust
pub async fn startup_recovery(
    &self,
    wal_path: &Path,
    collection_ids: &[Uuid],
) -> Result<()> {
    tracing::info!("Starting WAL recovery from {}", wal_path.display());

    // 1. For each collection, load all nodes and edges from LanceDB into petgraph
    for &collection_id in collection_ids {
        let nodes = self.load_all_nodes_from_lancedb(&collection_id).await?;
        let edges = self.load_all_edges_from_lancedb(&collection_id).await?;

        let graph = KnowledgeGraph::new(collection_id);
        let graph_arc = Arc::new(RwLock::new(graph));
        {
            let mut g = graph_arc.write().await;
            g.insert_nodes_batch(nodes);
            g.insert_edges_batch(edges);
        }

        let mut graphs = self.graphs.write().await;
        graphs.insert(collection_id.to_string(), graph_arc);
    }

    // 2. Replay WAL for any operations that may have been in-flight
    //    (in practice, LanceDB is MVCC and durable; WAL handles in-memory petgraph only)
    let wal_entries = replay_wal(wal_path)?;
    tracing::info!("WAL replay: {} entries", wal_entries.len());

    // 3. Truncate WAL (checkpoint)
    truncate_wal(wal_path)?;

    // 4. Transition to ACTIVE state
    self.state.store(IndexState::Active as u8, Ordering::Release);
    tracing::info!("IndexManager ready (recovered {} collections)", collection_ids.len());

    Ok(())
}
```

---

## 11. Monitoring and Observability

```rust
// Metrics exposed via /metrics (Prometheus format)

/// Number of currently active search operations
pub static CONCURRENT_SEARCHES: AtomicU64 = AtomicU64::new(0);

/// Total search requests processed
pub static TOTAL_SEARCHES: AtomicU64 = AtomicU64::new(0);

/// Index state as a gauge
pub fn record_metrics(index_manager: &IndexManager) {
    let state = IndexState::from_u8(index_manager.state.load(Ordering::Relaxed));
    let pending = index_manager.pending_writes.load(Ordering::Relaxed);

    metrics::gauge!("kg.index.state", state as u8 as f64);
    metrics::gauge!("kg.index.pending_writes", pending as f64);
    metrics::gauge!("kg.search.available_permits",
        index_manager.search_semaphore.available_permits() as f64);
}
```
