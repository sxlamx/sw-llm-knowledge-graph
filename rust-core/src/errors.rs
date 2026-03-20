//! Error types for the knowledge graph engine.

use thiserror::Error;

#[derive(Error, Debug)]
pub enum CoreError {
    #[error("Table not found: {0}")]
    TableNotFound(String),

    #[error("Collection not found: {0}")]
    CollectionNotFound(String),

    #[error("Graph not found for collection: {0}")]
    GraphNotFound(String),

    #[error("Node not found: {0}")]
    NodeNotFound(String),

    #[error("Edge not found: {0}")]
    EdgeNotFound(String),

    #[error("Document not found: {0}")]
    DocumentNotFound(String),

    #[error("Index error: {0}")]
    IndexError(String),

    #[error("Search error: {0}")]
    SearchError(String),

    #[error("Search timeout after {timeout_ms}ms")]
    SearchTimeout { timeout_ms: u64 },

    #[error("Storage error: {0}")]
    StorageError(String),

    #[error("Tantivy error: {0}")]
    TantivyError(String),

    #[error("IO error: {0}")]
    IoError(String),

    #[error("Serialization error: {0}")]
    SerializationError(String),

    #[error("Invalid path: {0}")]
    InvalidPath(String),

    #[error("Path traversal attempt denied: {path} not within {allowed_root}")]
    PathTraversal { path: String, allowed_root: String },

    #[error("Unsupported file type: {0}")]
    UnsupportedFileType(String),

    #[error("Ontology validation error: {0}")]
    OntologyValidationError(String),

    #[error("Entity resolution error: {0}")]
    EntityResolutionError(String),

    #[error("Graph error: {0}")]
    GraphError(String),

    #[error("Configuration error: {0}")]
    ConfigError(String),

    #[error("Semaphore error: {0}")]
    SemaphoreError(String),

    #[error("Watcher error: {0}")]
    WatcherError(String),
}

impl From<std::io::Error> for CoreError {
    fn from(e: std::io::Error) -> Self {
        CoreError::IoError(e.to_string())
    }
}

impl From<walkdir::Error> for CoreError {
    fn from(e: walkdir::Error) -> Self {
        CoreError::IoError(e.to_string())
    }
}

impl From<notify::Error> for CoreError {
    fn from(e: notify::Error) -> Self {
        CoreError::WatcherError(e.to_string())
    }
}

impl From<serde_json::Error> for CoreError {
    fn from(e: serde_json::Error) -> Self {
        CoreError::SerializationError(e.to_string())
    }
}

impl From<tantivy::TantivyError> for CoreError {
    fn from(e: tantivy::TantivyError) -> Self {
        CoreError::TantivyError(e.to_string())
    }
}

impl From<tokio::sync::AcquireError> for CoreError {
    fn from(e: tokio::sync::AcquireError) -> Self {
        CoreError::SemaphoreError(e.to_string())
    }
}

impl From<CoreError> for pyo3::PyErr {
    fn from(e: CoreError) -> Self {
        pyo3::exceptions::PyRuntimeError::new_err(e.to_string())
    }
}
