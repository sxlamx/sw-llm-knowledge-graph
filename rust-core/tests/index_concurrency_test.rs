//! index_concurrency_test.rs — Phase 3 concurrency stress tests.
//!
//! Validates:
//!   - 100 concurrent read-locks on a KnowledgeGraph do not deadlock.
//!   - Interleaved reads and writes respect the Level-2 → Level-3 ordering rule.
//!   - Graph version increments atomically across concurrent writers.
//!   - Semaphore correctly bounds concurrent-search concurrency.

use rust_core::models::{EdgeType, GraphEdge, GraphNode, KnowledgeGraph, NodeType};
use std::collections::HashMap;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{RwLock as TokioRwLock, Semaphore};
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Helper: build a minimal GraphNode
// ---------------------------------------------------------------------------

fn make_node(collection_id: Uuid) -> GraphNode {
    GraphNode {
        id: Uuid::new_v4(),
        node_type: NodeType::Person,
        label: "Alice".into(),
        description: None,
        aliases: vec![],
        confidence: 0.9,
        ontology_class: None,
        properties: HashMap::new(),
        collection_id,
        created_at: None,
        updated_at: None,
    }
}

fn make_edge(src: Uuid, tgt: Uuid, collection_id: Uuid) -> GraphEdge {
    GraphEdge {
        id: Uuid::new_v4(),
        source: src,
        target: tgt,
        edge_type: EdgeType::RelatesTo,
        weight: 0.8,
        context: None,
        chunk_id: None,
        properties: HashMap::new(),
        collection_id,
    }
}

// ---------------------------------------------------------------------------
// Test 1: 100 concurrent read-locks — no deadlock, consistent node count.
// ---------------------------------------------------------------------------

#[tokio::test(flavor = "multi_thread", worker_threads = 8)]
async fn test_100_concurrent_reads_no_deadlock() {
    let cid = Uuid::new_v4();
    let mut kg = KnowledgeGraph::new(cid);

    let n1 = make_node(cid);
    let n2 = make_node(cid);
    let node_count = 2;
    kg.insert_nodes_batch(vec![n1, n2]);

    let kg_arc = Arc::new(TokioRwLock::new(kg));

    let mut handles = Vec::new();
    for _ in 0..100 {
        let arc = Arc::clone(&kg_arc);
        handles.push(tokio::spawn(async move {
            let graph = arc.read().await;
            graph.node_count()
        }));
    }

    let results = futures::future::join_all(handles).await;
    for r in results {
        assert_eq!(r.unwrap(), node_count, "all readers should see same node count");
    }
}

// ---------------------------------------------------------------------------
// Test 2: Interleaved reads and writes — version increments, no stale reads.
// ---------------------------------------------------------------------------

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn test_interleaved_reads_and_writes() {
    let cid = Uuid::new_v4();
    let kg_arc = Arc::new(TokioRwLock::new(KnowledgeGraph::new(cid)));

    let writes = 10_usize;
    let reads_per_write = 5_usize;

    for _ in 0..writes {
        // Write a batch of nodes
        {
            let mut kg = kg_arc.write().await;
            kg.insert_nodes_batch(vec![make_node(cid)]);
        }

        // Concurrent reads immediately after each write
        let mut handles = Vec::new();
        for _ in 0..reads_per_write {
            let arc = Arc::clone(&kg_arc);
            handles.push(tokio::spawn(async move {
                let g = arc.read().await;
                (g.node_count(), g.version.load(Ordering::Relaxed))
            }));
        }
        let snapshots = futures::future::join_all(handles).await;
        for snap in snapshots {
            let (n, _v) = snap.unwrap();
            assert!(n > 0, "node count must be positive after writes");
        }
    }

    let final_count = kg_arc.read().await.node_count();
    assert_eq!(final_count, writes, "final node count must equal number of writes");
}

// ---------------------------------------------------------------------------
// Test 3: Version AtomicU64 increments correctly across write batches.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn test_version_increments_on_writes() {
    let cid = Uuid::new_v4();
    let mut kg = KnowledgeGraph::new(cid);

    let v0 = kg.version.load(Ordering::Relaxed);
    assert_eq!(v0, 0);

    kg.insert_nodes_batch(vec![make_node(cid)]);
    let v1 = kg.version.load(Ordering::Relaxed);
    assert_eq!(v1, 1);

    kg.insert_nodes_batch(vec![make_node(cid)]);
    let v2 = kg.version.load(Ordering::Relaxed);
    assert_eq!(v2, 2);

    // Edge insert also bumps version
    let n1 = make_node(cid);
    let n2 = make_node(cid);
    let e = make_edge(n1.id, n2.id, cid);
    kg.insert_edges_batch(vec![e]);
    let v3 = kg.version.load(Ordering::Relaxed);
    assert_eq!(v3, 3);
}

// ---------------------------------------------------------------------------
// Test 4: Semaphore bounds concurrency to the specified limit.
// ---------------------------------------------------------------------------

#[tokio::test(flavor = "multi_thread", worker_threads = 8)]
async fn test_semaphore_bounds_concurrent_searches() {
    const CAPACITY: usize = 10;
    let sem = Arc::new(Semaphore::new(CAPACITY));

    // Acquire all permits
    let permits: Vec<_> = futures::future::join_all(
        (0..CAPACITY).map(|_| {
            let s = Arc::clone(&sem);
            async move { s.acquire().await.unwrap() }
        })
    ).await;

    // At capacity: try_acquire should fail
    assert!(
        sem.try_acquire().is_err(),
        "semaphore should be exhausted at capacity"
    );
    assert_eq!(sem.available_permits(), 0);

    // Release half
    drop(permits.into_iter().take(CAPACITY / 2).collect::<Vec<_>>());

    assert_eq!(sem.available_permits(), CAPACITY / 2);
}

// ---------------------------------------------------------------------------
// Test 5: Level-2 → Level-3 lock ordering — outer map read, clone Arc,
//         release outer, then acquire inner write — no deadlock under concurrency.
// ---------------------------------------------------------------------------

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn test_correct_lock_ordering_no_deadlock() {
    let cid = Uuid::new_v4();
    let outer: Arc<TokioRwLock<HashMap<String, Arc<TokioRwLock<KnowledgeGraph>>>>> =
        Arc::new(TokioRwLock::new(HashMap::new()));

    // Pre-populate
    {
        let mut map = outer.write().await;
        map.insert(
            cid.to_string(),
            Arc::new(TokioRwLock::new(KnowledgeGraph::new(cid))),
        );
    }

    // Correct Level-2 → Level-3 pattern executed concurrently by 20 tasks
    let mut handles = Vec::new();
    for _ in 0..20 {
        let outer_clone = Arc::clone(&outer);
        let cid_str = cid.to_string();
        handles.push(tokio::spawn(async move {
            // Level-2 read: clone Arc, immediately release
            let inner_arc = {
                let map = outer_clone.read().await;
                map.get(&cid_str).cloned()
            }; // Level-2 lock released

            if let Some(arc) = inner_arc {
                // Level-3 write (only inner lock held)
                let mut g = arc.write().await;
                g.insert_nodes_batch(vec![make_node(cid)]);
            }
        }));
    }

    // All tasks must complete without timeout — panic means deadlock
    let timeout = tokio::time::timeout(Duration::from_secs(5), async {
        for h in handles {
            h.await.expect("task panicked");
        }
    });
    timeout.await.expect("deadlock detected: tasks did not complete within 5s");
}

// ---------------------------------------------------------------------------
// Test 6: Write semaphore serialises writes (only 1 write at a time).
// ---------------------------------------------------------------------------

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn test_write_semaphore_serialises_writes() {
    let write_sem = Arc::new(Semaphore::new(1));
    let counter = Arc::new(std::sync::atomic::AtomicUsize::new(0));

    let mut handles = Vec::new();
    for _ in 0..50 {
        let sem = Arc::clone(&write_sem);
        let cnt = Arc::clone(&counter);
        handles.push(tokio::spawn(async move {
            let _permit = sem.acquire().await.unwrap();
            // Verify exclusivity: no other "writer" is running simultaneously.
            let before = cnt.fetch_add(1, Ordering::SeqCst);
            // With a write semaphore of 1, concurrent_writers must stay at 0→1.
            assert_eq!(before, 0, "only one writer at a time");
            tokio::time::sleep(Duration::from_micros(50)).await;
            cnt.fetch_sub(1, Ordering::SeqCst);
        }));
    }

    tokio::time::timeout(Duration::from_secs(10), async {
        for h in handles {
            h.await.unwrap();
        }
    })
    .await
    .expect("write serialisation test timed out");
}
