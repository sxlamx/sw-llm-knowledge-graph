//! Search storage layer using Tantivy for full-text search.
//!
//! Phase 3 batch-committer design:
//!   - `insert_chunks` stages documents into the Tantivy writer **without committing**.
//!   - `commit_pending` flushes staged docs; returns `true` if a commit was actually issued.
//!   - `pending_doc_count` exposes how many staged but un-committed documents are buffered.
//!   - The caller (IndexManager::flush_tantivy) is responsible for periodic commits;
//!     Python startup wires this up as an asyncio task every 500 ms.

use crate::errors::CoreError;
use crate::models::ChunkRecord;
use std::sync::{
    atomic::{AtomicU64, Ordering},
    Arc, RwLock,
};
use tantivy::{
    collector::TopDocs,
    directory::MmapDirectory,
    doc,
    query::QueryParser,
    schema::{Field, Schema, Value, STORED, STRING, TEXT},
    Index, IndexReader, IndexWriter, ReloadPolicy, TantivyDocument,
};

pub struct SearchEngine {
    index: Index,
    reader: IndexReader,
    writer: Arc<RwLock<IndexWriter>>,
    id_field: Field,
    collection_id_field: Field,
    text_field: Field,
    doc_id_field: Field,
    /// Monotonically increasing count of staged-but-uncommitted documents.
    pending_doc_count: Arc<AtomicU64>,
}

impl SearchEngine {
    pub fn new(index_path: &str) -> Result<Self, CoreError> {
        let mut schema_builder = Schema::builder();
        let id_field = schema_builder.add_text_field("id", STRING | STORED);
        let collection_id_field = schema_builder.add_text_field("collection_id", STRING | STORED);
        let text_field = schema_builder.add_text_field("text", TEXT | STORED);
        let doc_id_field = schema_builder.add_text_field("doc_id", STRING | STORED);
        let schema = schema_builder.build();

        let dir =
            MmapDirectory::open(index_path).map_err(|e| CoreError::StorageError(e.to_string()))?;
        let index = Index::open_or_create(dir, schema.clone())
            .map_err(|e| CoreError::StorageError(e.to_string()))?;

        let reader = index
            .reader_builder()
            .reload_policy(ReloadPolicy::OnCommitWithDelay)
            .try_into()
            .map_err(|e: tantivy::TantivyError| CoreError::StorageError(e.to_string()))?;

        let writer = index
            .writer(50_000_000)
            .map_err(|e| CoreError::StorageError(e.to_string()))?;

        Ok(Self {
            index,
            reader,
            writer: Arc::new(RwLock::new(writer)),
            id_field,
            collection_id_field,
            text_field,
            doc_id_field,
            pending_doc_count: Arc::new(AtomicU64::new(0)),
        })
    }

    /// Stage `chunks` into the Tantivy writer without committing.
    ///
    /// Returns the number of staged documents.  The caller is responsible for
    /// calling [`commit_pending`] either explicitly or via the batch committer.
    pub fn insert_chunks(&self, chunks: Vec<ChunkRecord>) -> Result<usize, CoreError> {
        let count = chunks.len();
        let mut writer = self
            .writer
            .write()
            .map_err(|e| CoreError::StorageError(format!("Lock error: {}", e)))?;

        for chunk in chunks {
            writer
                .add_document(doc!(
                    self.id_field => chunk.id.as_str(),
                    self.collection_id_field => chunk.collection_id.as_str(),
                    self.text_field => chunk.text.as_str(),
                    self.doc_id_field => chunk.doc_id.as_str(),
                ))
                .map_err(|e| CoreError::StorageError(e.to_string()))?;
        }

        // Track staged-but-uncommitted docs; do NOT commit here.
        self.pending_doc_count.fetch_add(count as u64, Ordering::Release);
        Ok(count)
    }

    /// Flush all staged documents to the Tantivy index.
    ///
    /// Returns `true` if there were pending documents and a commit was issued,
    /// `false` if there was nothing to commit.
    pub fn commit_pending(&self) -> Result<bool, CoreError> {
        let pending = self.pending_doc_count.load(Ordering::Acquire);
        if pending == 0 {
            return Ok(false);
        }

        let mut writer = self
            .writer
            .write()
            .map_err(|e| CoreError::StorageError(format!("Lock error: {}", e)))?;

        writer
            .commit()
            .map_err(|e| CoreError::StorageError(e.to_string()))?;

        self.pending_doc_count.store(0, Ordering::Release);
        Ok(true)
    }

    /// How many staged documents are waiting for the next commit.
    pub fn pending_doc_count(&self) -> u64 {
        self.pending_doc_count.load(Ordering::Relaxed)
    }

    pub fn search(
        &self,
        collection_id: &str,
        query: &str,
        limit: usize,
    ) -> Result<Vec<serde_json::Value>, CoreError> {
        let searcher = self.reader.searcher();
        let query_parser = QueryParser::for_index(&self.index, vec![self.text_field]);
        let parsed_query = query_parser
            .parse_query(query)
            .map_err(|e| CoreError::SearchError(e.to_string()))?;

        let top_docs = searcher
            .search(&parsed_query, &TopDocs::with_limit(limit))
            .map_err(|e| CoreError::SearchError(e.to_string()))?;

        let mut results = Vec::new();
        for (_score, doc_address) in top_docs {
            let doc: TantivyDocument = searcher
                .doc(doc_address)
                .map_err(|e| CoreError::SearchError(e.to_string()))?;

            let id = doc
                .get_first(self.id_field)
                .and_then(|v| v.as_str())
                .unwrap_or_default()
                .to_string();
            let cid = doc
                .get_first(self.collection_id_field)
                .and_then(|v| v.as_str())
                .unwrap_or_default()
                .to_string();
            let text = doc
                .get_first(self.text_field)
                .and_then(|v| v.as_str())
                .unwrap_or_default()
                .to_string();
            let doc_id = doc
                .get_first(self.doc_id_field)
                .and_then(|v| v.as_str())
                .unwrap_or_default()
                .to_string();

            if cid == collection_id {
                results.push(serde_json::json!({
                    "id": id,
                    "text": text,
                    "doc_id": doc_id,
                }));
            }
        }
        Ok(results)
    }
}
