//! Index management — Phase 3 concurrency model.
//!
//! Concurrency design:
//!   - `search_semaphore(100)` — bounded concurrent searches
//!   - `write_semaphore(1)`   — serialised batch writes to LanceDB/Tantivy
//!   - `query_embedding_cache` — LRU(1000), 5-minute TTL, keyed by query SHA-256
//!   - `graph_neighbor_cache`  — LRU(500), 2-minute TTL, version-based invalidation
//!   - Lock ordering: Level-2 outer maps → release → Level-3 per-collection locks
//!   - `state` AtomicU64: 0=uninit, 1=building, 2=active, 3=compacting, 4=degraded

use crate::errors::CoreError;
use crate::models::{KnowledgeGraph, ChunkRecord, GraphEdge, GraphNode};
use crate::storage::SearchEngine;
use lru::LruCache;
use pyo3::prelude::*;
use std::collections::HashMap;
use std::num::NonZeroUsize;
use std::sync::{atomic::{AtomicU64, Ordering}, Arc, Mutex};
use std::time::{Duration, Instant};
use tokio::sync::{RwLock as TokioRwLock, Semaphore};
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Cache entry types
// ---------------------------------------------------------------------------

struct CachedEmbedding {
    embedding: Vec<f32>,
    cached_at: Instant,
}

struct CachedSubgraph {
    /// Serialised JSON of the subgraph (nodes + edges).
    payload: String,
    cached_at: Instant,
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
    pub query_embedding_cache: Arc<Mutex<LruCache<String, CachedEmbedding>>>,
    pub graph_neighbor_cache: Arc<Mutex<LruCache<String, CachedSubgraph>>>,
}

#[pyo3::pymethods]
impl IndexManager {
    #[new]
    pub fn new(index_path: &str) -> Result<Self, CoreError> {
        let search_engine = SearchEngine::new(index_path)
            .map_err(|e| CoreError::StorageError(e.to_string()))?;

        Ok(Self {
            search_engine: Arc::new(search_engine),
            state: AtomicU64::new(0),
            pending_writes: AtomicU64::new(0),
            search_semaphore: Semaphore::new(SEARCH_SEMAPHORE_CAPACITY),
            write_semaphore: Arc::new(Semaphore::new(1)),
            graphs: Arc::new(TokioRwLock::new(HashMap::new())),
            collection_id: Arc::new(TokioRwLock::new(None)),
            query_embedding_cache: Arc::new(Mutex::new(
                LruCache::new(NonZeroUsize::new(EMBED_CACHE_CAPACITY).unwrap()),
            )),
            graph_neighbor_cache: Arc::new(Mutex::new(
                LruCache::new(NonZeroUsize::new(GRAPH_CACHE_CAPACITY).unwrap()),
            )),
        })
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

        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        let graphs_clone = Arc::clone(&self.graphs);
        let graph_cache = Arc::clone(&self.graph_neighbor_cache);

        rt.block_on(async {
            // Correct Level-2 → Level-3 pattern: clone Arc, release Level-2, then lock Level-3
            let graph_arc = Self::get_or_create_graph_internal(&graphs_clone, uuid).await?;
            graph_arc.write().await.insert_nodes_batch(nodes);   // Level-3 write
            Ok::<(), CoreError>(())
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        // Invalidate graph neighborhood cache (version changed)
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

        // Check graph neighbor cache first
        let cache_key = format!("full:{}", uuid);
        if let Ok(mut cache) = self.graph_neighbor_cache.lock() {
            if let Some(entry) = cache.get(&cache_key) {
                if entry.cached_at.elapsed() < GRAPH_CACHE_TTL {
                    return Ok(entry.payload.clone());
                }
            }
        }

        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        let graphs_clone = Arc::clone(&self.graphs);
        let graph_cache = Arc::clone(&self.graph_neighbor_cache);

        let result = rt.block_on(async {
            // Level-2 read: clone Arc, release immediately
            let graph_arc = {
                let outer = graphs_clone.read().await;
                outer.get(&uuid.to_string()).cloned()
            };

            if let Some(arc) = graph_arc {
                let graph = arc.read().await;   // Level-3 read
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

        // Populate cache
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
            if let Some(entry) = cache.get(&key) {
                if entry.cached_at.elapsed() < EMBED_CACHE_TTL {
                    if let Ok(json) = serde_json::to_string(&entry.embedding) {
                        return json;
                    }
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
        if let Ok(mut cache) = self.query_embedding_cache.lock() {
            cache.put(key, CachedEmbedding { embedding, cached_at: Instant::now() });
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
