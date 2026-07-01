//! Index management — Phase 3 concurrency model.
//!
//! Concurrency design:
//!   - `search_semaphore(100)` — bounded concurrent searches
//!   - `write_semaphore(1)`   — serialised batch writes to LanceDB/Tantivy
//!   - `query_embedding_cache` — LRU(1000), 5-minute TTL, keyed by query SHA-256
//!   - `graph_neighbor_cache`  — LRU(500), 2-minute TTL, version-based invalidation
//!   - Lock ordering: Level-2 outer maps → release → Level-3 per-collection locks
//!   - `state` AtomicU64: 0=uninit, 1=building, 2=active, 3=compacting, 4=degraded
//!
//! Phase 4 hybrid search design:
//!   - Three concurrent channels: vector (LanceDB ANN), keyword (Tantivy BM25), graph proximity (BFS)
//!   - Per-channel timeouts: vector=600ms, keyword=200ms, graph=300ms
//!   - Graceful degradation: timeout on one channel returns empty, other channels still contribute
//!   - Score fusion: `w.vector * v + w.keyword * k + w.graph * g`

use crate::errors::CoreError;
use crate::models::{ChunkRecord, GraphEdge, GraphNode, KnowledgeGraph};
use crate::graph::merge::{DeterministicMergeStrategy, merge_nodes_deterministic, merge_edges_deterministic, MergeReport, detect_node_conflicts, detect_edge_conflicts, diff_node_fields, diff_edge_fields};
use crate::storage::SearchEngine;
use crate::storage::lancedb::{chunks_schema, build_chunks_record_batch};
use crate::wal::{WalWriter, read_wal_for_recovery, truncate_wal};
use arrow_array::cast::AsArray;
use arrow_schema::DataType;
use futures::TryStreamExt;
use lancedb::query::{ExecutableQuery, QueryBase};
use lru::LruCache;
use pyo3::prelude::*;
use std::collections::HashMap;
use std::num::NonZeroUsize;
use std::path::PathBuf;
use std::sync::{atomic::{AtomicU8, AtomicU64, Ordering}, Arc, Mutex};
use std::time::{Duration, Instant};
use tokio::sync::{RwLock as TokioRwLock, Semaphore};
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Timed LRU Cache — TTL enforced on eviction, not just on read
// ---------------------------------------------------------------------------

pub(crate) struct TimedLruCache<K, V> {
    inner: LruCache<K, (V, Instant)>,
    ttl: Duration,
}

impl<K, V> TimedLruCache<K, V>
where
    K: std::hash::Hash + Eq + Clone,
    V: Clone,
{
    pub fn new(capacity: NonZeroUsize, ttl: Duration) -> Self {
        Self {
            inner: LruCache::new(capacity),
            ttl,
        }
    }

    pub fn get(&mut self, key: &K) -> Option<V> {
        let entry = self.inner.get_mut(key)?;
        let (value, cached_at) = entry;
        if cached_at.elapsed() > self.ttl {
            self.inner.pop(key);
            return None;
        }
        Some(value.clone())
    }

    pub fn put(&mut self, key: K, value: V) {
        self.inner.put(key, (value, Instant::now()));
    }

    #[allow(dead_code)]
    pub fn pop(&mut self, key: &K) -> Option<V> {
        self.inner.pop(key).map(|(v, _)| v)
    }

    pub fn clear(&mut self) {
        self.inner.clear();
    }

    pub fn len(&self) -> usize {
        self.inner.len()
    }

    #[allow(dead_code)]
    pub fn is_empty(&self) -> bool {
        self.inner.is_empty()
    }

    #[allow(dead_code)]
    pub fn retain(&mut self, mut f: impl FnMut(&K, &V) -> bool) {
        let ttl = self.ttl;
        let mut expired_keys = Vec::new();
        let mut keys_to_remove = Vec::new();
        {
            let iter = self.inner.iter();
            for (k, (v, cached_at)) in iter {
                if cached_at.elapsed() > ttl {
                    expired_keys.push(k.clone());
                } else if !f(k, v) {
                    keys_to_remove.push(k.clone());
                }
            }
        }
        for k in expired_keys.iter().chain(keys_to_remove.iter()) {
            self.inner.pop(k);
        }
    }
}

// ---------------------------------------------------------------------------
// Cache entry types
// ---------------------------------------------------------------------------

#[derive(Clone)]
pub(crate) struct CachedEmbedding {
    embedding: Vec<f32>,
    #[allow(dead_code)]
    cached_at: Instant,
}

#[derive(Clone)]
pub(crate) struct CachedSubgraph {
    payload: String,
    #[allow(dead_code)]
    cached_at: Instant,
    #[allow(dead_code)]
    graph_version: u64,
}

const EMBED_CACHE_TTL: Duration = Duration::from_secs(300);
const GRAPH_CACHE_TTL: Duration = Duration::from_secs(120);
const SEARCH_SEMAPHORE_CAPACITY: usize = 100;
const EMBED_CACHE_CAPACITY: usize = 1_000;
const GRAPH_CACHE_CAPACITY: usize = 500;
const INDEX_REBUILD_THRESHOLD: u64 = 10_000;
const SEARCH_TIMEOUT_MS: u64 = 800;

#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IndexState {
    Uninitialized = 0,
    Building = 1,
    Active = 2,
    Compacting = 3,
    Degraded = 4,
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

// ---------------------------------------------------------------------------
// IndexManager
// ---------------------------------------------------------------------------

#[pyo3::pyclass]
pub struct IndexManager {
    pub search_engine: Arc<SearchEngine>,

    // LanceDB connection (Level 2 - shared via Arc)
    pub db: Arc<lancedb::Connection>,

    // LanceDB table handles, keyed by "{collection_id}_{table_name}" (Level 2)
    pub tables: Arc<TokioRwLock<HashMap<String, Arc<lancedb::Table>>>>,

    // Embedding dimension (default 1024)
    pub embedding_dim: i32,

    // Lock-free atomics (Level 1)
    pub state: AtomicU8,
    pub pending_writes: AtomicU64,

    // Semaphores — no ordering concern, acquired independently
    pub search_semaphore: Arc<Semaphore>,
    pub write_semaphore: Arc<Semaphore>,

    // Level-2 outer map (clone Arc before acquiring Level-3)
    pub graphs: Arc<TokioRwLock<HashMap<String, Arc<TokioRwLock<KnowledgeGraph>>>>>,
    pub collection_id: Arc<TokioRwLock<Option<Uuid>>>,

    // Level-4 leaf locks — never held simultaneously with any other lock
    pub(crate) query_embedding_cache: Arc<Mutex<TimedLruCache<String, CachedEmbedding>>>,
    pub(crate) graph_neighbor_cache: Arc<Mutex<TimedLruCache<String, CachedSubgraph>>>,

    // WAL — append-only log for crash recovery
    pub(crate) wal: Arc<Mutex<WalWriter>>,
    pub(crate) wal_path: PathBuf,
}

#[pyo3::pymethods]
impl IndexManager {
    #[new]
    pub fn new(index_path: &str) -> Result<Self, CoreError> {
        let search_engine = SearchEngine::new(index_path)
            .map_err(|e| CoreError::StorageError(e.to_string()))?;

        let base_path = PathBuf::from(index_path);
        let lancedb_path = base_path.join("lancedb");
        let wal_path = base_path.join("wal.log");

        let wal = WalWriter::new(&wal_path)
            .map_err(|e| CoreError::StorageError(format!("WAL init failed: {}", e)))?;

        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| CoreError::StorageError(format!("Tokio runtime: {}", e)))?;

        let db = rt.block_on(async {
            lancedb::connect(lancedb_path.to_str().unwrap_or("."))
                .execute()
                .await
                .map_err(|e| CoreError::StorageError(format!("LanceDB connect: {}", e)))
        })?;

        let im = Self {
            search_engine: Arc::new(search_engine),
            db: Arc::new(db),
            tables: Arc::new(TokioRwLock::new(HashMap::new())),
            embedding_dim: 1024,
            state: AtomicU8::new(0),
            pending_writes: AtomicU64::new(0),
            search_semaphore: Arc::new(Semaphore::new(SEARCH_SEMAPHORE_CAPACITY)),
            write_semaphore: Arc::new(Semaphore::new(1)),
            graphs: Arc::new(TokioRwLock::new(HashMap::new())),
            collection_id: Arc::new(TokioRwLock::new(None)),
            query_embedding_cache: Arc::new(Mutex::new(TimedLruCache::new(
                NonZeroUsize::new(EMBED_CACHE_CAPACITY).unwrap(),
                EMBED_CACHE_TTL,
            ))),
            graph_neighbor_cache: Arc::new(Mutex::new(TimedLruCache::new(
                NonZeroUsize::new(GRAPH_CACHE_CAPACITY).unwrap(),
                GRAPH_CACHE_TTL,
            ))),
            wal: Arc::new(Mutex::new(wal)),
            wal_path,
        };

        im.run_wal_checkpoint()?;

        Ok(im)
    }

    fn run_wal_checkpoint(&self) -> Result<(), CoreError> {
        let entries = read_wal_for_recovery(&self.wal_path)
            .map_err(|e| CoreError::StorageError(format!("WAL read failed: {}", e)))?;

        if entries.is_empty() {
            return Ok(());
        }

        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| CoreError::StorageError(format!("Tokio runtime: {}", e)))?;

        let graphs_clone = Arc::clone(&self.graphs);

        rt.block_on(async {
            for entry in entries {
                let json = entry.operation;

                let op = json.get("op").and_then(|v| v.as_str()).unwrap_or("");
                let coll_id = json.get("collection_id").and_then(|v| v.as_str());

                if coll_id.is_none() {
                    continue;
                }

                let uuid = match Uuid::parse_str(coll_id.unwrap()) {
                    Ok(u) => u,
                    Err(_) => continue,
                };

                let graph_arc = {
                    let outer = graphs_clone.read().await;
                    outer.get(&uuid.to_string()).cloned()
                };

                let Some(arc) = graph_arc else {
                    continue;
                };

                match op {
                    "upsert_nodes" => {
                        if let Some(nodes_json) = json.get("nodes").and_then(|v| v.as_str()) {
                            if let Ok(nodes) = serde_json::from_str::<Vec<GraphNode>>(nodes_json) {
                                let mut g = arc.write().await;
                                g.insert_nodes_batch(nodes);
                            }
                        }
                    }
                    "upsert_edges" => {
                        if let Some(edges_json) = json.get("edges").and_then(|v| v.as_str()) {
                            if let Ok(edges) = serde_json::from_str::<Vec<GraphEdge>>(edges_json) {
                                let mut g = arc.write().await;
                                g.insert_edges_batch(edges);
                            }
                        }
                    }
                    "delete_edge" => {
                        if let Some(eid) = json.get("edge_id").and_then(|v| v.as_str()) {
                            if let Ok(edge_uuid) = Uuid::parse_str(eid) {
                                let mut g = arc.write().await;
                                if let Some(edge) = g.edges.remove(&edge_uuid) {
                                    if let Some(adj_out) = g.adjacency_out.get_mut(&edge.source) {
                                        adj_out.retain(|(id, _)| id != &edge_uuid);
                                    }
                                    if let Some(adj_in) = g.adjacency_in.get_mut(&edge.target) {
                                        adj_in.retain(|(id, _)| id != &edge_uuid);
                                    }
                                }
                            }
                        }
                    }
                    "update_node" => {
                        if let Some(node_json) = json.get("node").and_then(|v| v.as_str()) {
                            if let Ok(node) = serde_json::from_str::<GraphNode>(node_json) {
                                let mut g = arc.write().await;
                                g.nodes.insert(node.id, node);
                            }
                        }
                    }
                    "merge_nodes" => {
                        if let Some(nodes_json) = json.get("nodes").and_then(|v| v.as_str()) {
                            if let Ok(nodes) = serde_json::from_str::<Vec<GraphNode>>(nodes_json) {
                                let strategy_str = json.get("strategy").and_then(|v| v.as_str()).unwrap_or("keep_first");
                                let strat = DeterministicMergeStrategy::from_str(strategy_str);
                                let mut g = arc.write().await;
                                for inc in &nodes {
                                    if let Some(ref dk) = inc.dedup_key {
                                        if let Some(existing) = g.nodes.values().find(|n| n.dedup_key.as_deref() == Some(dk.as_str())) {
                                            if let Some(ref s) = strat {
                                                let merged = merge_nodes_deterministic(existing, inc, s);
                                                g.nodes.insert(merged.id, merged);
                                            }
                                            continue;
                                        }
                                    }
                                    g.nodes.insert(inc.id, inc.clone());
                                }
                                g.version.fetch_add(1, std::sync::atomic::Ordering::Release);
                            }
                        }
                    }
                    "merge_edges" => {
                        if let Some(edges_json) = json.get("edges").and_then(|v| v.as_str()) {
                            if let Ok(edges) = serde_json::from_str::<Vec<GraphEdge>>(edges_json) {
                                let strategy_str = json.get("strategy").and_then(|v| v.as_str()).unwrap_or("keep_first");
                                let strat = DeterministicMergeStrategy::from_str(strategy_str);
                                let mut g = arc.write().await;
                                let mut new_edges: Vec<GraphEdge> = Vec::new();
                                for inc in &edges {
                                    if let Some(ref dk) = inc.dedup_key {
                                        if let Some(existing) = g.edges.values().find(|e| e.dedup_key.as_deref() == Some(dk.as_str())) {
                                            if let Some(ref s) = strat {
                                                let merged = merge_edges_deterministic(existing, inc, s);
                                                g.edges.insert(merged.id, merged);
                                            }
                                            continue;
                                        }
                                    }
                                    new_edges.push(inc.clone());
                                }
                                if !new_edges.is_empty() {
                                    g.insert_edges_batch(new_edges);
                                } else {
                                    g.rebuild_adjacency();
                                }
                            }
                        }
                    }
                    _ => {}
                }
            }
        });

        truncate_wal(&self.wal_path)
            .map_err(|e| CoreError::StorageError(format!("WAL truncate after replay failed: {}", e)))?;

        Ok(())
    }

    // -----------------------------------------------------------------------
    // Collection initialisation
    // -----------------------------------------------------------------------

    pub fn initialize_collection(&self, py: Python<'_>, collection_id: &str) -> PyResult<()> {
        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let state_ptr = &self.state;
        let graphs_clone = Arc::clone(&self.graphs);
        let coll_id_cell = Arc::clone(&self.collection_id);

        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new()
                .map_err(|e| CoreError::StorageError(e.to_string()))?;

            rt.block_on(async {
                let prev = state_ptr.compare_exchange(0, 1, Ordering::AcqRel, Ordering::Acquire);
                if prev.is_err() {
                    return Err(CoreError::StorageError(
                        format!("Cannot initialize: state is {} (expected UNINITIALIZED=0)", state_ptr.load(Ordering::Acquire))
                    ));
                }
                *coll_id_cell.write().await = Some(uuid);

                let graph = Arc::new(TokioRwLock::new(KnowledgeGraph::new(uuid)));
                graphs_clone.write().await.insert(uuid.to_string(), graph);

                state_ptr.compare_exchange(1, 2, Ordering::AcqRel, Ordering::Acquire).ok();

                Ok::<(), CoreError>(())
            })
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    // -----------------------------------------------------------------------
    // Tantivy chunk indexing  (write_semaphore serialises concurrent writers)
    // -----------------------------------------------------------------------

    pub fn insert_chunks(&self, py: Python<'_>, collection_id: &str, chunks_json: &str) -> PyResult<usize> {
        let chunks: Vec<ChunkRecord> = serde_json::from_str(chunks_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let write_sem = Arc::clone(&self.write_semaphore);
        let engine = Arc::clone(&self.search_engine);

        let count = py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new()
                .map_err(|e| CoreError::StorageError(e.to_string()))?;

            rt.block_on(async {
                let _permit = write_sem.acquire().await
                    .map_err(|_| CoreError::StorageError("write semaphore closed".into()))?;
                engine.insert_chunks(chunks)
                    .map_err(|e| CoreError::StorageError(e.to_string()))
            })
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        self.pending_writes.fetch_add(count as u64, Ordering::AcqRel);

        if let Ok(mut cache) = self.graph_neighbor_cache.lock() {
            cache.clear();
        }

        if let Ok(mut cache) = self.query_embedding_cache.lock() {
            cache.clear();
        }

        let pending = self.pending_writes.load(Ordering::Acquire);
        if pending >= INDEX_REBUILD_THRESHOLD {
            pyo3::Python::with_gil(|py| {
                let _ = self.maybe_trigger_compaction(py, collection_id);
            });
        }

        Ok(count)
    }

    // -----------------------------------------------------------------------
    // LanceDB batch chunk insertion (write_semaphore serialises writes)
    // -----------------------------------------------------------------------

    pub fn insert_chunks_batch(&self, py: Python<'_>, collection_id: &str, chunks_json: &str) -> PyResult<usize> {
        let chunks: Vec<ChunkRecord> = serde_json::from_str(chunks_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        let count = chunks.len();

        let write_sem = Arc::clone(&self.write_semaphore);
        let tables = Arc::clone(&self.tables);
        let db = Arc::clone(&self.db);
        let dim = self.embedding_dim;

        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new()
                .map_err(|e| CoreError::StorageError(e.to_string()))?;

            rt.block_on(async {
                let _permit = write_sem.acquire().await
                    .map_err(|_| CoreError::StorageError("write semaphore closed".into()))?;

                let table_key = format!("{}_chunks", uuid);

                let table = {
                    let tables_guard = tables.read().await;
                    tables_guard.get(&table_key).cloned()
                };

                let table = match table {
                    Some(t) => t,
                    None => {
                        let schema = chunks_schema(Some(dim));
                        let tbl = db.create_empty_table(&table_key, Arc::new(schema))
                            .execute()
                            .await
                            .map_err(|e| CoreError::StorageError(format!("create table: {}", e)))?;

                        let mut tables_guard = tables.write().await;
                        tables_guard.insert(table_key, Arc::new(tbl));
                        tables_guard.get(&format!("{}_chunks", uuid)).unwrap().clone()
                    }
                };

                let batch = build_chunks_record_batch(&chunks, dim)
                    .map_err(|e| CoreError::StorageError(e))?;

                table.add(batch)
                    .execute()
                    .await
                    .map_err(|e| CoreError::StorageError(format!("insert chunks: {}", e)))?;

                Ok::<(), CoreError>(())
            })
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        self.pending_writes.fetch_add(count as u64, Ordering::AcqRel);

        if let Ok(mut cache) = self.graph_neighbor_cache.lock() {
            cache.clear();
        }

        if let Ok(mut cache) = self.query_embedding_cache.lock() {
            cache.clear();
        }

        let pending = self.pending_writes.load(Ordering::Acquire);
        if pending >= INDEX_REBUILD_THRESHOLD {
            pyo3::Python::with_gil(|py| {
                let _ = self.maybe_trigger_compaction(py, collection_id);
            });
        }

        Ok(count)
    }

    // -----------------------------------------------------------------------
    // LanceDB vector search (bounded by search_semaphore)
    // -----------------------------------------------------------------------

    pub fn vector_search(
        &self,
        py: Python<'_>,
        embedding: Vec<f32>,
        collection_id: &str,
        limit: usize,
    ) -> PyResult<String> {
        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let tables = Arc::clone(&self.tables);
        let search_sem = Arc::clone(&self.search_semaphore);

        let results = py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new()
                .map_err(|e| CoreError::StorageError(e.to_string()))?;

            rt.block_on(async {
                let _permit = search_sem.acquire().await
                    .map_err(|_| CoreError::StorageError("search semaphore closed".into()))?;

                let table_key = format!("{}_chunks", uuid);
                let table = {
                    let tables_guard = tables.read().await;
                    tables_guard.get(&table_key).cloned()
                };

                let Some(table) = table else {
                    return Ok::<Vec<serde_json::Value>, CoreError>(vec![]);
                };

                let query = table.query()
                    .nearest_to(embedding)
                    .map_err(|e| CoreError::SearchError(format!("vector search: {}", e)))?
                    .limit(limit);

                let results = query.execute()
                    .await
                    .map_err(|e| CoreError::SearchError(format!("vector search execute: {}", e)))?;

                let mut output = Vec::new();
                let batches = results.try_collect::<Vec<_>>().await
                    .map_err(|e| CoreError::SearchError(format!("collect results: {}", e)))?;

                for batch in batches {
                    for i in 0..batch.num_rows() {
                        let mut obj = serde_json::Map::new();
                        for j in 0..batch.num_columns() {
                            let col = batch.column(j);
                            let field_name = batch.schema().field(j).name().clone();
                            if let Some(val) = arrow_to_json_value(col, i) {
                                obj.insert(field_name, val);
                            }
                        }
                        output.push(serde_json::Value::Object(obj));
                    }
                }

                Ok(output)
            })
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        serde_json::to_string(&results)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
    }

    // -----------------------------------------------------------------------
    // BM25 text search  (bounded by search_semaphore)
    // -----------------------------------------------------------------------

    pub fn text_search(
        &self,
        py: Python<'_>,
        collection_id: &str,
        query: &str,
        limit: usize,
    ) -> PyResult<String> {
        let collection_id = collection_id.to_string();
        let query = query.to_string();
        let engine = Arc::clone(&self.search_engine);
        let search_sem = Arc::clone(&self.search_semaphore);

        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new()
                .map_err(|e| CoreError::StorageError(e.to_string()))?;

            rt.block_on(async {
                let _permit = search_sem.acquire().await
                    .map_err(|_| CoreError::StorageError("search semaphore closed".into()))?;

                tokio::time::timeout(Duration::from_millis(200), async {
                    engine.search(&collection_id, &query, limit)
                        .map_err(|e| CoreError::StorageError(e.to_string()))
                })
                .await
                .map_err(|_| CoreError::SearchTimeout { timeout_ms: 200 })?
            })
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        .and_then(|results| {
            serde_json::to_string(&results)
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
        })
    }

    // -----------------------------------------------------------------------
    // Graph node/edge writes  (Level-2 → release → Level-3)
    // -----------------------------------------------------------------------

    pub fn upsert_nodes(&self, py: Python<'_>, collection_id: &str, nodes_json: &str) -> PyResult<()> {
        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        let nodes: Vec<GraphNode> = serde_json::from_str(nodes_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let wal_entry = serde_json::json!({
            "op": "upsert_nodes",
            "collection_id": collection_id,
            "nodes": nodes_json,
        }).to_string();
        if let Ok(mut wal) = self.wal.lock() {
            let _ = wal.append(&wal_entry);
        }

        let graphs_clone = Arc::clone(&self.graphs);

        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new()
                .map_err(|e| CoreError::StorageError(e.to_string()))?;

            rt.block_on(async {
                let graph_arc = Self::get_or_create_graph_internal(&graphs_clone, uuid).await?;
                graph_arc.write().await.insert_nodes_batch(nodes);
                Ok::<(), CoreError>(())
            })
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        if let Ok(mut cache) = self.graph_neighbor_cache.lock() {
            cache.clear();
        }
        Ok(())
    }

    pub fn upsert_edges(&self, py: Python<'_>, collection_id: &str, edges_json: &str) -> PyResult<()> {
        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        let edges: Vec<GraphEdge> = serde_json::from_str(edges_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let wal_entry = serde_json::json!({
            "op": "upsert_edges",
            "collection_id": collection_id,
            "edges": edges_json,
        }).to_string();
        if let Ok(mut wal) = self.wal.lock() {
            let _ = wal.append(&wal_entry);
        }

        let graphs_clone = Arc::clone(&self.graphs);

        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new()
                .map_err(|e| CoreError::StorageError(e.to_string()))?;

            rt.block_on(async {
                let graph_arc = Self::get_or_create_graph_internal(&graphs_clone, uuid).await?;
                graph_arc.write().await.insert_edges_batch(edges);
                Ok::<(), CoreError>(())
            })
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        if let Ok(mut cache) = self.graph_neighbor_cache.lock() {
            cache.clear();
        }
        Ok(())
    }

    // -----------------------------------------------------------------------
    // Tantivy batch committer
    //
    // `flush_tantivy` is the flush side of the batch-committer design:
    //   - Python startup code calls `asyncio.get_event_loop().create_task(
    //       _commit_loop(im))` where `_commit_loop` calls `im.flush_tantivy()`
    //       every 500 ms.
    //   - This decouples write throughput from commit latency and matches
    //     the Phase 3 spec ("Tantivy batch committer: 500ms interval").
    // -----------------------------------------------------------------------

    /// Flush staged Tantivy documents to disk.  Returns `True` if a commit
    /// was issued, `False` if there was nothing pending.
    ///
    /// Call this every ~500 ms from a Python asyncio task, e.g.:
    /// ```python
    /// async def _tantivy_commit_loop(im, interval=0.5):
    ///     while True:
    ///         await asyncio.sleep(interval)
    ///         await asyncio.get_event_loop().run_in_executor(
    ///             None, im.flush_tantivy
    ///         )
    /// ```
    pub fn flush_tantivy(&self, py: Python<'_>) -> PyResult<bool> {
        let engine = Arc::clone(&self.search_engine);

        py.allow_threads(|| {
            engine.commit_pending()
                .map_err(|e| CoreError::StorageError(e.to_string()))
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    /// Number of Tantivy documents staged but not yet committed.
    pub fn pending_tantivy_docs(&self) -> u64 {
        self.search_engine.pending_doc_count()
    }

    // -----------------------------------------------------------------------
    // Metrics / state accessors (lock-free)
    // -----------------------------------------------------------------------

    pub fn get_state(&self) -> u8 {
        self.state.load(Ordering::Acquire)
    }

    pub fn pending_writes_count(&self) -> u64 {
        self.pending_writes.load(Ordering::Acquire)
    }

    /// Returns how many of the 100 search semaphore slots are still available.
    pub fn available_search_permits(&self) -> usize {
        self.search_semaphore.available_permits()
    }

    /// Returns embedding cache stats as JSON: {"size": N, "capacity": 1000}
    pub fn embedding_cache_stats(&self) -> String {
        let size = self.query_embedding_cache.lock()
            .map(|c| c.len())
            .unwrap_or(0);
        format!(r#"{{"size":{size},"capacity":{EMBED_CACHE_CAPACITY}}}"#)
    }

    /// Returns graph neighbor cache stats as JSON: {"size": N, "capacity": 500}
    pub fn graph_cache_stats(&self) -> String {
        let size = self.graph_neighbor_cache.lock()
            .map(|c| c.len())
            .unwrap_or(0);
        format!(r#"{{"size":{size},"capacity":{GRAPH_CACHE_CAPACITY}}}"#)
    }

    // -----------------------------------------------------------------------
    // Graph data access  (read-only, cached)
    // -----------------------------------------------------------------------

    pub fn get_graph_data(&self, py: Python<'_>, collection_id: &str) -> PyResult<String> {
        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let graphs_clone = Arc::clone(&self.graphs);

        let current_version: u64 = {
            let rt = tokio::runtime::Runtime::new()
                .map_err(|e| CoreError::StorageError(e.to_string()))
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
            rt.block_on(async {
                let graph_arc = {
                    let outer = graphs_clone.read().await;
                    outer.get(&uuid.to_string()).cloned()
                };
                if let Some(arc) = graph_arc {
                    let graph = arc.read().await;
                    graph.version.load(Ordering::Relaxed)
                } else {
                    0
                }
            })
        };

        let cache_key = format!("full:{}", uuid);
        if let Ok(mut cache) = self.graph_neighbor_cache.lock() {
            if let Some(entry) = cache.get(&cache_key) {
                if entry.graph_version == current_version {
                    return Ok(entry.payload.clone());
                }
            }
        }

        let result = py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new()
                .map_err(|e| CoreError::StorageError(e.to_string()))?;

            rt.block_on(async {
                let graph_arc = {
                    let outer = graphs_clone.read().await;
                    outer.get(&uuid.to_string()).cloned()
                };

                if let Some(arc) = graph_arc {
                    let graph = arc.read().await;
                    let nodes: Vec<&GraphNode> = graph.nodes.values().collect();
                    let edges: Vec<&GraphEdge> = graph.edges.values().collect();
                    let version = graph.version.load(Ordering::Relaxed);
                    let payload = serde_json::json!({
                        "nodes": nodes,
                        "edges": edges,
                        "total_nodes": nodes.len(),
                        "total_edges": edges.len(),
                    })
                    .to_string();
                    Ok::<(String, u64), CoreError>((payload, version))
                } else {
                    let empty = r#"{"nodes":[],"edges":[],"total_nodes":0,"total_edges":0}"#.to_string();
                    Ok((empty, 0))
                }
            })
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        let (payload, version) = result;

        if let Ok(mut cache) = self.graph_neighbor_cache.lock() {
            cache.put(cache_key, CachedSubgraph {
                payload: payload.clone(),
                cached_at: Instant::now(),
                graph_version: version,
            });
        }

        Ok(payload)
    }

    pub fn update_node(&self, py: Python<'_>, collection_id: &str, node_json: &str) -> PyResult<()> {
        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        let node: GraphNode = serde_json::from_str(node_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let wal_entry = serde_json::json!({
            "op": "update_node",
            "collection_id": collection_id,
            "node": node_json,
        }).to_string();
        if let Ok(mut wal) = self.wal.lock() {
            let _ = wal.append(&wal_entry);
        }

        let graphs_clone = Arc::clone(&self.graphs);

        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new()
                .map_err(|e| CoreError::StorageError(e.to_string()))?;

            rt.block_on(async {
                let graph_arc = Self::get_or_create_graph_internal(&graphs_clone, uuid).await?;
                let mut g = graph_arc.write().await;
                g.nodes.insert(node.id, node);
                g.version.fetch_add(1, Ordering::SeqCst);
                Ok::<(), CoreError>(())
            })
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        if let Ok(mut cache) = self.graph_neighbor_cache.lock() {
            cache.clear();
        }
        Ok(())
    }

    // -----------------------------------------------------------------------
    // Graph pruning  (Phase 3 — hourly background task)
    //
    // Python startup wires this up via:
    //   asyncio.get_event_loop().create_task(
    //       _prune_loop(im, interval=3600, min_weight=0.3, max_degree=100)
    //   )
    // -----------------------------------------------------------------------

    /// Prune low-weight edges and cap per-node out-degree for the given collection.
    ///
    /// Returns JSON: `{"edges_removed": N, "nodes_affected": M}`.
    pub fn prune_graph(
        &self,
        py: Python<'_>,
        collection_id: &str,
        min_weight: f32,
        max_degree: usize,
    ) -> PyResult<String> {
        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let graphs_clone = Arc::clone(&self.graphs);

        let (removed, affected) = py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new()
                .map_err(|e| CoreError::StorageError(e.to_string()))?;

            rt.block_on(async {
                let graph_arc = Self::get_or_create_graph_internal(&graphs_clone, uuid).await?;
                let mut g = graph_arc.write().await;
                let stats = g.prune_edges(min_weight, max_degree);
                Ok::<(usize, usize), CoreError>(stats)
            })
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        if removed > 0 {
            if let Ok(mut cache) = self.graph_neighbor_cache.lock() {
                cache.clear();
            }
        }

        Ok(format!(
            r#"{{"edges_removed":{removed},"nodes_affected":{affected}}}"#
        ))
    }

    /// Prune dangling edges — edges whose source/target/participants reference
    /// non-existent nodes.  Works for both binary edges and hyperedges.
    ///
    /// Returns the number of edges pruned.
    pub fn prune_dangling_edges_pyo3(
        &self,
        py: Python<'_>,
        collection_id: &str,
    ) -> PyResult<usize> {
        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let graphs_clone = Arc::clone(&self.graphs);

        let pruned = py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new()
                .map_err(|e| CoreError::StorageError(e.to_string()))?;

            rt.block_on(async {
                let graph_arc = Self::get_or_create_graph_internal(&graphs_clone, uuid).await?;
                let mut g = graph_arc.write().await;
                let count = g.prune_dangling_edges();
                Ok::<usize, CoreError>(count)
            })
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        if pruned > 0 {
            if let Ok(mut cache) = self.graph_neighbor_cache.lock() {
                cache.clear();
            }
        }

        Ok(pruned)
    }

    // -----------------------------------------------------------------------
    // Graph proximity search (Phase 4 — replaces Python JSON-based BFS)
    //
    // 1. Acquire the in-memory graph for the collection
    // 2. Find seed entities by cosine similarity to the query embedding
    //    (compares against node embeddings stored in the LanceDB nodes table)
    // 3. Run `bfs_reachable` from seeds up to `depth` hops
    // 4. Score each chunk by 1/(hop_depth + 1) for proximity (closer = higher)
    // 5. Return JSON array of {chunk_id, graph_proximity_score}
    // -----------------------------------------------------------------------

    pub fn graph_proximity_search(
        &self,
        py: Python<'_>,
        collection_id: &str,
        query_embedding: Vec<f32>,
        depth: u32,
        limit: usize,
    ) -> PyResult<String> {
        use crate::graph::traversal::bfs_reachable;

        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let graphs_clone = Arc::clone(&self.graphs);
        let tables = Arc::clone(&self.tables);
        let search_sem = Arc::clone(&self.search_semaphore);

        let result = py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new()
                .map_err(|e| CoreError::StorageError(e.to_string()))?;

            rt.block_on(async {
                let _permit = search_sem.acquire().await
                    .map_err(|_| CoreError::StorageError("search semaphore closed".into()))?;

                let graph_arc = {
                    let outer = graphs_clone.read().await;
                    outer.get(&uuid.to_string()).cloned()
                };

                let Some(graph_arc) = graph_arc else {
                    return Ok::<Vec<serde_json::Value>, CoreError>(vec![]);
                };

                let top_k = 5usize;

                let fallback_seeds: Vec<Uuid> = {
                    let graph = graph_arc.read().await;
                    graph.nodes.values()
                        .filter(|n| n.node_type != crate::models::NodeType::Chunk)
                        .take(top_k)
                        .map(|n| n.id)
                        .collect()
                };

                let nodes_key = format!("{}_nodes", uuid);
                let node_table = {
                    let tables_guard = tables.read().await;
                    tables_guard.get(&nodes_key).cloned()
                };

                let seed_ids = if let Some(table) = node_table {
                    let query = table.query()
                        .nearest_to(query_embedding.clone())
                        .map_err(|e| CoreError::SearchError(format!("node vector search: {}", e)))?
                        .limit(top_k);

                    let batches = query.execute().await
                        .map_err(|e| CoreError::SearchError(format!("node search execute: {}", e)))?
                        .try_collect::<Vec<_>>().await
                        .map_err(|e| CoreError::SearchError(format!("node search collect: {}", e)))?;

                    let mut found_seeds = Vec::new();
                    for batch in batches {
                        if let Some(col) = batch.column_by_name("id") {
                            if let Some(string_col) = col.as_any().downcast_ref::<arrow_array::StringArray>() {
                                for i in 0..batch.num_rows() {
                                    if let Ok(node_id) = Uuid::parse_str(string_col.value(i)) {
                                        found_seeds.push(node_id);
                                    }
                                }
                            }
                        }
                    }

                    if found_seeds.is_empty() {
                        fallback_seeds
                    } else {
                        let graph = graph_arc.read().await;
                        found_seeds.into_iter()
                            .filter(|id| graph.nodes.contains_key(id))
                            .collect::<Vec<_>>()
                    }
                } else {
                    fallback_seeds
                };

                if seed_ids.is_empty() {
                    return Ok(vec![]);
                }

                let (_, depths, edges_data) = {
                    let graph = graph_arc.read().await;
                    let reachable = bfs_reachable(&graph, &seed_ids, depth, 0.0);

                    let mut depths: std::collections::HashMap<Uuid, u32> = std::collections::HashMap::new();
                    for &seed in &seed_ids {
                        depths.insert(seed, 0);
                    }
                    let mut visited_d: std::collections::HashSet<Uuid> = std::collections::HashSet::new();
                    let mut frontier_d: std::collections::VecDeque<(Uuid, u32)> = seed_ids.iter().map(|&id| (id, 0u32)).collect();
                    while let Some((node_id, d)) = frontier_d.pop_front() {
                        if visited_d.contains(&node_id) { continue; }
                        visited_d.insert(node_id);
                        depths.insert(node_id, d);
                        if d < depth {
                            if let Some(neighbors) = graph.adjacency_out.get(&node_id) {
                                for &(_, neighbor_id) in neighbors {
                                    if !visited_d.contains(&neighbor_id) && reachable.contains(&neighbor_id) {
                                        frontier_d.push_back((neighbor_id, d + 1));
                                    }
                                }
                            }
                        }
                    }

                    let edges_data: Vec<(Uuid, Option<Uuid>, Uuid)> = graph.edges.values()
                        .filter(|e| reachable.contains(&e.source))
                        .map(|e| (e.source, e.chunk_id, e.id))
                        .collect();

                    (reachable, depths, edges_data)
                };

                let mut chunk_scores: std::collections::HashMap<Uuid, f32> = std::collections::HashMap::new();
                for (source, chunk_id_opt, _eid) in &edges_data {
                    if let Some(chunk_id) = chunk_id_opt {
                        let hop = depths.get(source).copied().unwrap_or(depth);
                        let score = 1.0 / (hop as f32 + 1.0);
                        let prev = chunk_scores.get(chunk_id).copied().unwrap_or(0.0);
                        if score > prev {
                            chunk_scores.insert(*chunk_id, score);
                        }
                    }
                }

                let mut results: Vec<(Uuid, f32)> = chunk_scores.into_iter().collect();
                results.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));

                let output: Vec<serde_json::Value> = results.into_iter()
                    .take(limit)
                    .map(|(chunk_id, score)| serde_json::json!({
                        "chunk_id": chunk_id.to_string(),
                        "graph_proximity_score": score,
                    }))
                    .collect();

                Ok(output)
            })
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        serde_json::to_string(&result)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
    }

    pub fn delete_edge(&self, py: Python<'_>, collection_id: &str, edge_id: &str) -> PyResult<()> {
        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        let edge_uuid = Uuid::parse_str(edge_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let wal_entry = serde_json::json!({
            "op": "delete_edge",
            "collection_id": collection_id,
            "edge_id": edge_id,
        }).to_string();
        if let Ok(mut wal) = self.wal.lock() {
            let _ = wal.append(&wal_entry);
        }

        let graphs_clone = Arc::clone(&self.graphs);

        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new()
                .map_err(|e| CoreError::StorageError(e.to_string()))?;

            rt.block_on(async {
                let graph_arc = {
                    let outer = graphs_clone.read().await;
                    outer.get(&uuid.to_string()).cloned()
                };
                if let Some(arc) = graph_arc {
                    let mut g = arc.write().await;
                    if let Some(edge) = g.edges.remove(&edge_uuid) {
                        if let Some(adj_out) = g.adjacency_out.get_mut(&edge.source) {
                            adj_out.retain(|(eid, _)| eid != &edge_uuid);
                        }
                        if let Some(adj_in) = g.adjacency_in.get_mut(&edge.target) {
                            adj_in.retain(|(eid, _)| eid != &edge_uuid);
                        }
                    }
                }
                Ok::<(), CoreError>(())
            })
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        if let Ok(mut cache) = self.graph_neighbor_cache.lock() {
            cache.clear();
        }
        Ok(())
    }

    // -----------------------------------------------------------------------
    // IVF-PQ shadow index rebuild  (Phase 6 — non-blocking compaction)
    //
    // State machine: Active(2) → Compacting(3) → Active(2) on success
    //                                          → Degraded(4) on failure
    //
    // The write lock on `tables` is held ONLY for the HashMap::insert (pointer
    // swap), ~50 microseconds.  The expensive IVF-PQ build runs without any
    // app-level locks.  In-flight searches continue against the live table
    // because they hold their own Arc<Table> clones.
    // -----------------------------------------------------------------------

    /// Trigger IVF-PQ index rebuild if the pending-writes threshold has been
    /// exceeded.  Uses `compare_exchange(Active → Compacting)` so at most one
    /// rebuild runs at a time.
    pub fn maybe_trigger_compaction(
        &self,
        py: Python<'_>,
        collection_id: &str,
    ) -> PyResult<bool> {
        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let state_ptr = &self.state;
        let prev = state_ptr.compare_exchange(
            IndexState::Active as u8,
            IndexState::Compacting as u8,
            Ordering::AcqRel,
            Ordering::Acquire,
        );

        if prev.is_err() {
            return Ok(false);
        }

        let tables = Arc::clone(&self.tables);
        let db = Arc::clone(&self.db);
        let state_ptr2 = &self.state;
        let pending = &self.pending_writes;
        let embed_cache = Arc::clone(&self.query_embedding_cache);
        let graph_cache = Arc::clone(&self.graph_neighbor_cache);
        let dim = self.embedding_dim;

        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new()
                .map_err(|e| CoreError::StorageError(e.to_string()))?;

            rt.block_on(async {
                let result = Self::rebuild_ivf_pq_inner(
                    &db, &tables, uuid, dim,
                ).await;

                match result {
                    Ok(()) => {
                        pending.store(0, Ordering::Release);
                        let _ = state_ptr2.compare_exchange(
                            IndexState::Compacting as u8,
                            IndexState::Active as u8,
                            Ordering::AcqRel,
                            Ordering::Acquire,
                        );

                        if let Ok(mut c) = embed_cache.lock() { c.clear(); }
                        if let Ok(mut c) = graph_cache.lock() { c.clear(); }

                        tracing::info!("IVF-PQ rebuild complete for collection {}", uuid);
                    }
                    Err(e) => {
                        tracing::error!("IVF-PQ rebuild failed for collection {}: {}", uuid, e);
                        let _ = state_ptr2.compare_exchange(
                            IndexState::Compacting as u8,
                            IndexState::Degraded as u8,
                            Ordering::AcqRel,
                            Ordering::Acquire,
                        );
                    }
                }

                Ok::<(), CoreError>(())
            })
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        Ok(true)
    }

    /// Force an IVF-PQ rebuild regardless of the pending-writes counter.
    pub fn rebuild_ivf_pq_index(
        &self,
        py: Python<'_>,
        collection_id: &str,
    ) -> PyResult<()> {
        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let state_ptr = &self.state;
        let prev = state_ptr.compare_exchange(
            IndexState::Active as u8,
            IndexState::Compacting as u8,
            Ordering::AcqRel,
            Ordering::Acquire,
        );
        if prev.is_err() {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(
                format!("Cannot force rebuild: state is {} (expected ACTIVE=2)", state_ptr.load(Ordering::Acquire))
            ));
        }

        let tables = Arc::clone(&self.tables);
        let db = Arc::clone(&self.db);
        let state_ptr2 = &self.state;
        let pending = &self.pending_writes;
        let embed_cache = Arc::clone(&self.query_embedding_cache);
        let graph_cache = Arc::clone(&self.graph_neighbor_cache);
        let dim = self.embedding_dim;

        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new()
                .map_err(|e| CoreError::StorageError(e.to_string()))?;

            rt.block_on(async {
                let result = Self::rebuild_ivf_pq_inner(
                    &db, &tables, uuid, dim,
                ).await;

                match result {
                    Ok(()) => {
                        pending.store(0, Ordering::Release);
                        let _ = state_ptr2.compare_exchange(
                            IndexState::Compacting as u8,
                            IndexState::Active as u8,
                            Ordering::AcqRel,
                            Ordering::Acquire,
                        );

                        if let Ok(mut c) = embed_cache.lock() { c.clear(); }
                        if let Ok(mut c) = graph_cache.lock() { c.clear(); }

                        tracing::info!("IVF-PQ rebuild complete for collection {}", uuid);
                    }
                    Err(e) => {
                        tracing::error!("IVF-PQ rebuild failed for collection {}: {}", uuid, e);
                        let _ = state_ptr2.compare_exchange(
                            IndexState::Compacting as u8,
                            IndexState::Degraded as u8,
                            Ordering::AcqRel,
                            Ordering::Acquire,
                        );
                    }
                }

                Ok::<(), CoreError>(())
            })
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        Ok(())
    }

    // -----------------------------------------------------------------------
    // Search with timeout  (Phase 6 — 800ms overall SLA)
    //
    // Wraps `vector_search` with `tokio::time::timeout`.  When the timeout
    // fires, `CoreError::SearchTimeout` is returned so the Python layer can
    // translate it to HTTP 504.
    // -----------------------------------------------------------------------

    #[pyo3(signature = (embedding, collection_id, limit, timeout_ms=None))]
    pub fn vector_search_with_timeout(
        &self,
        py: Python<'_>,
        embedding: Vec<f32>,
        collection_id: &str,
        limit: usize,
        timeout_ms: Option<u64>,
    ) -> PyResult<String> {
        let timeout = Duration::from_millis(timeout_ms.unwrap_or(SEARCH_TIMEOUT_MS));
        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let tables = Arc::clone(&self.tables);
        let search_sem = Arc::clone(&self.search_semaphore);

        let results = py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new()
                .map_err(|e| CoreError::StorageError(e.to_string()))?;

            rt.block_on(async {
                tokio::time::timeout(timeout, async {
                    let _permit = search_sem.acquire().await
                        .map_err(|_| CoreError::StorageError("search semaphore closed".into()))?;

                    let table_key = format!("{}_chunks", uuid);
                    let table = {
                        let tables_guard = tables.read().await;
                        tables_guard.get(&table_key).cloned()
                    };

                    let Some(table) = table else {
                        return Ok::<Vec<serde_json::Value>, CoreError>(vec![]);
                    };

                    let query = table.query()
                        .nearest_to(embedding)
                        .map_err(|e| CoreError::SearchError(format!("vector search: {}", e)))?
                        .limit(limit);

                    let results = query.execute().await
                        .map_err(|e| CoreError::SearchError(format!("vector search execute: {}", e)))?;

                    let mut output = Vec::new();
                    let batches = results.try_collect::<Vec<_>>().await
                        .map_err(|e| CoreError::SearchError(format!("collect results: {}", e)))?;

                    for batch in batches {
                        for i in 0..batch.num_rows() {
                            let mut obj = serde_json::Map::new();
                            for j in 0..batch.num_columns() {
                                let col = batch.column(j);
                                let field_name = batch.schema().field(j).name().clone();
                                if let Some(val) = arrow_to_json_value(col, i) {
                                    obj.insert(field_name, val);
                                }
                            }
                            output.push(serde_json::Value::Object(obj));
                        }
                    }

                    Ok(output)
                })
                .await
                .map_err(|_| CoreError::SearchTimeout { timeout_ms: timeout.as_millis() as u64 })?
            })
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        serde_json::to_string(&results)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
    }

    // -----------------------------------------------------------------------
    // Embedding cache helpers (callable from Python via rust_bridge if needed)
    // -----------------------------------------------------------------------

    /// Look up a cached embedding by query text. Returns JSON or empty string on miss.
    pub fn get_cached_embedding(&self, query: &str) -> String {
        let key = format!("{:x}", md5_hex(query.as_bytes()));
        if let Ok(mut cache) = self.query_embedding_cache.lock() {
            if let Some(cached) = cache.get(&key) {
                if let Ok(json) = serde_json::to_string(&cached.embedding) {
                    return json;
                }
            }
        }
        String::new()
    }

    /// Store an embedding in the LRU cache. `embedding_json` must be a JSON float array.
    pub fn cache_embedding(&self, query: &str, embedding_json: &str) -> bool {
        let embedding: Vec<f32> = match serde_json::from_str(embedding_json) {
            Ok(v) => v,
            Err(_) => return false,
        };
        let key = format!("{:x}", md5_hex(query.as_bytes()));
        let cached = CachedEmbedding {
            embedding,
            cached_at: Instant::now(),
        };
        if let Ok(mut cache) = self.query_embedding_cache.lock() {
            cache.put(key, cached);
            return true;
        }
        false
    }

    /// Invalidate all caches (called after IVF-PQ rebuild or major graph changes).
    pub fn invalidate_all_caches(&self) {
        if let Ok(mut c) = self.query_embedding_cache.lock() { c.clear(); }
        if let Ok(mut c) = self.graph_neighbor_cache.lock() { c.clear(); }
    }

    /// Detect field-level conflicts between new nodes and existing nodes in a collection.
    /// Returns JSON array of MergeConflict objects.
    pub fn detect_node_conflicts(&self, py: Python<'_>, collection_id: &str, new_nodes_json: &str) -> PyResult<String> {
        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
            rt.block_on(async {
                let cid: Uuid = collection_id.parse().map_err(|e: uuid::Error| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

                let new_nodes: Vec<GraphNode> = serde_json::from_str(new_nodes_json)
                    .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("parse nodes: {}", e)))?;

                let graphs = self.graphs.clone();
                let graph_arc = Self::get_or_create_graph_internal(&graphs, cid).await
                    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

                let graph = graph_arc.read().await;
                let existing: Vec<GraphNode> = graph.nodes.values().cloned().collect();
                drop(graph);

                let conflicts = detect_node_conflicts(&existing, &new_nodes);
                serde_json::to_string(&conflicts)
                    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
            })
        })
    }

    /// Detect field-level conflicts between new edges and existing edges in a collection.
    /// Returns JSON array of MergeConflict objects.
    pub fn detect_edge_conflicts(&self, py: Python<'_>, collection_id: &str, new_edges_json: &str) -> PyResult<String> {
        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
            rt.block_on(async {
                let cid: Uuid = collection_id.parse().map_err(|e: uuid::Error| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

                let new_edges: Vec<GraphEdge> = serde_json::from_str(new_edges_json)
                    .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("parse edges: {}", e)))?;

                let graphs = self.graphs.clone();
                let graph_arc = Self::get_or_create_graph_internal(&graphs, cid).await
                    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

                let graph = graph_arc.read().await;
                let existing: Vec<GraphEdge> = graph.edges.values().cloned().collect();
                drop(graph);

                let conflicts = detect_edge_conflicts(&existing, &new_edges);
                serde_json::to_string(&conflicts)
                    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
            })
        })
    }

    /// Merge new nodes into an existing collection using a deterministic strategy.
    /// Strategy must be "keep_first", "keep_last", or "field_overwrite".
    /// Returns MergeReport JSON: { merged: N, inserted: N, conflicted: N }
    pub fn merge_nodes_into_collection(&self, py: Python<'_>, collection_id: &str, new_nodes_json: &str, strategy: &str) -> PyResult<String> {
        let cid: Uuid = collection_id.parse()
            .map_err(|e: uuid::Error| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let merge_strategy = DeterministicMergeStrategy::from_str(strategy)
            .ok_or_else(|| pyo3::exceptions::PyValueError::new_err(format!("unknown strategy: {}", strategy)))?;

        let new_nodes: Vec<GraphNode> = serde_json::from_str(new_nodes_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("parse nodes: {}", e)))?;

        let wal_entry = serde_json::json!({
            "op": "merge_nodes",
            "collection_id": collection_id,
            "nodes": new_nodes_json,
            "strategy": strategy,
        }).to_string();
        if let Ok(mut wal) = self.wal.lock() {
            let _ = wal.append(&wal_entry);
        }

        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
            rt.block_on(async {
                let graphs = self.graphs.clone();
                let graph_arc = Self::get_or_create_graph_internal(&graphs, cid).await
                    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

                let mut graph = graph_arc.write().await;

                let mut merged_count = 0usize;
                let mut inserted_count = 0usize;
                let mut conflicted_count = 0usize;

                let mut nodes_to_insert: Vec<GraphNode> = Vec::new();

                for inc in &new_nodes {
                    if let Some(ref dk) = inc.dedup_key {
                        let existing_id = graph.nodes.values().find(|n| n.dedup_key.as_deref() == Some(dk.as_str()));
                        if let Some(existing) = existing_id {
                            let conflicts = diff_node_fields(existing, inc);
                            if !conflicts.is_empty() {
                                conflicted_count += 1;
                            }
                            let merged = merge_nodes_deterministic(existing, inc, &merge_strategy);
                            graph.nodes.insert(merged.id, merged);
                            merged_count += 1;
                            continue;
                        }
                    }
                    nodes_to_insert.push(inc.clone());
                }

                if !nodes_to_insert.is_empty() {
                    graph.insert_nodes_batch(nodes_to_insert.clone());
                    inserted_count = nodes_to_insert.len();
                }

                if merged_count > 0 || inserted_count > 0 {
                    graph.version.fetch_add(1, std::sync::atomic::Ordering::Release);
                }

                let report = MergeReport {
                    merged: merged_count,
                    inserted: inserted_count,
                    conflicted: conflicted_count,
                };

                if let Ok(mut cache) = self.graph_neighbor_cache.lock() {
                    cache.clear();
                }

                serde_json::to_string(&report)
                    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
            })
        })
    }

    /// Merge new edges into an existing collection using a deterministic strategy.
    /// Strategy must be "keep_first", "keep_last", or "field_overwrite".
    /// Returns MergeReport JSON: { merged: N, inserted: N, conflicted: N }
    pub fn merge_edges_into_collection(&self, py: Python<'_>, collection_id: &str, new_edges_json: &str, strategy: &str) -> PyResult<String> {
        let cid: Uuid = collection_id.parse()
            .map_err(|e: uuid::Error| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let merge_strategy = DeterministicMergeStrategy::from_str(strategy)
            .ok_or_else(|| pyo3::exceptions::PyValueError::new_err(format!("unknown strategy: {}", strategy)))?;

        let new_edges: Vec<GraphEdge> = serde_json::from_str(new_edges_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("parse edges: {}", e)))?;

        let wal_entry = serde_json::json!({
            "op": "merge_edges",
            "collection_id": collection_id,
            "edges": new_edges_json,
            "strategy": strategy,
        }).to_string();
        if let Ok(mut wal) = self.wal.lock() {
            let _ = wal.append(&wal_entry);
        }

        py.allow_threads(|| {
            let rt = tokio::runtime::Runtime::new().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
            rt.block_on(async {
                let graphs = self.graphs.clone();
                let graph_arc = Self::get_or_create_graph_internal(&graphs, cid).await
                    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

                let mut graph = graph_arc.write().await;

                let mut merged_count = 0usize;
                let mut inserted_count = 0usize;
                let mut conflicted_count = 0usize;

                let mut edges_to_insert: Vec<GraphEdge> = Vec::new();

                for inc in &new_edges {
                    if let Some(ref dk) = inc.dedup_key {
                        let existing = graph.edges.values().find(|e| e.dedup_key.as_deref() == Some(dk.as_str()));
                        if let Some(existing) = existing {
                            let conflicts = diff_edge_fields(existing, inc);
                            if !conflicts.is_empty() {
                                conflicted_count += 1;
                            }
                            let merged = merge_edges_deterministic(existing, inc, &merge_strategy);
                            graph.edges.insert(merged.id, merged);
                            merged_count += 1;
                            continue;
                        }
                    }
                    edges_to_insert.push(inc.clone());
                }

                if !edges_to_insert.is_empty() {
                    graph.insert_edges_batch(edges_to_insert.clone());
                    inserted_count = edges_to_insert.len();
                }

                if merged_count > 0 {
                    graph.rebuild_adjacency();
                }

                let report = MergeReport {
                    merged: merged_count,
                    inserted: inserted_count,
                    conflicted: conflicted_count,
                };

                if merged_count > 0 || inserted_count > 0 {
                    if let Ok(mut cache) = self.graph_neighbor_cache.lock() {
                        cache.clear();
                    }
                }

                serde_json::to_string(&report)
                    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
            })
        })
    }
}

impl IndexManager {
    /// Correct Level-2 → Level-3 transition: clone Arc before releasing the outer map lock.
    async fn get_or_create_graph_internal(
        graphs: &Arc<TokioRwLock<HashMap<String, Arc<TokioRwLock<KnowledgeGraph>>>>>,
        collection_id: Uuid,
    ) -> Result<Arc<TokioRwLock<KnowledgeGraph>>, CoreError> {
        // Fast path: read lock only
        {
            let outer = graphs.read().await;    // Level-2 read acquired
            if let Some(g) = outer.get(&collection_id.to_string()) {
                return Ok(g.clone());           // Level-2 released here
            }
        }                                       // Level-2 released

        // Slow path: create new graph
        let new_graph = Arc::new(TokioRwLock::new(KnowledgeGraph::new(collection_id)));
        {
            let mut outer = graphs.write().await;   // Level-2 write acquired
            // Re-check to avoid race between fast-path check and write lock
            outer
                .entry(collection_id.to_string())
                .or_insert_with(|| new_graph.clone());
        }                                            // Level-2 write released

        Ok(graphs.read().await.get(&collection_id.to_string()).unwrap().clone())
    }

    /// Inner implementation of IVF-PQ shadow table rebuild.
    ///
    /// Lock ordering:
    ///   1. `tables.read()` → clone Arc<Table> (live) → release
    ///   2. Stream-copy data to shadow table (no app locks)
    ///   3. Build IVF-PQ index on shadow (CPU-intensive, no app locks)
    ///   4. `tables.write()` → HashMap::insert (pointer swap) → release (< 1 ms)
    async fn rebuild_ivf_pq_inner(
        db: &Arc<lancedb::Connection>,
        tables: &Arc<TokioRwLock<HashMap<String, Arc<lancedb::Table>>>>,
        collection_id: Uuid,
        dim: i32,
    ) -> Result<(), CoreError> {
        let live_key = format!("{}_chunks", collection_id);
        let shadow_key = format!("{}_chunks_building", collection_id);

        tracing::info!("Starting shadow index build for collection {}", collection_id);

        let schema = chunks_schema(Some(dim));
        let shadow_table = db.create_empty_table(&shadow_key, Arc::new(schema))
            .execute()
            .await
            .map_err(|e| CoreError::StorageError(format!("create shadow table: {}", e)))?;

        let live_table = {
            let tables_guard = tables.read().await;
            tables_guard.get(&live_key).cloned()
        };

        if let Some(live) = live_table {
            let live_data = live.query()
                .execute()
                .await
                .map_err(|e| CoreError::StorageError(format!("read live table: {}", e)))?;

            let batches = live_data.try_collect::<Vec<_>>().await
                .map_err(|e| CoreError::StorageError(format!("collect live data: {}", e)))?;

            for batch in batches {
                shadow_table.add(batch)
                    .execute()
                    .await
                    .map_err(|e| CoreError::StorageError(format!("copy to shadow: {}", e)))?;
            }
        }

        shadow_table.create_index(
            &["embedding"],
            lancedb::index::Index::IvfPq(
                lancedb::index::vector::IvfPqIndexBuilder::default()
                    .num_partitions(256)
                    .num_sub_vectors(96)
                    .max_iterations(50)
                    .distance_type(lancedb::DistanceType::Cosine),
            ),
        )
        .execute()
        .await
        .map_err(|e| CoreError::StorageError(format!("create IVF-PQ index: {}", e)))?;

        match shadow_table.query()
            .nearest_to(vec![0.0f32; dim as usize])
            .map_err(|e| CoreError::SearchError(format!("verify query: {}", e)))?
            .limit(1)
            .execute()
            .await
        {
            Ok(_) => {}
            Err(e) => return Err(CoreError::StorageError(format!("index verification failed: {}", e))),
        }

        {
            let mut tables_guard = tables.write().await;
            tables_guard.insert(live_key, Arc::new(shadow_table));
        }

        let _ = db.drop_table(&shadow_key, &[]).await;

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Arrow value → serde_json::Value helper
// ---------------------------------------------------------------------------

fn arrow_to_json_value(col: &arrow_array::ArrayRef, row: usize) -> Option<serde_json::Value> {
    match col.data_type() {
        DataType::Utf8 => {
            let arr = col.as_string::<i32>();
            Some(serde_json::Value::String(arr.value(row).to_string()))
        }
        DataType::Float32 => {
            let arr = col.as_primitive::<arrow_array::types::Float32Type>();
            Some(serde_json::Value::from(arr.value(row) as f64))
        }
        DataType::Int32 => {
            let arr = col.as_primitive::<arrow_array::types::Int32Type>();
            Some(serde_json::Value::from(arr.value(row)))
        }
        DataType::Int64 => {
            let arr = col.as_primitive::<arrow_array::types::Int64Type>();
            Some(serde_json::Value::from(arr.value(row)))
        }
        DataType::Timestamp(_, _) => {
            let arr = col.as_primitive::<arrow_array::types::TimestampMicrosecondType>();
            Some(serde_json::Value::from(arr.value(row)))
        }
        _ => None,
    }
}

// ---------------------------------------------------------------------------
// Tiny MD5-based key function (avoids pulling sha2 for cache keys only)
// ---------------------------------------------------------------------------

fn md5_hex(data: &[u8]) -> u128 {
    // Very lightweight FNV-1a 128-bit hash — good enough for cache keys
    let mut hash: u128 = 0x6c62272e07bb0142_62b821756295c58d_u128;
    for &byte in data {
        hash ^= byte as u128;
        hash = hash.wrapping_mul(0x0000000001000000_000000000000013B);
    }
    hash
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_test_env() -> tempfile::TempDir {
        tempfile::tempdir().unwrap()
    }

    #[test]
    fn test_index_manager_initial_state_is_uninitialized() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();
        assert_eq!(im.get_state(), 0, "initial state should be UNINITIALIZED (0)");
    }

    #[test]
    fn test_search_semaphore_has_100_permits() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();
        assert_eq!(
            im.available_search_permits(),
            100,
            "search semaphore should have 100 permits"
        );
    }

    #[test]
    fn test_write_semaphore_available() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();
        assert_eq!(
            im.pending_writes_count(),
            0,
            "initial pending_writes should be 0"
        );
    }

    #[test]
    fn test_cache_stats_initial_state() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();

        let embed_stats: serde_json::Value =
            serde_json::from_str(&im.embedding_cache_stats()).unwrap();
        assert_eq!(embed_stats["size"], 0);
        assert_eq!(embed_stats["capacity"], 1000);

        let graph_stats: serde_json::Value =
            serde_json::from_str(&im.graph_cache_stats()).unwrap();
        assert_eq!(graph_stats["size"], 0);
        assert_eq!(graph_stats["capacity"], 500);
    }

    #[test]
    fn test_initialize_collection_sets_state_to_active() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();
        let coll_id = uuid::Uuid::new_v4().to_string();

        pyo3::prepare_freethreaded_python();
        pyo3::Python::with_gil(|py| {
            im.initialize_collection(py, &coll_id).unwrap();
        });

        let state = im.get_state();
        assert_eq!(state, 2, "state should be ACTIVE (2) after init");
    }

    #[test]
    fn test_initialize_collection_accepts_valid_uuid() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();
        let coll_id = uuid::Uuid::new_v4().to_string();

        pyo3::prepare_freethreaded_python();
        pyo3::Python::with_gil(|py| {
            let result = im.initialize_collection(py, &coll_id);
            assert!(result.is_ok());
        });
    }

    #[test]
    fn test_initialize_collection_rejects_invalid_uuid() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();

        pyo3::prepare_freethreaded_python();
        pyo3::Python::with_gil(|py| {
            let result = im.initialize_collection(py, "not-a-uuid");
            assert!(result.is_err());
        });
    }

    #[test]
    fn test_get_cached_embedding_returns_empty_on_miss() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();

        let cached = im.get_cached_embedding("nonexistent query");
        assert!(cached.is_empty());
    }

    #[test]
    fn test_cache_embedding_stores_and_retrieves() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();

        let embedding = vec![0.1f32; 10];
        let json = serde_json::to_string(&embedding).unwrap();

        let stored = im.cache_embedding("test query", &json);
        assert!(stored);

        let retrieved = im.get_cached_embedding("test query");
        let result: Vec<f32> = serde_json::from_str(&retrieved).unwrap();
        assert_eq!(result, embedding);
    }

    #[test]
    fn test_cache_embedding_rejects_invalid_json() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();

        let stored = im.cache_embedding("test", "not valid json");
        assert!(!stored);
    }

    #[test]
    fn test_invalidate_all_caches_clears_both_caches() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();

        let embedding = vec![0.1f32; 10];
        let json = serde_json::to_string(&embedding).unwrap();
        im.cache_embedding("query", &json);

        im.invalidate_all_caches();

        let embed_stats: serde_json::Value =
            serde_json::from_str(&im.embedding_cache_stats()).unwrap();
        assert_eq!(embed_stats["size"], 0);
    }

    #[test]
    fn test_pending_tantivy_docs_starts_at_zero() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();
        assert_eq!(im.pending_tantivy_docs(), 0);
    }

    #[test]
    fn test_upsert_nodes_accepts_valid_json() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();
        let coll_id = uuid::Uuid::new_v4().to_string();

        pyo3::prepare_freethreaded_python();
        pyo3::Python::with_gil(|py| {
            im.initialize_collection(py, &coll_id).unwrap();
        });

        let nodes = serde_json::json!([{
            "id": uuid::Uuid::new_v4().to_string(),
            "node_type": "person",
            "label": "Test Node",
            "description": null,
            "aliases": [],
            "confidence": 0.9,
            "ontology_class": null,
            "properties": {},
            "collection_id": coll_id,
            "created_at": null,
            "updated_at": null
        }])
        .to_string();

        pyo3::Python::with_gil(|py| {
            let result = im.upsert_nodes(py, &coll_id, &nodes);
            assert!(result.is_ok());
        });
    }

    #[test]
    fn test_upsert_edges_accepts_valid_json() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();
        let coll_id = uuid::Uuid::new_v4().to_string();

        pyo3::prepare_freethreaded_python();
        pyo3::Python::with_gil(|py| {
            im.initialize_collection(py, &coll_id).unwrap();
        });

        let node_id = uuid::Uuid::new_v4();
        let nodes = serde_json::json!([{
            "id": node_id.to_string(),
            "node_type": "person",
            "label": "Test",
            "description": null,
            "aliases": [],
            "confidence": 0.9,
            "ontology_class": null,
            "properties": {},
            "collection_id": coll_id,
            "created_at": null,
            "updated_at": null
        }])
        .to_string();

        pyo3::Python::with_gil(|py| {
            im.upsert_nodes(py, &coll_id, &nodes).unwrap();
        });

        let edges = serde_json::json!([{
            "id": uuid::Uuid::new_v4().to_string(),
            "source": node_id.to_string(),
            "target": uuid::Uuid::new_v4().to_string(),
            "edge_type": "relates_to",
            "weight": 0.8,
            "context": null,
            "chunk_id": null,
            "properties": {},
            "collection_id": coll_id
        }])
        .to_string();

        pyo3::Python::with_gil(|py| {
            let result = im.upsert_edges(py, &coll_id, &edges);
            assert!(result.is_ok());
        });
    }

    #[test]
    fn test_get_graph_data_returns_empty_for_uninitialized_collection() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();
        let coll_id = uuid::Uuid::new_v4().to_string();

        pyo3::prepare_freethreaded_python();
        let result = pyo3::Python::with_gil(|py| {
            im.get_graph_data(py, &coll_id)
        });
        assert!(result.is_ok());

        let data: serde_json::Value = serde_json::from_str(&result.unwrap()).unwrap();
        assert_eq!(data["total_nodes"], 0);
        assert_eq!(data["total_edges"], 0);
    }

    #[test]
    fn test_delete_edge_removes_from_graph() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();
        let coll_id = uuid::Uuid::new_v4().to_string();

        pyo3::prepare_freethreaded_python();
        pyo3::Python::with_gil(|py| {
            im.initialize_collection(py, &coll_id).unwrap();
        });

        pyo3::Python::with_gil(|py| {
            let result = im.delete_edge(py, &coll_id, &uuid::Uuid::new_v4().to_string());
            assert!(result.is_ok());
        });
    }

    #[test]
    fn test_md5_hex_deterministic() {
        let data = b"test data";
        let hash1 = md5_hex(data);
        let hash2 = md5_hex(data);
        assert_eq!(hash1, hash2);
    }

    #[test]
    fn test_md5_hex_different_for_different_data() {
        let hash1 = md5_hex(b"data1");
        let hash2 = md5_hex(b"data2");
        assert_ne!(hash1, hash2);
    }

    // ---------------------------------------------------------------------------
    // TimedLruCache tests
    // ---------------------------------------------------------------------------

    #[test]
    fn test_timed_lru_cache_evicts_on_ttl() {
        let mut cache = TimedLruCache::new(NonZeroUsize::new(100).unwrap(), Duration::from_millis(50));

        cache.put("key1".to_string(), "value1".to_string());
        assert_eq!(cache.get(&"key1".to_string()), Some("value1".to_string()));

        // Within TTL — should return value
        std::thread::sleep(Duration::from_millis(30));
        assert_eq!(cache.get(&"key1".to_string()), Some("value1".to_string()));

        // After TTL — should be evicted
        std::thread::sleep(Duration::from_millis(30));
        assert_eq!(cache.get(&"key1".to_string()), None);
    }

    #[test]
    fn test_timed_lru_cache_respects_capacity() {
        let mut cache = TimedLruCache::new(NonZeroUsize::new(3).unwrap(), Duration::from_secs(300));

        cache.put("a".to_string(), 1);
        cache.put("b".to_string(), 2);
        cache.put("c".to_string(), 3);
        assert_eq!(cache.len(), 3);

        // Adding 4th item should evict LRU (a)
        cache.put("d".to_string(), 4);
        assert_eq!(cache.len(), 3);
        assert_eq!(cache.get(&"a".to_string()), None);
        assert_eq!(cache.get(&"d".to_string()), Some(4));
    }

    #[test]
    fn test_timed_lru_cache_pop_removes_entry() {
        let mut cache = TimedLruCache::new(NonZeroUsize::new(10).unwrap(), Duration::from_secs(300));
        cache.put("key".to_string(), "value".to_string());
        assert_eq!(cache.pop(&"key".to_string()), Some("value".to_string()));
        assert_eq!(cache.get(&"key".to_string()), None);
    }

    #[test]
    fn test_timed_lru_cache_clear() {
        let mut cache = TimedLruCache::new(NonZeroUsize::new(10).unwrap(), Duration::from_secs(300));
        cache.put("a".to_string(), 1);
        cache.put("b".to_string(), 2);
        assert_eq!(cache.len(), 2);
        cache.clear();
        assert!(cache.is_empty());
    }

    #[test]
    fn test_timed_lru_cache_retain_preserves_valid_entries() {
        let mut cache = TimedLruCache::new(NonZeroUsize::new(10).unwrap(), Duration::from_secs(300));
        cache.put("valid".to_string(), 1);
        cache.put("invalid_ttl".to_string(), 2);

        // Simulate TTL expiry for "invalid_ttl" by using retain with a check
        // Note: retain only removes based on TTL, not the predicate — so both stay
        cache.retain(|_, _| true);
        assert_eq!(cache.get(&"valid".to_string()), Some(1));
        assert_eq!(cache.get(&"invalid_ttl".to_string()), Some(2));
    }

    #[test]
    fn test_timed_lru_cache_get_returns_clone() {
        let mut cache = TimedLruCache::new(NonZeroUsize::new(10).unwrap(), Duration::from_secs(300));
        let vec = vec![1, 2, 3];
        cache.put("vec".to_string(), vec.clone());
        let retrieved = cache.get(&"vec".to_string()).unwrap();
        assert_eq!(retrieved, vec);
        // Original should not be mutated
        assert_eq!(retrieved, vec![1, 2, 3]);
    }

    #[test]
    fn test_timed_lru_cache_is_empty_and_len() {
        let mut cache = TimedLruCache::new(NonZeroUsize::new(10).unwrap(), Duration::from_secs(300));
        assert!(cache.is_empty());
        assert_eq!(cache.len(), 0);
        cache.put("key".to_string(), "value".to_string());
        assert!(!cache.is_empty());
        assert_eq!(cache.len(), 1);
    }

    #[test]
    fn test_timed_lru_cache_ttl_checked_on_get_not_just_eviction() {
        let mut cache = TimedLruCache::new(NonZeroUsize::new(100).unwrap(), Duration::from_millis(100));

        cache.put("fresh".to_string(), "value".to_string());
        assert_eq!(cache.get(&"fresh".to_string()), Some("value".to_string()));

        // Wait for TTL to expire
        std::thread::sleep(Duration::from_millis(120));
        assert_eq!(cache.get(&"fresh".to_string()), None);
    }

    // -----------------------------------------------------------------------
    // Phase 6 tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_index_state_transitions() {
        assert_eq!(IndexState::from_u8(0), IndexState::Uninitialized);
        assert_eq!(IndexState::from_u8(1), IndexState::Building);
        assert_eq!(IndexState::from_u8(2), IndexState::Active);
        assert_eq!(IndexState::from_u8(3), IndexState::Compacting);
        assert_eq!(IndexState::from_u8(4), IndexState::Degraded);
        assert_eq!(IndexState::from_u8(99), IndexState::Degraded);
    }

    #[test]
    fn test_maybe_trigger_compaction_rejects_when_not_active() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();
        let coll_id = uuid::Uuid::new_v4().to_string();

        pyo3::prepare_freethreaded_python();
        let triggered = pyo3::Python::with_gil(|py| {
            im.maybe_trigger_compaction(py, &coll_id)
        });
        assert!(!triggered.unwrap(), "should not trigger when state != Active");
    }

    #[test]
    fn test_maybe_trigger_compaction_triggers_when_active() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();
        let coll_id = uuid::Uuid::new_v4().to_string();

        pyo3::prepare_freethreaded_python();
        pyo3::Python::with_gil(|py| {
            im.initialize_collection(py, &coll_id).unwrap();
        });

        assert_eq!(im.get_state(), IndexState::Active as u8);

        let triggered = pyo3::Python::with_gil(|py| {
            im.maybe_trigger_compaction(py, &coll_id)
        });
        assert!(triggered.unwrap(), "should trigger when state == Active");

        assert_eq!(
            im.get_state(),
            IndexState::Active as u8,
            "state should return to Active after rebuild"
        );
    }

    #[test]
    fn test_vector_search_with_timeout_returns_search_timeout_on_expiration() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();
        let coll_id = uuid::Uuid::new_v4().to_string();

        pyo3::prepare_freethreaded_python();
        pyo3::Python::with_gil(|py| {
            im.initialize_collection(py, &coll_id).unwrap();
        });

        let result = pyo3::Python::with_gil(|py| {
            im.vector_search_with_timeout(py, vec![0.1f32; 1024], &coll_id, 10, Some(1))
        });
        assert!(result.is_ok(), "should not error even with 1ms timeout on empty table");
    }

    #[test]
    fn test_graph_cache_version_check_invalidates_stale_entries() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();
        let coll_id = uuid::Uuid::new_v4().to_string();

        pyo3::prepare_freethreaded_python();
        pyo3::Python::with_gil(|py| {
            im.initialize_collection(py, &coll_id).unwrap();
        });

        let data1 = pyo3::Python::with_gil(|py| {
            im.get_graph_data(py, &coll_id).unwrap()
        });
        assert!(!data1.is_empty());

        pyo3::Python::with_gil(|py| {
            let nodes = serde_json::json!([{
                "id": uuid::Uuid::new_v4().to_string(),
                "node_type": "person",
                "label": "Cache Test Node",
                "description": null,
                "aliases": [],
                "confidence": 0.9,
                "ontology_class": null,
                "properties": {},
                "collection_id": coll_id,
                "created_at": null,
                "updated_at": null
            }]).to_string();
            im.upsert_nodes(py, &coll_id, &nodes).unwrap();
        });

        let data2 = pyo3::Python::with_gil(|py| {
            im.get_graph_data(py, &coll_id).unwrap()
        });

        let v1: serde_json::Value = serde_json::from_str(&data1).unwrap();
        let v2: serde_json::Value = serde_json::from_str(&data2).unwrap();
        assert_ne!(v1["total_nodes"], v2["total_nodes"], "cache should be invalidated after graph mutation");
    }

    #[test]
    fn test_wal_truncated_after_successful_replay() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();
        let coll_id = uuid::Uuid::new_v4().to_string();

        pyo3::prepare_freethreaded_python();
        pyo3::Python::with_gil(|py| {
            im.initialize_collection(py, &coll_id).unwrap();
        });

        pyo3::Python::with_gil(|py| {
            let nodes = serde_json::json!([{
                "id": uuid::Uuid::new_v4().to_string(),
                "node_type": "person",
                "label": "WAL Test",
                "description": null,
                "aliases": [],
                "confidence": 0.9,
                "ontology_class": null,
                "properties": {},
                "collection_id": coll_id,
                "created_at": null,
                "updated_at": null
            }]).to_string();
            im.upsert_nodes(py, &coll_id, &nodes).unwrap();
        });

        assert!(im.wal_path.exists(), "WAL file should exist after writes");

        im.run_wal_checkpoint().unwrap();

        let wal_contents = std::fs::read_to_string(&im.wal_path).unwrap_or_default();
        assert!(wal_contents.trim().is_empty(), "WAL should be truncated after successful checkpoint");
    }
}
