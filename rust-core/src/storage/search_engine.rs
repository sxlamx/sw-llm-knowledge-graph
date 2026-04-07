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

/// Sigmoid normalization for BM25 scores — maps raw scores to [0, 1] range.
///
/// BM25 scores are unbounded (typically 0–20 for short queries) so they must
/// be normalized before weighted fusion with bounded vector/graph scores.
#[inline]
pub fn normalize_bm25_score(raw: f32) -> f32 {
    raw / (raw + 1.0)
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
        let writer = self
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

        self.reader
            .reload()
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
                    "collection_id": cid,
                    "bm25_score": _score,
                }));
            }
        }
            Ok(results)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn make_test_engine(tmpdir: &std::path::Path) -> SearchEngine {
        SearchEngine::new(tmpdir.to_str().unwrap()).unwrap()
    }

    fn make_chunk(text: &str, collection_id: &str) -> ChunkRecord {
        ChunkRecord {
            id: uuid::Uuid::new_v4().to_string(),
            doc_id: uuid::Uuid::new_v4().to_string(),
            collection_id: collection_id.to_string(),
            text: text.to_string(),
            contextual_text: text.to_string(),
            embedding: vec![0.0f32; 1024],
            position: 0,
            token_count: Some(2),
            page: Some(1),
            topics: vec![],
            created_at: 1234567890,
        }
    }

    #[test]
    fn test_search_engine_new_creates_index() {
        let tmp = tempdir().unwrap();
        let engine = make_test_engine(tmp.path());
        assert!(engine.pending_doc_count() == 0);
    }

    #[test]
    fn test_insert_chunks_stages_without_commit() {
        let tmp = tempdir().unwrap();
        let engine = make_test_engine(tmp.path());

        let chunk = make_chunk("hello world test document", "coll1");
        let count = engine.insert_chunks(vec![chunk]).unwrap();

        assert_eq!(count, 1);
        assert_eq!(engine.pending_doc_count(), 1);
    }

    #[test]
    fn test_insert_chunks_increments_pending_count() {
        let tmp = tempdir().unwrap();
        let engine = make_test_engine(tmp.path());

        let chunks = vec![
            make_chunk("document one", "coll1"),
            make_chunk("document two", "coll1"),
            make_chunk("document three", "coll1"),
        ];
        engine.insert_chunks(chunks).unwrap();

        assert_eq!(engine.pending_doc_count(), 3);
    }

    #[test]
    fn test_commit_pending_returns_false_when_nothing_to_commit() {
        let tmp = tempdir().unwrap();
        let engine = make_test_engine(tmp.path());

        let committed = engine.commit_pending().unwrap();
        assert!(!committed, "should return false when no pending docs");
        assert_eq!(engine.pending_doc_count(), 0);
    }

    #[test]
    fn test_commit_pending_returns_true_when_docs_staged() {
        let tmp = tempdir().unwrap();
        let engine = make_test_engine(tmp.path());

        engine.insert_chunks(vec![make_chunk("test content", "coll1")]).unwrap();
        assert_eq!(engine.pending_doc_count(), 1);

        let committed = engine.commit_pending().unwrap();
        assert!(committed, "should return true when docs were committed");
        assert_eq!(engine.pending_doc_count(), 0);
    }

    #[test]
    fn test_commit_pending_resets_pending_count() {
        let tmp = tempdir().unwrap();
        let engine = make_test_engine(tmp.path());

        engine.insert_chunks(vec![make_chunk("a", "c"), make_chunk("b", "c")]).unwrap();
        assert_eq!(engine.pending_doc_count(), 2);

        engine.commit_pending().unwrap();
        assert_eq!(engine.pending_doc_count(), 0, "pending count should reset after commit");
    }

    #[test]
    fn test_search_returns_matching_results() {
        let tmp = tempdir().unwrap();
        let engine = make_test_engine(tmp.path());

        engine.insert_chunks(vec![make_chunk("machine learning is AI", "coll1")]).unwrap();
        engine.commit_pending().unwrap();

        std::thread::sleep(std::time::Duration::from_millis(100));

        let results = engine.search("coll1", "machine", 10).unwrap();
        assert!(!results.is_empty(), "should find document with 'machine'");
    }

    #[test]
    fn test_search_collection_isolation() {
        let tmp = tempdir().unwrap();
        let engine = make_test_engine(tmp.path());

        engine.insert_chunks(vec![make_chunk("hello from collection A", "collA")]).unwrap();
        engine.insert_chunks(vec![make_chunk("hello from collection B", "collB")]).unwrap();
        engine.commit_pending().unwrap();

        std::thread::sleep(std::time::Duration::from_millis(100));

        let results_a = engine.search("collA", "hello", 10).unwrap();
        let results_b = engine.search("collB", "hello", 10).unwrap();

        assert!(!results_a.is_empty(), "collA should have results");
        assert!(!results_b.is_empty(), "collB should have results");
        assert_eq!(
            results_a.len(),
            results_b.len(),
            "both collections should have 1 result each"
        );
    }

    #[test]
    fn test_search_returns_empty_for_nonexistent_query() {
        let tmp = tempdir().unwrap();
        let engine = make_test_engine(tmp.path());

        engine.insert_chunks(vec![make_chunk("specific unique content xyz", "coll1")]).unwrap();
        engine.commit_pending().unwrap();

        std::thread::sleep(std::time::Duration::from_millis(100));

        let results = engine.search("coll1", "nonexistent_term_12345", 10).unwrap();
        assert!(results.is_empty(), "nonexistent query should return empty results");
    }

    #[test]
    fn test_insert_multiple_collections_works() {
        let tmp = tempdir().unwrap();
        let engine = make_test_engine(tmp.path());

        engine
            .insert_chunks(vec![make_chunk("content for coll X", "collX")])
            .unwrap();
        engine
            .insert_chunks(vec![make_chunk("content for coll Y", "collY")])
            .unwrap();
        engine.commit_pending().unwrap();

        std::thread::sleep(std::time::Duration::from_millis(100));

        let results_x = engine.search("collX", "coll", 10).unwrap();
        let results_y = engine.search("collY", "coll", 10).unwrap();

        assert!(!results_x.is_empty());
        assert!(!results_y.is_empty());
    }

    #[test]
    fn test_search_returns_bm25_score() {
        let tmp = tempdir().unwrap();
        let engine = make_test_engine(tmp.path());

        engine.insert_chunks(vec![make_chunk("machine learning is AI", "coll1")]).unwrap();
        engine.commit_pending().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(100));

        let results = engine.search("coll1", "machine", 10).unwrap();
        assert!(!results.is_empty(), "should find document with 'machine'");

        let r = &results[0];
        assert!(
            r.get("bm25_score").is_some(),
            "result must include bm25_score field"
        );
        let score = r.get("bm25_score").unwrap().as_f64().unwrap() as f32;
        assert!(
            score > 0.0,
            "bm25_score for matching term should be positive, got {}",
            score
        );
    }

    #[test]
    fn test_bm25_normalization_zero_maps_to_zero() {
        assert_eq!(normalize_bm25_score(0.0), 0.0);
    }

    #[test]
    fn test_bm25_normalization_maps_to_0_1_range() {
        let scores = [0.0f32, 0.5, 1.0, 5.0, 10.0, 100.0];
        for s in scores {
            let normalized = normalize_bm25_score(s);
            assert!(
                (0.0..=1.0).contains(&normalized),
                "score {} normalized to {} not in [0,1]",
                s,
                normalized
            );
        }
    }

    #[test]
    fn test_bm25_normalization_is_monotonic() {
        let scores = [0.0f32, 1.0, 5.0, 10.0, 100.0];
        for window in scores.windows(2) {
            let n0 = normalize_bm25_score(window[0]);
            let n1 = normalize_bm25_score(window[1]);
            assert!(
                n1 > n0,
                "normalize_bm25_score should be monotonic: {} -> {} vs {} -> {}",
                window[0],
                n0,
                window[1],
                n1
            );
        }
    }
}
