//! Rust Core — PyO3 Python extension module.
//!
//! Exposes IndexManager, SearchEngine, and IngestionEngine to Python via PyO3.

#![recursion_limit = "2048"]

use pyo3::prelude::*;
use std::collections::HashMap;

pub mod errors;
pub mod models;
pub mod index_manager;
pub mod ingestion;
pub mod storage;
pub mod ontology;
pub mod graph;
pub mod wal;

use index_manager::IndexManager;
use models::*;
use ingestion::{FileScanner, DocumentExtractor, Chunker, FileEntry, FileType};
use graph::{export_graphml, export_json, EntityResolver, Resolution, MergeStrategy, bfs_subgraph};
use ontology::{Ontology, OntologyValidator};

#[pyclass]
pub struct PySearchEngine;

#[pymethods]
impl PySearchEngine {
    #[new]
    pub fn new() -> Self {
        Self
    }

    pub fn fuse_scores(
        &self,
        vector_scores: Vec<(String, f32)>,
        keyword_scores: Vec<(String, f32)>,
        graph_scores: Vec<(String, f32)>,
        w_vector: f32,
        w_keyword: f32,
        w_graph: f32,
    ) -> Vec<(String, f32)> {
        let mut all_ids: std::collections::HashSet<String> = std::collections::HashSet::new();
        for (id, _) in &vector_scores { all_ids.insert(id.clone()); }
        for (id, _) in &keyword_scores { all_ids.insert(id.clone()); }
        for (id, _) in &graph_scores { all_ids.insert(id.clone()); }

        let v_map: HashMap<String, f32> = vector_scores.into_iter().collect();
        let k_map: HashMap<String, f32> = keyword_scores.into_iter().collect();
        let g_map: HashMap<String, f32> = graph_scores.into_iter().collect();

        let mut results: Vec<(String, f32)> = all_ids
            .into_iter()
            .map(|id| {
                let v = v_map.get(&id).copied().unwrap_or(0.0);
                let k = k_map.get(&id).copied().unwrap_or(0.0);
                let g = g_map.get(&id).copied().unwrap_or(0.0);
                let score = v * w_vector + k * w_keyword + g * w_graph;
                (id, score)
            })
            .collect();

        results.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
        results
    }
}

impl Default for PySearchEngine {
    fn default() -> Self { Self::new() }
}

#[pyclass]
pub struct PyIngestionEngine;

#[pymethods]
impl PyIngestionEngine {
    #[new]
    pub fn new() -> Self {
        Self
    }

    pub fn scan_folder(
        &self,
        path: &str,
        allowed_roots: Vec<String>,
        max_depth: usize,
        max_files: usize,
    ) -> PyResult<String> {
        let root = std::path::PathBuf::from(path);
        let roots: Vec<std::path::PathBuf> = if allowed_roots.is_empty() {
            vec![root.clone()]
        } else {
            allowed_roots.into_iter().map(std::path::PathBuf::from).collect()
        };

        let scanner = FileScanner::new(root, roots)
            .with_max_depth(max_depth)
            .with_max_files(max_files);

        let entries: Vec<serde_json::Value> = scanner
            .scan()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?
            .into_iter()
            .map(|e| {
                serde_json::json!({
                    "path": e.path.display().to_string(),
                    "file_type": match e.file_type {
                        FileType::Pdf => "pdf",
                        FileType::Docx => "docx",
                        FileType::Markdown => "markdown",
                        FileType::Text => "text",
                        FileType::Html => "html",
                        FileType::Rst => "rst",
                        FileType::Unknown => "unknown",
                    },
                    "size_bytes": e.size_bytes,
                    "modified_at": e.modified_at
                        .duration_since(std::time::UNIX_EPOCH)
                        .map(|d| d.as_secs())
                        .unwrap_or(0),
                })
            })
            .collect();

        serde_json::to_string(&entries)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
    }

    pub fn extract_text(&self, path: &str, file_type: &str) -> PyResult<String> {
        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        let path_buf = std::path::PathBuf::from(path);
        let ext = path_buf.extension()
            .and_then(|e| e.to_str())
            .unwrap_or("");

        let ft = match (file_type, ext.to_lowercase().as_str()) {
            ("pdf", _) | (_, "pdf") => FileType::Pdf,
            ("docx", _) | (_, "docx") => FileType::Docx,
            ("markdown", _) | ("md", _) | (_, "md") | (_, "markdown") => FileType::Markdown,
            ("html", _) | (_, "html") | (_, "htm") => FileType::Html,
            ("text", _) | (_, "txt") | (_, "rst") => FileType::Text,
            _ => FileType::Unknown,
        };

        let entry = FileEntry {
            path: path_buf,
            file_type: ft,
            size_bytes: 0,
            modified_at: std::time::SystemTime::now(),
            blake3_hash: None,
        };

        let extractor = DocumentExtractor;
        let result = rt.block_on(async {
            extractor.extract(&entry).await
        }).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        let result_json = serde_json::json!({
            "title": result.title,
            "raw_text": result.raw_text,
            "pages": result.pages,
            "metadata": result.metadata,
        });
        serde_json::to_string(&result_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
    }

    pub fn chunk_text(
        &self,
        text: &str,
        pages_json: &str,
        chunk_size: usize,
        chunk_overlap: usize,
    ) -> PyResult<String> {
        let pages: Vec<ingestion::PageContent> = serde_json::from_str(pages_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let doc = ingestion::ExtractedDocument {
            title: None,
            raw_text: text.to_string(),
            pages,
            metadata: std::collections::HashMap::new(),
        };

        let chunker = Chunker::new(chunk_size, chunk_overlap);
        let chunks = chunker.chunk_document(&doc);

        serde_json::to_string(&chunks)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
    }
}

impl Default for PyIngestionEngine {
    fn default() -> Self { Self::new() }
}

#[pyclass]
pub struct PyOntologyValidator;

#[pymethods]
impl PyOntologyValidator {
    #[new]
    pub fn new() -> Self {
        Self
    }

    pub fn validate(
        &self,
        entities_json: &str,
        relationships_json: &str,
        confidence_threshold: f32,
    ) -> PyResult<String> {
        let ontology = Ontology::default_ontology();
        let validator = OntologyValidator::new(ontology, confidence_threshold);

        let entities: Vec<ExtractedEntity> = serde_json::from_str(entities_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let relationships: Vec<ExtractedRelationship> = serde_json::from_str(relationships_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let report = validator.validate_batch(entities, relationships);

        serde_json::to_string(&report)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
    }

    pub fn get_default_ontology(&self) -> PyResult<String> {
        let ontology = Ontology::default_ontology();
        serde_json::to_string(&ontology)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
    }
}

impl Default for PyOntologyValidator {
    fn default() -> Self { Self::new() }
}

#[pyfunction]
pub fn compute_blake3(path: &str) -> PyResult<String> {
    let path_buf = std::path::PathBuf::from(path);
    let hash = ingestion::compute_blake3_hash(&path_buf)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    Ok(blake3::Hash::from_bytes(hash).to_hex().to_string())
}

#[pyfunction]
#[pyo3(signature = (computed, stored = None))]
pub fn check_hash_matches(computed: &str, stored: Option<&str>) -> PyResult<bool> {
    let hash = blake3::Hash::from_hex(computed)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("Invalid hash: {}", e)))?;
    let computed_bytes: &[u8; 32] = hash.as_bytes();
    let mut arr = [0u8; 32];
    arr.copy_from_slice(computed_bytes);
    Ok(ingestion::hash_matches(stored, &arr))
}

#[pyfunction]
pub fn resolve_entity(
    entity_json: &str,
    existing_nodes_json: &str,
    embedding: Vec<f32>,
) -> PyResult<String> {
    let entity: ExtractedEntity = serde_json::from_str(entity_json)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

    let nodes: Vec<GraphNode> = serde_json::from_str(existing_nodes_json)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

    let mut embeddings: HashMap<String, Vec<f32>> = HashMap::new();
    embeddings.insert(entity.name.clone(), embedding);

    let resolver = EntityResolver::new();
    let resolution = resolver.resolve(&entity, &nodes, &embeddings);

    let result = match resolution {
        Resolution::Merge { existing_id, strategy } => {
            let strategy_str = match strategy {
                MergeStrategy::ExactMatch => "exact_match",
                MergeStrategy::FuzzyMatch { distance, cosine_sim } => {
                    return Ok(serde_json::json!({
                        "resolution": "merge",
                        "existing_id": existing_id.to_string(),
                        "strategy": {
                            "type": "fuzzy_match",
                            "distance": distance,
                            "cosine_sim": cosine_sim,
                        }
                    }).to_string());
                }
            };
            serde_json::json!({
                "resolution": "merge",
                "existing_id": existing_id.to_string(),
                "strategy": strategy_str
            })
        }
        Resolution::NewNode => {
            serde_json::json!({ "resolution": "new_node" })
        }
    };

    Ok(result.to_string())
}

#[pyfunction]
pub fn check_bfs_reachable(graph_json: &str, start_id: &str, target_id: &str) -> PyResult<bool> {
    use graph::traversal::bfs_reachable;
    let sg: SerializableGraph = serde_json::from_str(graph_json)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    let graph = KnowledgeGraph::from(sg);
    let start = uuid::Uuid::parse_str(start_id)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    let target = uuid::Uuid::parse_str(target_id)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    let reachable: std::collections::HashSet<uuid::Uuid> = bfs_reachable(&graph, &[start], 10, 0.0);
    Ok(reachable.contains(&target))
}

#[pyfunction]
pub fn check_shortest_path(graph_json: &str, start_id: &str, target_id: &str) -> PyResult<Option<String>> {
    use graph::traversal::find_shortest_path;
    let sg: SerializableGraph = serde_json::from_str(graph_json)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    let graph = KnowledgeGraph::from(sg);
    let start = uuid::Uuid::parse_str(start_id)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    let target = uuid::Uuid::parse_str(target_id)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    let path = find_shortest_path(&graph, start, target, 10);
    Ok(path.map(|p| serde_json::to_string(&p).unwrap_or_default()))
}

#[pyfunction]
pub fn get_bfs_subgraph(graph_json: &str, start_id: &str, max_hops: u32, min_weight: f32) -> PyResult<String> {
    let sg: SerializableGraph = serde_json::from_str(graph_json)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    let graph = KnowledgeGraph::from(sg);
    let start = uuid::Uuid::parse_str(start_id)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    let subgraph = bfs_subgraph(&graph, start, max_hops, min_weight);
    serde_json::to_string(&subgraph)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
}

#[pyfunction]
pub fn export_graph(graph_json: &str, format: &str) -> PyResult<String> {
    let sg: SerializableGraph = serde_json::from_str(graph_json)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    let graph = KnowledgeGraph::from(sg);
    match format {
        "graphml" => {
            let result = export_graphml(&graph);
            Ok(result)
        }
        "json" => {
            let result = export_json(&graph);
            Ok(serde_json::to_string(&result).unwrap_or_default())
        }
        _ => Err(pyo3::exceptions::PyValueError::new_err(
            "Unsupported format. Use 'graphml' or 'json'.".to_string(),
        )),
    }
}

#[pymodule]
fn rust_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    use tracing_subscriber::{fmt, EnvFilter};
    fmt().with_env_filter(EnvFilter::from_default_env()).init();

    m.add_class::<IndexManager>()?;
    m.add_class::<PySearchEngine>()?;
    m.add_class::<PyIngestionEngine>()?;
    m.add_class::<PyOntologyValidator>()?;
    m.add_function(wrap_pyfunction!(compute_blake3, m)?)?;
    m.add_function(wrap_pyfunction!(check_hash_matches, m)?)?;
    m.add_function(wrap_pyfunction!(resolve_entity, m)?)?;
    m.add_function(wrap_pyfunction!(check_bfs_reachable, m)?)?;
    m.add_function(wrap_pyfunction!(check_shortest_path, m)?)?;
    m.add_function(wrap_pyfunction!(get_bfs_subgraph, m)?)?;
    m.add_function(wrap_pyfunction!(export_graph, m)?)?;
    Ok(())
}
