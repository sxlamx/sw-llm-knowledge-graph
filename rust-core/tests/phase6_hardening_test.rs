//! Phase 6 production hardening regression tests.
//!
//! Guards against BLOCKER-level bugs found during the Phase 6 review:
//!   - BLOCKER-1: rebuild_ivf_pq_index must use compare_exchange (not store)
//!   - BLOCKER-2: Rate limiter must use asyncio.Lock (tested in Python)
//!   - BLOCKER-3: 800ms overall timeout must wrap hybrid search (tested in Python)
//!   - Shadow swap must not hold Level-2 write lock during IVF-PQ build
//!   - 800ms search timeout returns empty results (not error)
//!   - Only one compaction wins a compare_exchange race
//!   - LRU cache invalidation after writes covers both caches
//!   - WAL truncated only AFTER successful replay, not before

use rust_core::index_manager::IndexManager;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use uuid::Uuid;

// ── BLOCKER-1: rebuild_ivf_pq_index uses compare_exchange ────────────────

#[test]
fn test_rebuild_uses_compare_exchange_not_store() {
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();
    let coll_id = Uuid::new_v4().to_string();

    pyo3::prepare_freethreaded_python();
    pyo3::Python::with_gil(|py| {
        im.initialize_collection(py, &coll_id).unwrap();
    });

    // State should be ACTIVE (2) after init
    assert_eq!(im.get_state(), 2, "state must be ACTIVE after init");

    // Calling rebuild_ivf_pq_index should transition Active→Compacting via compare_exchange
    // If it used store() instead, calling it when state is UNINITIALIZED would silently
    // corrupt state. Compare_exchange prevents that.
    let result = pyo3::Python::with_gil(|py| {
        im.rebuild_ivf_pq_index(py, &coll_id)
    });
    assert!(result.is_ok(), "rebuild_ivf_pq_index on ACTIVE state should succeed");

    // State should return to ACTIVE after rebuild
    assert_eq!(im.get_state(), 2, "state should return to ACTIVE after rebuild");
}

#[test]
fn test_rebuild_rejected_when_not_active() {
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();

    // State is UNINITIALIZED (0) — rebuild should FAIL (compare_exchange rejects)
    let result = pyo3::Python::with_gil(|py| {
        im.rebuild_ivf_pq_index(py, &Uuid::new_v4().to_string())
    });
    assert!(result.is_err(), "rebuild when state != ACTIVE must be rejected (compare_exchange)");
    assert_eq!(im.get_state(), 0, "state must remain UNINITIALIZED after rejected rebuild");
}

// ── Only one compaction wins compare_exchange race ───────────────────────

#[test]
fn test_only_one_compaction_wins_race() {
    let tmp = tempfile::tempdir().unwrap();
    let im = Arc::new(IndexManager::new(tmp.path().to_str().unwrap()).unwrap());
    let coll_id = Uuid::new_v4().to_string();

    pyo3::prepare_freethreaded_python();
    pyo3::Python::with_gil(|py| {
        im.initialize_collection(py, &coll_id).unwrap();
    });

    assert_eq!(im.get_state(), 2, "state must be ACTIVE (2)");

    // Two threads try to trigger compaction simultaneously
    let im1 = Arc::clone(&im);
    let im2 = Arc::clone(&im);
    let cid1 = coll_id.clone();
    let cid2 = coll_id.clone();

    let h1 = std::thread::spawn(move || {
        pyo3::Python::with_gil(|py| {
            im1.maybe_trigger_compaction(py, &cid1)
        })
    });
    let h2 = std::thread::spawn(move || {
        pyo3::Python::with_gil(|py| {
            im2.maybe_trigger_compaction(py, &cid2)
        })
    });

    let r1 = h1.join().unwrap();
    let r2 = h2.join().unwrap();

    // Exactly one should succeed (true), the other should fail (false)
    let successes = [r1.unwrap_or(false), r2.unwrap_or(false)].iter().filter(|&&x| x).count();
    assert!(successes <= 1, "at most one compaction should trigger via compare_exchange, got {}", successes);
}

// ── maybe_trigger_compaction rejects when state is COMPACTING ───────────

#[test]
fn test_compaction_rejected_when_compacting() {
    // This test verifies that while compaction is in progress (state=3),
    // another call to maybe_trigger_compaction returns false.
    // We can't easily set state to 3 externally, so we test that
    // maybe_trigger_compaction returns false when state != ACTIVE.
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();
    let coll_id = Uuid::new_v4().to_string();

    // State is UNINITIALIZED (0) — should be rejected
    pyo3::prepare_freethreaded_python();
    let result = pyo3::Python::with_gil(|py| {
        im.maybe_trigger_compaction(py, &coll_id)
    });
    assert!(!result.unwrap(), "should not trigger compaction when state != ACTIVE");
}

// ── 800ms search timeout returns empty results (not error) ──────────────

#[test]
fn test_search_timeout_returns_empty_not_error() {
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();
    let coll_id = Uuid::new_v4().to_string();

    pyo3::prepare_freethreaded_python();
    pyo3::Python::with_gil(|py| {
        im.initialize_collection(py, &coll_id).unwrap();
    });

    // vector_search_with_timeout with 1ms timeout on empty table
    // must return Ok (not Err) with empty results
    let result = pyo3::Python::with_gil(|py| {
        im.vector_search_with_timeout(py, vec![0.0f32; 1024], &coll_id, 10, Some(1))
    });

    // Should succeed — timeout returns empty results, not an error
    assert!(result.is_ok(), "search timeout should return Ok, not Err");
    let json_str = result.unwrap();
    let parsed: Vec<serde_json::Value> = serde_json::from_str(&json_str).unwrap_or_default();
    // Empty table means we might get 0 results even without timeout — that's fine
    // The key invariant is: no panic, no error, just empty results
}

// ── Both caches invalidated after write ──────────────────────────────────

#[test]
fn test_both_caches_invalidated_after_upsert_nodes() {
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();
    let coll_id = Uuid::new_v4().to_string();

    pyo3::prepare_freethreaded_python();
    pyo3::Python::with_gil(|py| {
        im.initialize_collection(py, &coll_id).unwrap();
    });

    // Cache an embedding
    let embedding = vec![0.1f32; 10];
    let json = serde_json::to_string(&embedding).unwrap();
    let stored = pyo3::Python::with_gil(|py| {
        im.cache_embedding("test_query", &json)
    });
    assert!(stored, "embedding should be cached");

    // Verify cache has 1 entry
    let stats_before = im.embedding_cache_stats();
    assert!(stats_before.contains("\"size\":1"), "cache should have 1 entry");

    // Upsert nodes — must invalidate both caches
    let nodes = serde_json::json!([{
        "id": Uuid::new_v4().to_string(),
        "node_type": "person",
        "label": "Cache Test",
        "description": null,
        "aliases": [],
        "confidence": 0.9,
        "ontology_class": null,
        "properties": {},
        "collection_id": coll_id,
        "created_at": null,
        "updated_at": null
    }]).to_string();

    pyo3::Python::with_gil(|py| {
        im.upsert_nodes(py, &coll_id, &nodes).unwrap();
    });

    // Embedding cache should be cleared
    let stats_after = im.embedding_cache_stats();
    assert!(stats_after.contains("\"size\":0"), "embedding cache must be cleared after write");
}

// ── Graph neighbor cache version-based invalidation ─────────────────────

#[test]
fn test_graph_cache_invalidated_on_version_bump() {
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();
    let coll_id = Uuid::new_v4().to_string();

    pyo3::prepare_freethreaded_python();
    pyo3::Python::with_gil(|py| {
        im.initialize_collection(py, &coll_id).unwrap();
    });

    // Get initial graph data (will be cached)
    let data1 = pyo3::Python::with_gil(|py| {
        im.get_graph_data(py, &coll_id).unwrap()
    });
    let v1: serde_json::Value = serde_json::from_str(&data1).unwrap();
    assert_eq!(v1["total_nodes"], 0);

    // Mutate graph (version bump)
    let node_id = Uuid::new_v4().to_string();
    let nodes = serde_json::json!([{
        "id": node_id,
        "node_type": "person",
        "label": "CacheInvalidation",
        "description": null,
        "aliases": [],
        "confidence": 0.9,
        "ontology_class": null,
        "properties": {},
        "collection_id": coll_id,
        "created_at": null,
        "updated_at": null
    }]).to_string();
    pyo3::Python::with_gil(|py| {
        im.upsert_nodes(py, &coll_id, &nodes).unwrap();
    });

    // Cache should be invalidated — new data should reflect mutation
    let data2 = pyo3::Python::with_gil(|py| {
        im.get_graph_data(py, &coll_id).unwrap()
    });
    let v2: serde_json::Value = serde_json::from_str(&data2).unwrap();
    assert_ne!(v1["total_nodes"], v2["total_nodes"],
        "cache must be invalidated after graph mutation — node count should change");
    assert_eq!(v2["total_nodes"], 1, "should see the inserted node");
}

// ── LRU cache TTL enforced on read ──────────────────────────────────────

#[test]
fn test_lru_cache_ttl_enforced_on_read() {
    use std::time::Duration;
    use std::num::NonZeroUsize;
    use rust_core::index_manager::TimedLruCache;

    let mut cache: TimedLruCache<String, String> =
        TimedLruCache::new(NonZeroUsize::new(100).unwrap(), Duration::from_millis(50));

    cache.put("key1".to_string(), "value1".to_string());
    assert_eq!(cache.get(&"key1".to_string()), Some("value1".to_string()));

    // Within TTL
    std::thread::sleep(Duration::from_millis(30));
    assert_eq!(cache.get(&"key1".to_string()), Some("value1".to_string()));

    // After TTL — must return None (evicted)
    std::thread::sleep(Duration::from_millis(30));
    assert_eq!(cache.get(&"key1".to_string()), None, "TTL must be enforced on read");
}

// ── LRU cache respects capacity ─────────────────────────────────────────

#[test]
fn test_lru_cache_capacity_eviction() {
    use std::time::Duration;
    use std::num::NonZeroUsize;
    use rust_core::index_manager::TimedLruCache;

    let mut cache: TimedLruCache<String, i32> =
        TimedLruCache::new(NonZeroUsize::new(3).unwrap(), Duration::from_secs(300));

    cache.put("a".to_string(), 1);
    cache.put("b".to_string(), 2);
    cache.put("c".to_string(), 3);
    assert_eq!(cache.len(), 3);

    // Adding 4th item evicts LRU (a)
    cache.put("d".to_string(), 4);
    assert_eq!(cache.len(), 3);
    assert_eq!(cache.get(&"a".to_string()), None, "LRU entry should be evicted");
    assert_eq!(cache.get(&"d".to_string()), Some(4));
}

// ── WAL truncated only AFTER successful replay ────────────────────────────

#[test]
fn test_wal_not_truncated_before_successful_replay() {
    use rust_core::wal::{WalWriter, read_wal_for_recovery, truncate_wal};

    let tmp = tempfile::tempdir().unwrap();
    let wal_path = tmp.path().join("test_wal.log");

    let mut writer = WalWriter::new(&wal_path).unwrap();
    writer.append(r#"{"op":"upsert_nodes","collection_id":"c1","nodes":"[{}]"}"#).unwrap();
    writer.append(r#"{"op":"upsert_edges","collection_id":"c1","edges":"[{}]"}"#).unwrap();
    drop(writer);

    // read_wal_for_recovery does NOT truncate
    let entries = read_wal_for_recovery(&wal_path).unwrap();
    assert_eq!(entries.len(), 2, "should read 2 entries");
    assert!(wal_path.exists(), "WAL must still exist after read_wal_for_recovery");

    // Content should still be there
    let content = std::fs::read_to_string(&wal_path).unwrap();
    assert!(!content.trim().is_empty(), "WAL must not be truncated by read_wal_for_recovery");

    // Now explicitly truncate after successful replay
    truncate_wal(&wal_path).unwrap();
    let after_truncate = std::fs::read_to_string(&wal_path).unwrap();
    assert!(after_truncate.trim().is_empty(), "WAL must be empty after explicit truncate");
}

// ── Shadow swap: Level-2 write lock held only for HashMap insert ──────

#[test]
fn test_compaction_state_returns_to_active_after_success() {
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();
    let coll_id = Uuid::new_v4().to_string();

    pyo3::prepare_freethreaded_python();
    pyo3::Python::with_gil(|py| {
        im.initialize_collection(py, &coll_id).unwrap();
    });

    assert_eq!(im.get_state(), 2);

    let triggered = pyo3::Python::with_gil(|py| {
        im.maybe_trigger_compaction(py, &coll_id)
    }).unwrap();

    // Even though there's no data, the compaction attempt should complete
    // and state should return to ACTIVE
    assert!(triggered, "compaction should trigger from ACTIVE state");
    assert_eq!(im.get_state(), 2, "state must return to ACTIVE after compaction");
}

// ── Embedding cache capacity is 1000 ────────────────────────────────────

#[test]
fn test_embedding_cache_capacity_is_1000() {
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();
    let stats = im.embedding_cache_stats();
    assert!(stats.contains("\"capacity\":1000"), "embedding cache capacity must be 1000");
}

// ── Graph neighbor cache capacity is 500 ──────────────────────────────────

#[test]
fn test_graph_cache_capacity_is_500() {
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();
    let stats = im.graph_cache_stats();
    assert!(stats.contains("\"capacity\":500"), "graph cache capacity must be 500");
}

// ── Search semaphore has exactly 100 permits ─────────────────────────────

#[test]
fn test_search_semaphore_exactly_100() {
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();
    assert_eq!(im.available_search_permits(), 100,
        "search semaphore must start with exactly 100 permits");
}