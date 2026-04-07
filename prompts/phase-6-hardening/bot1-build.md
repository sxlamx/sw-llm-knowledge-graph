# Bot 1 — Build: Phase 6 — Production Hardening

## Your Role

You are implementing production hardening for `sw-llm-knowledge-graph`. This phase covers the
full concurrency model with atomic index swap, LRU caching, rate limiting, Prometheus metrics,
WAL checkpointing, and Criterion benchmarks.

---

## Project Context

- **Goal**: 100 concurrent searches without deadlock; atomic IVF-PQ index rebuild while searches run
- **Performance targets**: P50 < 200ms, P95 < 800ms, 100 concurrent searches, >1000 vectors/sec writes
- **Rate limiting**: 60 req/min per user, 200 req/min per IP

**Read these specs before writing any code:**
- `specifications/05-index-manager.md` — CRITICAL: full concurrency model, shadow swap, WAL
- `specifications/11-concurrency-performance.md` — CRITICAL: lock ordering, batch writes, LRU caches
- `specifications/10-auth-security.md` section 10 — rate limiting implementation

---

## LESSONS.md Rules (Non-Negotiable)

1. **Lock ordering Level 1→2→3→4**: Never hold Level 2 while acquiring Level 3. Never reverse.
2. **Shadow swap write lock <1ms**: The atomic pointer swap holds Level 2 write lock ONLY for
   `HashMap::insert` (~50 microseconds). NOT for building the shadow table.
3. **Old Arc freed by refcount**: Old table Arc is freed when all in-flight searches complete
   (Arc refcount drops to 0). Do not manually drop or force-free.
4. **WAL checkpoint on startup**: After successful recovery replay, truncate WAL file (checkpoint).

---

## Implementation Tasks

### 1. Full IndexManager concurrency model (`rust-core/src/index_manager.rs`)

```rust
pub struct IndexManager {
    pub state: AtomicU8,                                        // Level 1
    pub pending_writes: AtomicU64,                             // Level 1
    pub tables: Arc<RwLock<HashMap<String, Arc<dyn TableOps>>>>, // Level 2 (outer, brief)
    pub graph: Arc<RwLock<KnowledgeGraph>>,                    // Level 3
    pub tantivy_writer: Arc<Mutex<IndexWriter>>,               // Level 4
    pub search_semaphore: Arc<Semaphore>,                      // 100 permits
    pub write_semaphore: Arc<Semaphore>,                       // 1 permit
    pub embedding_cache: Arc<Mutex<TimedLruCache<String, Vec<f32>>>>,     // Level 4
    pub neighborhood_cache: Arc<Mutex<TimedLruCache<Uuid, (Vec<Uuid>, u64)>>>, // Level 4
}
```

**Search path** (must NOT hold Level 2 during actual search):
```rust
// CORRECT: clone Arc while holding Level 2, then release before search
let table = {
    let tables = self.tables.read().await;  // Level 2 acquired
    tables.get(key).cloned()               // clone Arc
    // Level 2 released here (tables dropped)
}?;
table.vector_search(embedding, limit).await  // search with no locks held
```

**Write path** (Level 2 write lock for pointer swap only, after LanceDB write completes):
```rust
// 1. Acquire write_semaphore (serializes batch writes)
let _write_permit = self.write_semaphore.acquire().await?;
// 2. Write to LanceDB (no locks held)
new_table.add_batch(records).await?;
// 3. Acquire Level 2 write lock ONLY for pointer swap
{
    let mut tables = self.tables.write().await;  // Level 2 write acquired
    tables.insert(key, Arc::new(new_table));     // ~50μs
    // Level 2 released here
}
// 4. Old Arc freed when refcount drops to 0 (no manual action needed)
```

### 2. Atomic shadow table swap (`rebuild_ivf_pq_index`)

```rust
pub async fn rebuild_ivf_pq_index(&self, collection_id: Uuid) -> Result<()> {
    // State: ACTIVE → COMPACTING (compare_exchange)
    self.state.compare_exchange(
        IndexState::Active as u8,
        IndexState::Compacting as u8,
        Ordering::AcqRel,
        Ordering::Relaxed,
    )?;

    // 1. Build shadow table in background (no locks held — searches continue on live table)
    let shadow_table = self.build_shadow_table(collection_id).await?;

    // 2. Atomic pointer swap (hold Level 2 write lock for microseconds only)
    {
        let mut tables = self.tables.write().await;
        tables.insert(shadow_key, Arc::new(shadow_table));
    }
    // Old Arc freed when in-flight searches complete

    // 3. State: COMPACTING → ACTIVE
    self.state.store(IndexState::Active as u8, Ordering::Release);
    Ok(())
}
```

### 3. 800ms search timeout

```rust
pub async fn search_with_timeout(&self, query: SearchQuery) -> Result<Vec<SearchResult>> {
    tokio::time::timeout(
        Duration::from_millis(800),
        self.hybrid_search(query),
    ).await
    .unwrap_or_else(|_| Ok(vec![]))  // timeout → return empty results
}
```

### 4. LRU caches with TTL

**Embedding cache** (`TimedLruCache<String, Vec<f32>>`):
- 1000 entries max
- 5-minute TTL (evict entries older than 5 minutes on access)
- Key: first 100 chars of query text

**Neighborhood cache** (`TimedLruCache<Uuid, (Vec<Uuid>, u64)>`):
- 500 entries max
- 2-minute TTL
- Value: `(neighbor_ids, graph_version)` — invalidate if `graph_version != kg.version`

Implement `TimedLruCache<K, V>` as a wrapper around `lru::LruCache` with entry timestamps.

### 5. Batch RecordBatch writes

Group chunk inserts into Arrow RecordBatch of up to 512 rows OR 1-second timeout:

```rust
pub struct BatchWriter {
    pending: Vec<ChunkRecord>,
    last_flush: Instant,
    batch_size: usize,   // 512
    max_age: Duration,   // 1 second
}

impl BatchWriter {
    pub async fn push_and_maybe_flush(&mut self, record: ChunkRecord) -> Result<()> {
        self.pending.push(record);
        if self.pending.len() >= self.batch_size || self.last_flush.elapsed() > self.max_age {
            self.flush().await?;
        }
        Ok(())
    }
}
```

### 6. Graph pruning background task (hourly)

```rust
pub async fn prune_graph(&self, collection_id: Uuid) -> Result<PruneReport> {
    // 1. Remove edges with weight < 0.1 (configurable min_weight)
    // 2. Enforce max_degree: keep only top-K weight edges per node (default K=50)
    // 3. Tombstone orphan nodes (no edges after pruning)
    // 4. Write changes to LanceDB
    // 5. Update in-memory petgraph (brief write lock)
}
```

Schedule via Python asyncio task (not Rust Tokio spawn — same pattern as Tantivy flusher):
```python
async def graph_pruning_loop():
    while True:
        await asyncio.sleep(3600)  # hourly
        await loop.run_in_executor(None, index_manager.prune_graph, collection_id)
```

### 7. Per-user rate limiting (`python-api/app/auth/middleware.py`)

Sliding window rate limiter (already in spec, verify production-ready):
```python
class InMemoryRateLimiter:
    def __init__(self, per_user_limit=60, window_seconds=60): ...
    async def check(self, user_id: str) -> bool: ...  # True = allowed
```

For multi-replica deployment: swap for Redis-backed rate limiter using `redis-py` async:
```python
async def check_redis(self, user_id: str) -> bool:
    key = f"rate:{user_id}"
    # INCR + EXPIRE pattern with sliding window
```

### 8. Prometheus metrics (`python-api/app/core/metrics.py`)

```python
from prometheus_client import Counter, Gauge, Histogram

kg_searches_total = Counter('kg_searches_total', 'Total search requests', ['collection_id', 'mode'])
kg_concurrent_searches = Gauge('kg_concurrent_searches', 'Current concurrent searches')
kg_index_state = Gauge('kg_index_state', 'IndexManager state (0=uninit, 1=building, 2=active, 3=compacting)')
kg_pending_writes = Gauge('kg_pending_writes', 'Pending write count')
kg_search_latency = Histogram('kg_search_latency_seconds', 'Search latency', buckets=[0.05, 0.1, 0.2, 0.5, 0.8, 1.0])
```

`GET /metrics` endpoint returns Prometheus text format (no auth required).

### 9. WAL checkpoint on startup

```rust
// In IndexManager startup after replaying WAL:
pub async fn startup_recovery(&self) -> Result<()> {
    let entries = WalRecovery::read_entries(&self.wal_path)?;
    for entry in entries {
        self.replay_wal_entry(entry).await?;
    }
    // Checkpoint: truncate WAL after successful recovery
    WalWriter::checkpoint(&self.wal_path)?;
    Ok(())
}
```

### 10. Criterion benchmarks (`rust-core/benches/`)

```rust
// search_bench.rs
fn bench_100_concurrent_searches(c: &mut Criterion) {
    c.bench_function("100_concurrent_vector_search", |b| b.iter(|| {
        let handles: Vec<_> = (0..100).map(|_| {
            tokio::spawn(engine.vector_search(emb.clone(), col_id, 20))
        }).collect();
        runtime.block_on(futures::future::join_all(handles))
    }));
}

// index_bench.rs
fn bench_insert_throughput(c: &mut Criterion) {
    // Target: > 1000 vectors/sec
    c.bench_function("insert_1000_vectors", |b| b.iter(|| {
        runtime.block_on(engine.insert_chunks_batch(batch_1000.clone()))
    }));
}
```

---

## Acceptance Criteria

1. 100 simulated concurrent searches complete without deadlock or Tokio panic
2. `rebuild_ivf_pq_index` completes while searches run concurrently (no 5xx errors)
3. Rate limiter returns 429 after 60 requests in 60 seconds for the same user
4. `GET /metrics` returns valid Prometheus format including `kg_concurrent_searches`
5. WAL replay on restart correctly restores graph nodes/edges
6. LRU embedding cache hit rate > 60% for repeated queries in benchmark
7. Criterion benchmark: 100-concurrent-search completes; P95 < 800ms
8. `IndexManager` state machine transitions atomic (no race condition in stress test)
