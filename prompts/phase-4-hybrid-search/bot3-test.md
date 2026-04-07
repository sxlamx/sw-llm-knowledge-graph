# Bot 3 — Test: Phase 4 — Hybrid Search

## Your Role

QA engineer writing search correctness tests, performance benchmarks, and degradation tests
for the hybrid search pipeline.

---

## Test File Locations

```
rust-core/
  src/storage/search_engine.rs  ← inline unit tests for score fusion, normalization
  tests/
    search_test.rs               ← hybrid search correctness + partial failure
  benches/
    search_bench.rs              ← criterion: P50/P95 latency

python-api/tests/
  test_search.py                 ← search modes, topic filter, empty results
  test_search_service.py         ← score fusion, channel wiring
```

---

## Critical Test Cases

### Rust: Score Normalization

```rust
#[test]
fn test_bm25_normalization_maps_to_0_1_range() {
    let scores = [0.0f32, 1.0, 5.0, 100.0];
    for s in scores {
        let normalized = normalize_bm25(s);
        assert!(normalized >= 0.0 && normalized <= 1.0, "score {} normalized to {}", s, normalized);
    }
}

#[test]
fn test_bm25_normalization_zero_maps_to_zero() {
    assert_eq!(normalize_bm25(0.0), 0.0);
}
```

### Rust: Score Fusion

```rust
#[test]
fn test_fusion_weights_sum_to_one() {
    let w = SearchWeights::default();
    assert!((w.vector + w.keyword + w.graph - 1.0).abs() < 1e-6);
}

#[test]
fn test_fusion_deduplicates_by_chunk_id() {
    let v = vec![("chunk-1".to_string(), 0.9f32)];
    let k = vec![("chunk-1".to_string(), 0.8f32)];
    let g = vec![];
    let results = fuse_results(v, k, g, SearchWeights::default(), 10);
    // chunk-1 appears only once (merged, not duplicated)
    assert_eq!(results.iter().filter(|r| r.chunk_id == "chunk-1").count(), 1);
}

#[test]
fn test_fusion_returns_correct_top_k() {
    // 10 chunks, limit=5 → returns 5
    let results = fuse_results(big_vector, big_keyword, big_graph, weights, 5);
    assert_eq!(results.len(), 5);
}
```

### Rust: Graceful Degradation (`tests/search_test.rs`)

```rust
#[tokio::test]
async fn test_partial_failure_keyword_timeout_still_returns_vector_results() {
    // Simulate Tantivy being slow (mock with delay > 200ms keyword timeout)
    // hybrid_search should return vector results with graph proximity only
    let results = engine.hybrid_search_with_mock_timeout("keyword", query, col, 10).await;
    // Should not be empty — vector channel succeeded
    assert!(!results.is_empty());
}

#[tokio::test]
async fn test_all_channels_timeout_returns_empty_not_error() {
    // If all 3 channels timeout, return empty Vec (not Err)
    let results = engine.hybrid_search_with_all_timeouts(query, col, 10).await;
    assert!(results.is_empty()); // empty, not Err
}
```

### Python: Search API (`test_search.py`)

```python
@pytest.mark.asyncio
async def test_keyword_search_returns_nonempty(authed_client, ingested_collection):
    """Regression: keyword search was a stub returning []."""
    resp = await authed_client.post("/api/v1/search",
        json={"query": "company founded", "collection_id": ingested_collection, "mode": "keyword"})
    assert resp.status_code == 200
    assert len(resp.json()["results"]) > 0

@pytest.mark.asyncio
async def test_vector_search_returns_nonempty(authed_client, ingested_collection):
    resp = await authed_client.post("/api/v1/search",
        json={"query": "legal concept", "collection_id": ingested_collection, "mode": "vector"})
    assert len(resp.json()["results"]) > 0

@pytest.mark.asyncio
async def test_hybrid_search_returns_results(authed_client, ingested_collection):
    resp = await authed_client.post("/api/v1/search",
        json={"query": "litigation parties", "collection_id": ingested_collection, "mode": "hybrid"})
    assert len(resp.json()["results"]) > 0

@pytest.mark.asyncio
async def test_topic_filter_applied(authed_client, ingested_collection_with_topics):
    resp = await authed_client.post("/api/v1/search",
        json={"query": "contract", "collection_id": cid, "mode": "hybrid", "topics": ["contracts"]})
    # All results should have "contracts" in their topics
    for result in resp.json()["results"]:
        assert "contracts" in result.get("topics", [])
```

### Criterion Benchmarks (`benches/search_bench.rs`)

```rust
fn bench_vector_search(c: &mut Criterion) {
    // Setup: insert 10k chunks with 1024-dim embeddings
    c.bench_function("vector_search_p50", |b| b.iter(|| {
        runtime.block_on(engine.vector_search(query_emb.clone(), col_id, 20))
    }));
}

fn bench_hybrid_search(c: &mut Criterion) {
    c.bench_function("hybrid_search_p95", |b| b.iter(|| {
        runtime.block_on(engine.hybrid_search(query_emb.clone(), "contract", col_id, 20, None))
    }));
}
```

**Performance targets** (from `specifications/11-concurrency-performance.md`):
- P50 < 200ms
- P95 < 800ms
- If benchmarks exceed targets: flag as [WARNING] in bot2 review

---

## Mock Patterns

- Simulate channel timeout using `tokio::time::sleep` + `tokio::select!` in test helpers
- Use deterministic 1024-dim embeddings `[0.1f32; 1024]` for reproducible ANN results
- Pre-populate Tantivy index in test fixture with known texts for BM25 recall testing
