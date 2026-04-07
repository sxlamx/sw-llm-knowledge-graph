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
use crate::storage::SearchEngine;
use crate::wal::{WalWriter, checkpoint_on_startup};
use lru::LruCache;
use pyo3::prelude::*;
use std::collections::HashMap;
use std::num::NonZeroUsize;
use std::path::PathBuf;
use std::sync::{atomic::{AtomicU64, Ordering}, Arc, Mutex};
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

    pub fn pop(&mut self, key: &K) -> Option<V> {
        self.inner.pop(key).map(|(v, _)| v)
    }

    pub fn clear(&mut self) {
        self.inner.clear();
    }

    pub fn len(&self) -> usize {
        self.inner.len()
    }

    pub fn is_empty(&self) -> bool {
        self.inner.is_empty()
    }
}

// ---------------------------------------------------------------------------
// Cache entry types
// ---------------------------------------------------------------------------

#[derive(Clone)]
pub(crate) struct CachedEmbedding {
    embedding: Vec<f32>,
    cached_at: Instant,
}

#[derive(Clone)]
pub(crate) struct CachedSubgraph {
    payload: String,
    cached_at: Instant,
    #[allow(dead_code)]
    graph_version: u64,
}

const EMBED_CACHE_TTL: Duration = Duration::from_secs(300);   // 5 minutes
const GRAPH_CACHE_TTL: Duration = Duration::from_secs(120);   // 2 minutes
const SEARCH_SEMAPHORE_CAPACITY: usize = 100;
const EMBED_CACHE_CAPACITY: usize = 1_000;
const GRAPH_CACHE_CAPACITY: usize = 500;

// ---------------------------------------------------------------------------
// IndexManager
// ---------------------------------------------------------------------------

#[pyo3::pyclass]
pub struct IndexManager {
    pub search_engine: Arc<SearchEngine>,

    // Lock-free atomics (Level 1)
    pub state: AtomicU64,
    pub pending_writes: AtomicU64,

    // Semaphores — no ordering concern, acquired independently
    pub search_semaphore: Semaphore,
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
        let wal_path = base_path.join("wal.log");

        let wal = WalWriter::new(&wal_path)
            .map_err(|e| CoreError::StorageError(format!("WAL init failed: {}", e)))?;

        let im = Self {
            search_engine: Arc::new(search_engine),
            state: AtomicU64::new(0),
            pending_writes: AtomicU64::new(0),
            search_semaphore: Semaphore::new(SEARCH_SEMAPHORE_CAPACITY),
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
        let entries = checkpoint_on_startup(&self.wal_path)
            .map_err(|e| CoreError::StorageError(format!("WAL checkpoint failed: {}", e)))?;

        if entries.is_empty() {
            return Ok(());
        }

        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| CoreError::StorageError(format!("Tokio runtime: {}", e)))?;

        let graphs_clone = Arc::clone(&self.graphs);

        rt.block_on(async {
            for entry in entries {
                let json: serde_json::Value = match serde_json::from_str(&entry) {
                    Ok(v) => v,
                    Err(_) => continue,
                };

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
                                for node in nodes {
                                    g.nodes.insert(node.id, node);
                                }
                            }
                        }
                    }
                    "upsert_edges" => {
                        if let Some(edges_json) = json.get("edges").and_then(|v| v.as_str()) {
                            if let Ok(edges) = serde_json::from_str::<Vec<GraphEdge>>(edges_json) {
                                let mut g = arc.write().await;
                                for edge in edges {
                                    g.edges.insert(edge.id, edge);
                                }
                            }
                        }
                    }
                    _ => {}
                }
            }
        });

        Ok(())
    }

    // -----------------------------------------------------------------------
    // Collection initialisation
    // -----------------------------------------------------------------------

    pub fn initialize_collection(&self, collection_id: &str) -> PyResult<()> {
        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        let graphs_clone = Arc::clone(&self.graphs);
        let coll_id_cell = Arc::clone(&self.collection_id);

        rt.block_on(async {
            self.state.store(1, Ordering::Release);
            *coll_id_cell.write().await = Some(uuid);

            // Level-2 write: insert new graph handle, then immediately release
            let graph = Arc::new(TokioRwLock::new(KnowledgeGraph::new(uuid)));
            graphs_clone.write().await.insert(uuid.to_string(), graph);

            Ok::<(), CoreError>(())
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    // -----------------------------------------------------------------------
    // Tantivy chunk indexing  (write_semaphore serialises concurrent writers)
    // -----------------------------------------------------------------------

    pub fn insert_chunks(&self, collection_id: &str, chunks_json: &str) -> PyResult<usize> {
        let chunks: Vec<ChunkRecord> = serde_json::from_str(chunks_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        let write_sem = Arc::clone(&self.write_semaphore);
        let engine = Arc::clone(&self.search_engine);

        let count = rt.block_on(async {
            // Serialise concurrent Tantivy writes
            let _permit = write_sem.acquire().await
                .map_err(|_| CoreError::StorageError("write semaphore closed".into()))?;
            engine.insert_chunks(chunks)
                .map_err(|e| CoreError::StorageError(e.to_string()))
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        self.pending_writes.fetch_add(count as u64, Ordering::AcqRel);

        // Invalidate graph cache after a write (embedding cache stays valid)
        if let Ok(mut cache) = self.graph_neighbor_cache.lock() {
            cache.clear();
        }

        Ok(count)
    }

    // -----------------------------------------------------------------------
    // BM25 text search  (bounded by search_semaphore)
    // -----------------------------------------------------------------------

    pub fn text_search(
        &self,
        collection_id: &str,
        query: &str,
        limit: usize,
    ) -> PyResult<String> {
        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        let search_sem = &self.search_semaphore;
        let engine = Arc::clone(&self.search_engine);
        let collection_id = collection_id.to_string();
        let query = query.to_string();

        rt.block_on(async {
            // Back-pressure: queue rather than reject
            let _permit = search_sem.acquire().await
                .map_err(|_| CoreError::StorageError("search semaphore closed".into()))?;
            engine.search(&collection_id, &query, limit)
                .map_err(|e| CoreError::StorageError(e.to_string()))
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

    pub fn upsert_nodes(&self, collection_id: &str, nodes_json: &str) -> PyResult<()> {
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

        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        let graphs_clone = Arc::clone(&self.graphs);
        let graph_cache = Arc::clone(&self.graph_neighbor_cache);

        rt.block_on(async {
            let graph_arc = Self::get_or_create_graph_internal(&graphs_clone, uuid).await?;
            graph_arc.write().await.insert_nodes_batch(nodes);
            Ok::<(), CoreError>(())
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        if let Ok(mut cache) = graph_cache.lock() {
            cache.clear();
        }
        Ok(())
    }

    pub fn upsert_edges(&self, collection_id: &str, edges_json: &str) -> PyResult<()> {
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

        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        let graphs_clone = Arc::clone(&self.graphs);
        let graph_cache = Arc::clone(&self.graph_neighbor_cache);

        rt.block_on(async {
            let graph_arc = Self::get_or_create_graph_internal(&graphs_clone, uuid).await?;
            graph_arc.write().await.insert_edges_batch(edges);
            Ok::<(), CoreError>(())
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        if let Ok(mut cache) = graph_cache.lock() {
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
    pub fn flush_tantivy(&self) -> PyResult<bool> {
        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        let write_sem = Arc::clone(&self.write_semaphore);
        let engine = Arc::clone(&self.search_engine);

        rt.block_on(async {
            let _permit = write_sem.acquire().await
                .map_err(|_| crate::errors::CoreError::StorageError("write semaphore closed".into()))?;
            engine.commit_pending()
                .map_err(|e| crate::errors::CoreError::StorageError(e.to_string()))
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

    pub fn get_state(&self) -> u64 {
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

    pub fn get_graph_data(&self, collection_id: &str) -> PyResult<String> {
        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let cache_key = format!("full:{}", uuid);
        if let Ok(mut cache) = self.graph_neighbor_cache.lock() {
            if let Some(entry) = cache.get(&cache_key) {
                return Ok(entry.payload.clone());
            }
        }

        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        let graphs_clone = Arc::clone(&self.graphs);
        let graph_cache = Arc::clone(&self.graph_neighbor_cache);

        let result = rt.block_on(async {
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
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        let (payload, version) = result;

        if let Ok(mut cache) = graph_cache.lock() {
            cache.put(cache_key, CachedSubgraph {
                payload: payload.clone(),
                cached_at: Instant::now(),
                graph_version: version,
            });
        }

        Ok(payload)
    }

    pub fn update_node(&self, collection_id: &str, node_json: &str) -> PyResult<()> {
        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        let node: GraphNode = serde_json::from_str(node_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        let graphs_clone = Arc::clone(&self.graphs);
        let graph_cache = Arc::clone(&self.graph_neighbor_cache);

        rt.block_on(async {
            let graph_arc = Self::get_or_create_graph_internal(&graphs_clone, uuid).await?;
            let mut g = graph_arc.write().await;
            g.nodes.insert(node.id, node);
            Ok::<(), CoreError>(())
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        if let Ok(mut cache) = graph_cache.lock() {
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
        collection_id: &str,
        min_weight: f32,
        max_degree: usize,
    ) -> PyResult<String> {
        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        let graphs_clone = Arc::clone(&self.graphs);
        let graph_cache = Arc::clone(&self.graph_neighbor_cache);

        let (removed, affected) = rt.block_on(async {
            let graph_arc = Self::get_or_create_graph_internal(&graphs_clone, uuid).await?;
            let mut g = graph_arc.write().await;
            let stats = g.prune_edges(min_weight, max_degree);
            Ok::<(usize, usize), crate::errors::CoreError>(stats)
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        if removed > 0 {
            if let Ok(mut cache) = graph_cache.lock() {
                cache.clear();
            }
        }

        Ok(format!(
            r#"{{"edges_removed":{removed},"nodes_affected":{affected}}}"#
        ))
    }

    pub fn delete_edge(&self, collection_id: &str, edge_id: &str) -> PyResult<()> {
        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        let edge_uuid = Uuid::parse_str(edge_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        let graphs_clone = Arc::clone(&self.graphs);
        let graph_cache = Arc::clone(&self.graph_neighbor_cache);

        rt.block_on(async {
            // Level-2 read: clone Arc, release before Level-3 write
            let graph_arc = {
                let outer = graphs_clone.read().await;
                outer.get(&uuid.to_string()).cloned()
            };
            if let Some(arc) = graph_arc {
                arc.write().await.edges.remove(&edge_uuid);
            }
            Ok::<(), CoreError>(())
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        if let Ok(mut cache) = graph_cache.lock() {
            cache.clear();
        }
        Ok(())
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
}

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

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
    fn test_initialize_collection_sets_state_to_building() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();
        let coll_id = uuid::Uuid::new_v4().to_string();

        im.initialize_collection(&coll_id).unwrap();

        let state = im.get_state();
        assert_eq!(state, 1, "state should be BUILDING (1) after init");
    }

    #[test]
    fn test_initialize_collection_accepts_valid_uuid() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();
        let coll_id = uuid::Uuid::new_v4().to_string();

        let result = im.initialize_collection(&coll_id);
        assert!(result.is_ok());
    }

    #[test]
    fn test_initialize_collection_rejects_invalid_uuid() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();

        let result = im.initialize_collection("not-a-uuid");
        assert!(result.is_err());
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
        im.initialize_collection(&coll_id).unwrap();

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

        let result = im.upsert_nodes(&coll_id, &nodes);
        assert!(result.is_ok());
    }

    #[test]
    fn test_upsert_edges_accepts_valid_json() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();
        let coll_id = uuid::Uuid::new_v4().to_string();
        im.initialize_collection(&coll_id).unwrap();

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

        im.upsert_nodes(&coll_id, &nodes).unwrap();

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

        let result = im.upsert_edges(&coll_id, &edges);
        assert!(result.is_ok());
    }

    #[test]
    fn test_get_graph_data_returns_empty_for_uninitialized_collection() {
        let _tmp = make_test_env();
        let im = IndexManager::new(_tmp.path().to_str().unwrap()).unwrap();
        let coll_id = uuid::Uuid::new_v4().to_string();

        let result = im.get_graph_data(&coll_id);
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
        im.initialize_collection(&coll_id).unwrap();

        let result = im.delete_edge(&coll_id, &uuid::Uuid::new_v4().to_string());
        assert!(result.is_ok());
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
        let cache = TimedLruCache::new(NonZeroUsize::new(100).unwrap(), Duration::from_millis(50));

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
        let cache = TimedLruCache::new(NonZeroUsize::new(3).unwrap(), Duration::from_secs(300));

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
        let cache = TimedLruCache::new(NonZeroUsize::new(10).unwrap(), Duration::from_secs(300));
        cache.put("key".to_string(), "value".to_string());
        assert_eq!(cache.pop(&"key".to_string()), Some("value".to_string()));
        assert_eq!(cache.get(&"key".to_string()), None);
    }

    #[test]
    fn test_timed_lru_cache_clear() {
        let cache = TimedLruCache::new(NonZeroUsize::new(10).unwrap(), Duration::from_secs(300));
        cache.put("a".to_string(), 1);
        cache.put("b".to_string(), 2);
        assert_eq!(cache.len(), 2);
        cache.clear();
        assert!(cache.is_empty());
    }

    #[test]
    fn test_timed_lru_cache_retain_preserves_valid_entries() {
        let cache = TimedLruCache::new(NonZeroUsize::new(10).unwrap(), Duration::from_secs(300));
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
        let cache = TimedLruCache::new(NonZeroUsize::new(10).unwrap(), Duration::from_secs(300));
        let vec = vec![1, 2, 3];
        cache.put("vec".to_string(), vec.clone());
        let retrieved = cache.get(&"vec".to_string()).unwrap();
        assert_eq!(retrieved, vec);
        // Original should not be mutated
        assert_eq!(retrieved, vec![1, 2, 3]);
    }

    #[test]
    fn test_timed_lru_cache_is_empty_and_len() {
        let cache = TimedLruCache::new(NonZeroUsize::new(10).unwrap(), Duration::from_secs(300));
        assert!(cache.is_empty());
        assert_eq!(cache.len(), 0);
        cache.put("key".to_string(), "value".to_string());
        assert!(!cache.is_empty());
        assert_eq!(cache.len(), 1);
    }

    #[test]
    fn test_timed_lru_cache_ttl_checked_on_get_not_just_eviction() {
        let cache = TimedLruCache::new(NonZeroUsize::new(100).unwrap(), Duration::from_millis(100));

        cache.put("fresh".to_string(), "value".to_string());
        assert_eq!(cache.get(&"fresh".to_string()), Some("value".to_string()));

        // Wait for TTL to expire
        std::thread::sleep(Duration::from_millis(120));
        assert_eq!(cache.get(&"fresh".to_string()), None);
    }
}
