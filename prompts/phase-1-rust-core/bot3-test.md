# Bot 3 — Test: Phase 1 — Rust Core Engine

## Your Role

You are a QA engineer writing comprehensive tests for the Phase 1 Rust core engine after
Bot 2 has completed its review. Your tests must cover all acceptance criteria from the spec
and guard against the known failure modes documented in `tasks/LESSONS.md`.

---

## Test Frameworks

- **Unit/Integration**: `cargo test` (standard Rust test harness)
- **Benchmarks**: `criterion` crate — placed in `rust-core/benches/`
- **Coverage**: `cargo tarpaulin` target 80%+

**Requirement**: `crate-type = ["cdylib", "rlib"]` must be in Cargo.toml for tests to compile.

---

## Test File Locations

```
rust-core/
  src/
    index_manager.rs      ← #[cfg(test)] unit tests inline
    models.rs             ← #[cfg(test)] unit tests inline
    ingestion/
      scanner.rs          ← #[cfg(test)] path validation tests
      chunker.rs          ← #[cfg(test)] chunk boundary tests
    graph/
      builder.rs          ← #[cfg(test)] entity resolution tests
      traversal.rs        ← #[cfg(test)] BFS/Dijkstra tests
    wal/
      writer.rs           ← #[cfg(test)] WAL write/read tests
  tests/
    integration_lancedb.rs    ← LanceDB insert + search integration
    integration_graph.rs      ← graph construction + traversal
    index_concurrency.rs      ← 100 concurrent searches stress test (Phase 3 prep)
  benches/
    search_bench.rs           ← criterion: vector search latency
    index_bench.rs            ← criterion: insert throughput
```

---

## Test Categories and Coverage

### 1. Data Models (`src/models.rs`)

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use uuid::Uuid;

    #[test]
    fn test_knowledge_graph_insert_nodes_increments_version() {
        let collection_id = Uuid::new_v4();
        let mut kg = KnowledgeGraph::new(collection_id);
        let v0 = kg.version.load(Ordering::Relaxed);

        kg.insert_nodes_batch(vec![
            GraphNode { id: Uuid::new_v4(), node_type: NodeType::Person, label: "Alice".into(), ..Default::default() }
        ]);

        assert_eq!(kg.version.load(Ordering::Relaxed), v0 + 1);
        assert_eq!(kg.node_count(), 1);
    }

    #[test]
    fn test_insert_edges_updates_both_adjacency_maps() {
        let mut kg = KnowledgeGraph::new(Uuid::new_v4());
        let src = Uuid::new_v4();
        let tgt = Uuid::new_v4();
        let edge_id = Uuid::new_v4();

        kg.insert_edges_batch(vec![GraphEdge {
            id: edge_id, source: src, target: tgt,
            edge_type: EdgeType::RelatesTo, weight: 0.8,
            context: None, chunk_id: None, properties: Default::default(),
        }]);

        assert!(kg.adjacency_out.get(&src).unwrap().iter().any(|(eid, tid)| *eid == edge_id && *tid == tgt));
        assert!(kg.adjacency_in.get(&tgt).unwrap().iter().any(|(eid, sid)| *eid == edge_id && *sid == src));
    }
}
```

### 2. Path Validation (`src/ingestion/scanner.rs`)

```rust
#[test]
fn test_validate_path_blocks_traversal() {
    let allowed = Path::new("/tmp/docs");
    let bad = Path::new("/tmp/docs/../etc/passwd");
    assert!(validate_path(bad, allowed).is_err());
}

#[test]
fn test_validate_path_blocks_exe_extension() {
    // Create temp file with .exe extension
    assert!(validate_path(Path::new("/tmp/docs/tool.exe"), Path::new("/tmp/docs")).is_err());
}

#[test]
fn test_validate_path_allows_pdf() {
    // Should not error for .pdf inside allowed root
}

#[test]
fn test_validate_path_blocks_pem_key() {
    assert!(validate_path(Path::new("/tmp/docs/key.pem"), Path::new("/tmp/docs")).is_err());
}
```

### 3. Chunker (`src/ingestion/chunker.rs`)

```rust
#[test]
fn test_chunker_produces_expected_count() {
    let chunker = Chunker::new(512, 50);
    let doc = ExtractedDocument { raw_text: "word ".repeat(2000), pages: vec![...], ... };
    let chunks = chunker.chunk_document(&doc);
    // 2000 words * ~1.3 tokens/word ≈ 2600 tokens → ~5 chunks at 512 tokens
    assert!(chunks.len() >= 4 && chunks.len() <= 8);
}

#[test]
fn test_chunker_preserves_page_numbers() {
    // Verify each RawChunk.page matches source page
}

#[test]
fn test_chunker_handles_empty_document() {
    let chunker = Chunker::new(512, 50);
    let doc = ExtractedDocument { raw_text: String::new(), pages: vec![], ... };
    assert!(chunker.chunk_document(&doc).is_empty());
}
```

### 4. BLAKE3 Hash

```rust
#[test]
fn test_check_file_changed_returns_true_for_new_file() {
    assert!(check_file_changed(path, None));
}

#[test]
fn test_check_file_changed_returns_false_for_same_hash() {
    // Write file, compute hash, check again
    let hash = compute_blake3_hash(path);
    assert!(!check_file_changed(path, Some(hash.as_str())));
}
```

### 5. WAL Write and Recovery

```rust
#[test]
fn test_wal_write_and_read_roundtrip() {
    let dir = tempdir().unwrap();
    let mut writer = WalWriter::new(dir.path().join("test.wal"));
    let entry = WalEntry {
        timestamp: SystemTime::now(),
        operation: WalOp::InsertNode,
        collection_id: Uuid::new_v4(),
        payload: serde_json::json!({"id": "abc"}),
    };
    writer.append(&entry).unwrap();
    let recovered = WalRecovery::read_entries(dir.path().join("test.wal")).unwrap();
    assert_eq!(recovered.len(), 1);
}
```

### 6. IndexManager State

```rust
#[test]
fn test_index_manager_initial_state_is_uninitialized() {
    let im = IndexManager::new_for_test();
    assert_eq!(im.get_state(), 0); // UNINITIALIZED
}

#[test]
fn test_search_semaphore_has_100_permits() {
    let im = IndexManager::new_for_test();
    assert_eq!(im.available_search_permits(), 100);
}

#[test]
fn test_write_semaphore_has_1_permit() {
    // Acquire write permit, verify second acquire blocks
}
```

### 7. Integration: LanceDB Insert + Search (`tests/integration_lancedb.rs`)

```rust
#[tokio::test]
async fn test_insert_and_search_chunks() {
    let dir = tempdir().unwrap();
    let im = IndexManager::new(dir.path().to_str().unwrap(), "test-collection").unwrap();

    let embedding = vec![0.1f32; 1024];
    let chunk = ChunkRecord {
        id: Uuid::new_v4().to_string(),
        doc_id: Uuid::new_v4().to_string(),
        collection_id: "test-collection".to_string(),
        text: "sample text".to_string(),
        contextual_text: "sample text".to_string(),
        embedding: embedding.clone(),
        position: 0,
        token_count: 2,
        page: 1,
        topics: vec![],
    };

    im.insert_chunks_batch(vec![chunk]).await.unwrap();
    let results = im.vector_search(embedding, "test-collection", 5).await.unwrap();
    assert!(!results.is_empty());
}
```

### 8. Criterion Benchmarks (`benches/search_bench.rs`)

```rust
fn bench_vector_search(c: &mut Criterion) {
    // Setup: insert 10k chunks
    // Measure: p50/p95 of vector_search call
    c.bench_function("vector_search_10k", |b| {
        b.iter(|| im.vector_search(query_embedding.clone(), "bench", 20))
    });
}
```

Target: P50 < 200ms, P95 < 800ms per spec `11-concurrency-performance.md`.

---

## Coverage Targets

- Rust unit tests: ≥ 80% line coverage (`cargo tarpaulin --out Html`)
- All acceptance criteria from `phase-1-rust-core/bot1-build.md` verified with at least one test
- Edge cases: empty inputs, boundary conditions, concurrent access

---

## Mock/Fixture Patterns

- Use `tempfile::tempdir()` for all LanceDB and WAL file paths
- Use fixed UUIDs for deterministic test ordering
- For concurrent tests, use `tokio::join!` or `FuturesUnordered`
- Do NOT hit real network or real GPU in tests — mock embeddings as `vec![0.1f32; 1024]`
