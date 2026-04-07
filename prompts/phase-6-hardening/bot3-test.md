# Bot 3 — Test: Phase 6 — Production Hardening

## Your Role

QA engineer writing concurrency stress tests, security tests, and performance benchmarks
for the production hardening layer.

---

## Reference Documents

- `specifications/05-index-manager.md` — CRITICAL: full concurrency model and lock ordering
- `specifications/11-concurrency-performance.md` — CRITICAL: performance targets, semaphore counts
- `specifications/10-auth-security.md` — rate limiting, token revocation

---

## Test File Locations

```
rust-core/
  src/
    index_manager.rs      ← inline unit tests for state machine transitions
  tests/
    concurrency_test.rs   ← 100-concurrent-search stress test, shadow swap under load
  benches/
    index_bench.rs        ← criterion: rebuild time, swap latency

python-api/tests/
  test_rate_limiting.py   ← 429 enforcement, sliding window, /metrics exempt
  test_metrics.py         ← Prometheus format, label safety, concurrent gauge
  test_wal_recovery.py    ← WAL write → crash → recover → verify
  test_graph_pruning.py   ← orphan tombstoning, LanceDB-before-petgraph order
```

---

## Critical Test Cases

### Rust: Concurrent Search (no deadlock, no panic)

```rust
#[tokio::test]
async fn test_100_concurrent_searches_no_deadlock() {
    let engine = build_test_engine_with_data(1000).await;
    let handles: Vec<_> = (0..100).map(|i| {
        let eng = engine.clone();
        tokio::spawn(async move {
            let result = tokio::time::timeout(
                Duration::from_secs(5),
                eng.hybrid_search(query_emb(), &format!("query {}", i), "col-1", 10, None),
            ).await;
            assert!(result.is_ok(), "search {} timed out (possible deadlock)", i);
        })
    }).collect();
    for h in handles { h.await.unwrap(); }
}
```

### Rust: Search Semaphore Limits Concurrency

```rust
#[tokio::test]
async fn test_search_semaphore_is_100() {
    let engine = build_test_engine().await;
    // Inspect internal semaphore available_permits
    assert_eq!(engine.search_semaphore.available_permits(), 100);
}

#[tokio::test]
async fn test_write_semaphore_is_1() {
    let engine = build_test_engine().await;
    assert_eq!(engine.write_semaphore.available_permits(), 1);
}
```

### Rust: 800ms Search Timeout Returns Empty Not Error

```rust
#[tokio::test]
async fn test_search_timeout_returns_empty_vec_not_err() {
    // Engine where all channels artificially sleep 1 second
    let engine = build_slow_engine(Duration::from_secs(1)).await;
    let result = engine.hybrid_search(query_emb(), "query", "col-1", 10, None).await;
    // Must be Ok(vec![]) — not Err
    assert!(result.is_ok());
    assert!(result.unwrap().is_empty());
}
```

### Rust: Shadow Swap Under Concurrent Load

```rust
#[tokio::test]
async fn test_shadow_swap_does_not_block_concurrent_searches() {
    let engine = build_test_engine_with_data(5000).await;

    // Spawn 50 concurrent searches
    let search_handles: Vec<_> = (0..50).map(|_| {
        let eng = engine.clone();
        tokio::spawn(async move {
            let start = Instant::now();
            let _ = eng.vector_search(query_emb(), "col-1", 10).await;
            start.elapsed()
        })
    }).collect();

    // Trigger shadow swap concurrently
    let eng = engine.clone();
    let swap_handle = tokio::spawn(async move {
        eng.rebuild_index("col-1").await.unwrap();
    });

    // Searches must complete within 2× their normal latency (not blocked for rebuild duration)
    for h in search_handles {
        let elapsed = h.await.unwrap();
        assert!(elapsed < Duration::from_secs(2), "search blocked during shadow swap: {:?}", elapsed);
    }
    swap_handle.await.unwrap();
}
```

### Rust: State Machine compare_exchange (No Double-Compact Race)

```rust
#[tokio::test]
async fn test_only_one_compaction_wins_race() {
    let engine = build_test_engine().await;
    // Both tasks attempt to start compaction simultaneously
    let (r1, r2) = tokio::join!(
        engine.start_compaction("col-1"),
        engine.start_compaction("col-1"),
    );
    // Exactly one must succeed, one must fail (state already COMPACTING)
    let successes = [r1.is_ok(), r2.is_ok()].iter().filter(|&&x| x).count();
    assert_eq!(successes, 1, "compare_exchange must prevent double-compaction");
}
```

### Rust: WAL Recovery Roundtrip

```rust
#[tokio::test]
async fn test_wal_recovery_restores_entries() {
    let dir = tempdir().unwrap();
    let wal_path = dir.path().join("wal.bin");

    // Write 5 entries to WAL
    let wal = WalWriter::open(&wal_path).await.unwrap();
    for i in 0..5 {
        wal.append(WalEntry { chunk_id: format!("chunk-{}", i), ..Default::default() }).await.unwrap();
    }
    drop(wal);

    // Recover
    let recovered = recover_wal(&wal_path).await.unwrap();
    assert_eq!(recovered.len(), 5);

    // WAL is NOT truncated until recovery confirmed successful
    assert!(wal_path.exists(), "WAL must not be truncated before recovery success");
}

#[tokio::test]
async fn test_wal_truncated_only_after_successful_recovery() {
    let dir = tempdir().unwrap();
    let wal_path = dir.path().join("wal.bin");
    write_test_wal(&wal_path, 3).await;

    let engine = KnowledgeGraphEngine::new_with_wal(&dir.path()).await.unwrap();
    engine.startup_recovery().await.unwrap();

    // After successful recovery, WAL should be checkpointed (empty or removed)
    let size = tokio::fs::metadata(&wal_path).await.map(|m| m.len()).unwrap_or(0);
    assert_eq!(size, 0, "WAL must be truncated after successful recovery");
}
```

### Rust: Batch Write Buffer (512 rows or 1 second)

```rust
#[tokio::test]
async fn test_batch_flush_on_512_rows() {
    let engine = build_test_engine().await;
    // Insert 512 rows — should auto-flush without waiting 1 second
    for i in 0..512 {
        engine.buffer_chunk(make_chunk(i)).await;
    }
    tokio::time::sleep(Duration::from_millis(50)).await; // small yield
    assert_eq!(engine.pending_buffer_len().await, 0, "buffer must flush at 512 rows");
}

#[tokio::test]
async fn test_batch_flush_on_1_second_timer() {
    let engine = build_test_engine().await;
    engine.buffer_chunk(make_chunk(0)).await; // only 1 row
    tokio::time::sleep(Duration::from_millis(1100)).await;
    assert_eq!(engine.pending_buffer_len().await, 0, "buffer must flush after 1 second");
}

#[tokio::test]
async fn test_buffer_cleared_after_flush() {
    let engine = build_test_engine().await;
    for i in 0..512 { engine.buffer_chunk(make_chunk(i)).await; }
    tokio::time::sleep(Duration::from_millis(100)).await;
    // No stale data in buffer
    assert_eq!(engine.pending_buffer_len().await, 0);
}
```

### Python: Rate Limiting (`test_rate_limiting.py`)

```python
@pytest.mark.asyncio
async def test_rate_limit_429_after_60_requests(client):
    """61st request in 60s window must return 429."""
    for i in range(60):
        resp = await client.get("/api/v1/collections",
            headers={"Authorization": "Bearer test-token"})
        assert resp.status_code != 429, f"rate limited too early at request {i+1}"

    resp = await client.get("/api/v1/collections",
        headers={"Authorization": "Bearer test-token"})
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "60"
    assert resp.headers["X-RateLimit-Limit"] == "60"


@pytest.mark.asyncio
async def test_health_endpoint_not_rate_limited(client):
    """Health endpoint must never return 429 regardless of request count."""
    for _ in range(100):
        resp = await client.get("/health")
        assert resp.status_code != 429


@pytest.mark.asyncio
async def test_metrics_endpoint_not_rate_limited(client):
    """/metrics must never return 429 — Prometheus must be able to scrape freely."""
    for _ in range(100):
        resp = await client.get("/metrics")
        assert resp.status_code != 429


@pytest.mark.asyncio
async def test_rate_limit_resets_after_window(client, freeze_time):
    """After 60s window expires, counter resets and requests succeed again."""
    # Exhaust limit
    for _ in range(60):
        await client.get("/api/v1/collections",
            headers={"Authorization": "Bearer test-token"})
    resp = await client.get("/api/v1/collections",
        headers={"Authorization": "Bearer test-token"})
    assert resp.status_code == 429

    # Advance time by 61 seconds
    freeze_time.tick(61)
    resp = await client.get("/api/v1/collections",
        headers={"Authorization": "Bearer test-token"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_rate_limit_per_user_not_global(client):
    """Two different users each get their own 60/min quota."""
    for _ in range(60):
        await client.get("/api/v1/collections",
            headers={"Authorization": "Bearer user1-token"})

    # user2 must NOT be rate-limited by user1's exhausted quota
    resp = await client.get("/api/v1/collections",
        headers={"Authorization": "Bearer user2-token"})
    assert resp.status_code == 200
```

### Python: Prometheus Metrics (`test_metrics.py`)

```python
@pytest.mark.asyncio
async def test_metrics_endpoint_returns_prometheus_format(client):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert "kg_concurrent_searches" in resp.text
    assert "kg_index_state" in resp.text


@pytest.mark.asyncio
async def test_metrics_endpoint_no_auth_required(client):
    """Prometheus scrape must work without Authorization header."""
    resp = await client.get("/metrics")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_concurrent_searches_gauge_increments(client, authed_headers):
    """kg_concurrent_searches must increment during active searches."""
    # Start a slow search
    search_task = asyncio.create_task(
        client.post("/api/v1/search", json={"query": "test", ...}, headers=authed_headers)
    )
    await asyncio.sleep(0.05)  # let search start

    resp = await client.get("/metrics")
    assert "kg_concurrent_searches 1.0" in resp.text or \
           "kg_concurrent_searches{" in resp.text  # gauge > 0

    await search_task


@pytest.mark.asyncio
async def test_metrics_no_user_ids_in_labels(client):
    """User IDs and document content must never appear in metric labels."""
    await client.post("/api/v1/search",
        json={"query": "sensitive_user_123", "collection_id": "col-1"},
        headers=authed_headers)
    resp = await client.get("/metrics")
    assert "sensitive_user_123" not in resp.text
    assert "user_id" not in resp.text
```

### Python: Graph Pruning (`test_graph_pruning.py`)

```python
@pytest.mark.asyncio
async def test_orphan_tombstoned_not_hard_deleted(kg_service):
    """Orphan pruning must set tombstoned=true, not remove the node."""
    node_id = await kg_service.add_isolated_node("OrphanCorp", "ORGANIZATION")
    await kg_service.run_pruning()

    node = await kg_service.get_node(node_id)
    assert node is not None, "orphan node must not be hard-deleted"
    assert node.get("tombstoned") is True


@pytest.mark.asyncio
async def test_lancedb_updated_before_petgraph(kg_service, monkeypatch):
    """Two-phase write: LanceDB must be updated before in-memory graph."""
    write_order = []

    original_lance = kg_service._lancedb_tombstone
    original_graph = kg_service._graph_tombstone

    async def mock_lance(node_id):
        write_order.append("lancedb")
        return await original_lance(node_id)

    def mock_graph(node_id):
        write_order.append("petgraph")
        return original_graph(node_id)

    monkeypatch.setattr(kg_service, "_lancedb_tombstone", mock_lance)
    monkeypatch.setattr(kg_service, "_graph_tombstone", mock_graph)

    await kg_service.run_pruning()
    assert write_order.index("lancedb") < write_order.index("petgraph"), \
        "LanceDB must be updated before petgraph"
```

### Criterion Benchmarks (`benches/index_bench.rs`)

```rust
fn bench_shadow_swap_latency(c: &mut Criterion) {
    // Measure Level 2 lock hold time during HashMap::insert (the swap itself)
    // Target: < 1ms (spec: ~50μs)
    c.bench_function("shadow_swap_level2_hold", |b| {
        b.iter(|| {
            runtime.block_on(engine.execute_shadow_swap("col-1"))
        })
    });
}

fn bench_concurrent_search_p95(c: &mut Criterion) {
    // 100 concurrent searches — P95 must be < 800ms
    c.bench_function("concurrent_search_p95", |b| {
        b.iter(|| {
            let handles: Vec<_> = (0..100).map(|_| {
                let eng = engine.clone();
                runtime.spawn(async move {
                    eng.hybrid_search(query_emb(), "test query", "col-1", 10, None).await
                })
            }).collect();
            runtime.block_on(async { join_all(handles).await })
        })
    });
}

fn bench_batch_flush_throughput(c: &mut Criterion) {
    // 512 rows per flush — measure flush duration
    // Target: < 500ms per flush batch
    c.bench_function("batch_flush_512_rows", |b| {
        b.iter(|| runtime.block_on(engine.flush_batch(make_batch(512))))
    });
}
```

**Performance targets** (from `specifications/11-concurrency-performance.md`):
- Shadow swap Level 2 hold time: **< 1ms** (typical ~50μs)
- 100 concurrent searches P95: **< 800ms**
- Batch flush (512 rows): **< 500ms**

---

## LRU Cache Safety Tests

```rust
#[tokio::test]
async fn test_embedding_cache_mutex_not_held_during_inference() {
    // Verify the mutex guard is dropped before calling model.encode()
    // Proxy test: 10 concurrent cache misses must not serialize (if mutex were held
    // during inference, they would complete sequentially, taking ~10x single call time)
    let cache = EmbeddingCache::new(1000);
    let start = Instant::now();
    let handles: Vec<_> = (0..10).map(|i| {
        let c = cache.clone();
        tokio::spawn(async move {
            c.get_or_compute(&format!("unique-key-{}", i), || async {
                tokio::time::sleep(Duration::from_millis(50)).await; // simulate inference
                vec![0.1f32; 1024]
            }).await
        })
    }).collect();
    join_all(handles).await;
    let elapsed = start.elapsed();
    // If mutex were held during inference, elapsed ≈ 500ms; parallel ≈ 50ms
    assert!(elapsed < Duration::from_millis(200),
        "embedding cache mutex held during inference (elapsed: {:?})", elapsed);
}

#[tokio::test]
async fn test_neighborhood_cache_invalidated_on_version_bump() {
    let engine = build_test_engine().await;
    // Populate cache
    let _ = engine.get_neighborhood("node-1", "col-1").await;
    assert!(engine.neighborhood_cache_has("node-1", "col-1").await);

    // Bump graph version (simulate write)
    engine.increment_graph_version("col-1").await;

    // Cache entry must be invalidated on next read
    let result = engine.get_neighborhood("node-1", "col-1").await;
    assert!(!engine.neighborhood_cache_served_stale().await,
        "neighborhood cache must recompute after version bump");
}
```

---

## Security Tests

```python
@pytest.mark.asyncio
async def test_revoked_token_rejected(client, db):
    """Tokens in revoked_tokens table must be rejected with 401."""
    token = create_test_token(jti="revoked-jti-123")
    await db.revoke_token("revoked-jti-123")

    resp = await client.get("/api/v1/collections",
        headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_metrics_no_auth_required_confirms_no_sensitive_data(client):
    """Unauthenticated /metrics access is safe — no user data exposed."""
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    # Verify no PII patterns
    import re
    email_pattern = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
    assert not email_pattern.search(resp.text), "email found in metrics output"
```

---

## Coverage Targets

- Concurrency: 100-concurrent-search stress test with no deadlock/panic
- State machine: only one compaction wins compare_exchange race
- WAL: write → recovery → checkpoint in correct order
- Rate limiting: 429 at limit, reset after window, /health and /metrics exempt
- Prometheus: correct format, no PII in labels, unauthenticated access
- LRU cache: Mutex not held during inference, version-based invalidation
- Batch buffer: flush at 512 rows AND at 1 second timer
- Graph pruning: tombstone (not hard delete), LanceDB before petgraph
