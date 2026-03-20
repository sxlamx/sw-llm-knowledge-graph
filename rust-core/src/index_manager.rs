//! Index management with Tantivy search integration.

use crate::errors::CoreError;
use crate::models::{KnowledgeGraph, ChunkRecord, GraphEdge, GraphNode};
use crate::storage::SearchEngine;
use pyo3::prelude::*;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use tokio::sync::{RwLock as TokioRwLock, Semaphore};
use uuid::Uuid;

#[pyo3::pyclass]
pub struct IndexManager {
    pub search_engine: Arc<SearchEngine>,
    pub state: AtomicU64,
    pub graphs: Arc<TokioRwLock<HashMap<String, Arc<TokioRwLock<KnowledgeGraph>>>>>,
    pub collection_id: Arc<TokioRwLock<Option<Uuid>>>,
    pub pending_writes: AtomicU64,
    pub search_semaphore: Semaphore,
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
            graphs: Arc::new(TokioRwLock::new(HashMap::new())),
            collection_id: Arc::new(TokioRwLock::new(None)),
            pending_writes: AtomicU64::new(0),
            search_semaphore: Semaphore::new(10),
        })
    }

    pub fn initialize_collection(&self, collection_id: &str) -> PyResult<()> {
        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        
        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        
        let graphs_clone = Arc::clone(&self.graphs);
        let collection_id_clone = uuid;
        
        rt.block_on(async {
            self.state.store(1, Ordering::Release);
            *self.collection_id.write().await = Some(collection_id_clone);

            let graph: Arc<TokioRwLock<KnowledgeGraph>> = Arc::new(TokioRwLock::new(KnowledgeGraph::new(collection_id_clone)));
            let mut graphs = graphs_clone.write().await;
            graphs.insert(collection_id_clone.to_string(), graph);

            Ok::<(), CoreError>(())
        }).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    pub fn insert_chunks(&self, collection_id: &str, chunks_json: &str) -> PyResult<usize> {
        let chunks: Vec<ChunkRecord> = serde_json::from_str(chunks_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        
        let _uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        
        let count = self.search_engine.insert_chunks(chunks)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        self.pending_writes.fetch_add(count as u64, Ordering::AcqRel);
        Ok(count)
    }

    pub fn text_search(
        &self,
        collection_id: &str,
        query: &str,
        limit: usize,
    ) -> PyResult<String> {
        let results = self.search_engine.search(collection_id, query, limit)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        serde_json::to_string(&results)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
    }

    pub fn upsert_nodes(&self, collection_id: &str, nodes_json: &str) -> PyResult<()> {
        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        let nodes: Vec<GraphNode> = serde_json::from_str(nodes_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        
        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        
        let graphs_clone = Arc::clone(&self.graphs);
        
        rt.block_on(async {
            let graph: Arc<TokioRwLock<KnowledgeGraph>> = Self::get_or_create_graph_internal(&graphs_clone, uuid).await?;
            let mut graph = graph.write().await;
            graph.insert_nodes_batch(nodes);
            Ok::<(), CoreError>(())
        }).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    pub fn upsert_edges(&self, collection_id: &str, edges_json: &str) -> PyResult<()> {
        let uuid = Uuid::parse_str(collection_id)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        let edges: Vec<GraphEdge> = serde_json::from_str(edges_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        
        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        
        let graphs_clone = Arc::clone(&self.graphs);
        
        rt.block_on(async {
            let graph: Arc<TokioRwLock<KnowledgeGraph>> = Self::get_or_create_graph_internal(&graphs_clone, uuid).await?;
            let mut graph = graph.write().await;
            graph.insert_edges_batch(edges);
            Ok::<(), CoreError>(())
        }).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }
}

impl IndexManager {
    async fn get_or_create_graph_internal(
        graphs: &Arc<TokioRwLock<HashMap<String, Arc<TokioRwLock<KnowledgeGraph>>>>>,
        collection_id: Uuid,
    ) -> Result<Arc<TokioRwLock<KnowledgeGraph>>, CoreError> {
        {
            let graphs = graphs.read().await;
            if let Some(g) = graphs.get(&collection_id.to_string()) {
                return Ok(g.clone());
            }
        }

        let graph: Arc<TokioRwLock<KnowledgeGraph>> = Arc::new(TokioRwLock::new(KnowledgeGraph::new(collection_id)));
        {
            let mut graphs = graphs.write().await;
            graphs.insert(collection_id.to_string(), graph.clone());
        }

        Ok(graph)
    }
}
