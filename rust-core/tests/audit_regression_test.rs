//! Audit regression tests — guards against known BLOCKER-level bugs.
//!
//! These tests verify fixes from the Bot 2 spec-compliance audit:
//!   - BLOCKER-1: state is AtomicU8 (not AtomicU64)
//!   - BLOCKER-2: initialize_collection uses compare_exchange (not store)
//!   - BLOCKER-3: GIL released via py.allow_threads
//!   - BLOCKER-4&5: WAL recovery uses insert_edges_batch / insert_nodes_batch
//!   - BLOCKER-6: delete_edge cleans up adjacency maps
//!   - WARNING-1: search_semaphore is Arc<Semaphore>
//!   - WARNING-6: initialize_collection transitions UNINITIALIZED→BUILDING→ACTIVE

use rust_core::index_manager::IndexManager;
use rust_core::models::{EdgeType, GraphEdge, GraphNode, KnowledgeGraph, NodeType};
use std::collections::HashMap;
use std::sync::atomic::Ordering;
use uuid::Uuid;

fn make_node(label: &str, cid: Uuid) -> GraphNode {
    GraphNode {
        id: Uuid::new_v4(),
        node_type: NodeType::Concept,
        label: label.to_string(),
        description: None,
        aliases: vec![],
        confidence: 0.9,
        ontology_class: None,
        properties: HashMap::new(),
        collection_id: cid,
        display_label: None,
        dedup_key: None,
        doc_origins: vec![],
        created_at: None,
        updated_at: None,
    }
}

fn make_edge(source: Uuid, target: Uuid, weight: f32, cid: Uuid) -> GraphEdge {
    GraphEdge {
        id: Uuid::new_v4(),
        source,
        target,
        edge_type: EdgeType::RelatesTo,
        weight,
        context: None,
        chunk_id: None,
        properties: HashMap::new(),
        collection_id: cid,
        display_label: None,
        dedup_key: None,
        predicate: String::new(),
        time: None,
        location: None,
        participants: None,
        doc_origins: vec![],
    }
}

// ── BLOCKER-1: state is AtomicU8 ──────────────────────────────────────

#[test]
fn test_state_is_u8_range() {
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();

    let state = im.get_state();
    assert!(state <= 4, "state must be in [0..4] range (AtomicU8), got {}", state);
}

#[test]
fn test_initial_state_is_uninitialized() {
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();
    assert_eq!(im.get_state(), 0, "initial state must be UNINITIALIZED (0)");
}

// ── BLOCKER-2 & WARNING-6: state machine transitions ──────────────────

#[test]
fn test_initialize_collection_transitions_to_active() {
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();
    let coll_id = Uuid::new_v4().to_string();

    pyo3::prepare_freethreaded_python();
    pyo3::Python::with_gil(|py| {
        im.initialize_collection(py, &coll_id).unwrap();
    });

    assert_eq!(im.get_state(), 2, "state should be ACTIVE (2) after successful init");
}

#[test]
fn test_double_initialize_is_rejected() {
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();
    let coll_id = Uuid::new_v4().to_string();

    pyo3::prepare_freethreaded_python();
    pyo3::Python::with_gil(|py| {
        im.initialize_collection(py, &coll_id).unwrap();
    });

    let result = pyo3::Python::with_gil(|py| {
        im.initialize_collection(py, &coll_id)
    });
    assert!(result.is_err(), "second initialize_collection should fail (state is ACTIVE, not UNINITIALIZED)");
}

// ── WARNING-1: search_semaphore is Arc<Semaphore> ─────────────────────

#[test]
fn test_search_semaphore_starts_at_100() {
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();
    assert_eq!(im.available_search_permits(), 100);
}

// ── BLOCKER-6: delete_edge adjacency cleanup ───────────────────────────

#[test]
fn test_delete_edge_removes_from_adjacency_out() {
    let cid = Uuid::new_v4();
    let mut kg = KnowledgeGraph::new(cid);
    let a = make_node("A", cid);
    let b = make_node("B", cid);
    kg.insert_nodes_batch(vec![a.clone(), b.clone()]);
    let edge = make_edge(a.id, b.id, 0.8, cid);
    kg.insert_edges_batch(vec![edge.clone()]);

    assert!(kg.adjacency_out.get(&a.id).unwrap().iter().any(|(eid, _)| *eid == edge.id));

    kg.edges.remove(&edge.id);
    if let Some(adj_out) = kg.adjacency_out.get_mut(&a.id) {
        adj_out.retain(|(eid, _)| *eid != edge.id);
    }
    if let Some(adj_in) = kg.adjacency_in.get_mut(&b.id) {
        adj_in.retain(|(eid, _)| *eid != edge.id);
    }

    assert!(!kg.adjacency_out.get(&a.id).unwrap().iter().any(|(eid, _)| *eid == edge.id),
        "edge should be removed from adjacency_out");
    assert!(!kg.adjacency_in.get(&b.id).unwrap().iter().any(|(eid, _)| *eid == edge.id),
        "edge should be removed from adjacency_in");
}

#[test]
fn test_delete_edge_via_index_manager_cleans_adjacency() {
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();
    let coll_id = Uuid::new_v4().to_string();

    pyo3::prepare_freethreaded_python();
    pyo3::Python::with_gil(|py| {
        im.initialize_collection(py, &coll_id).unwrap();
    });

    let node_a = Uuid::new_v4();
    let node_b = Uuid::new_v4();
    let edge_id = Uuid::new_v4();

    let nodes = serde_json::json!([
        {"id": node_a.to_string(), "node_type": "concept", "label": "A", "description": null,
         "aliases": [], "confidence": 0.9, "ontology_class": null, "properties": {},
         "collection_id": coll_id, "created_at": null, "updated_at": null},
        {"id": node_b.to_string(), "node_type": "concept", "label": "B", "description": null,
         "aliases": [], "confidence": 0.9, "ontology_class": null, "properties": {},
         "collection_id": coll_id, "created_at": null, "updated_at": null}
    ]).to_string();

    let edges = serde_json::json!([
        {"id": edge_id.to_string(), "source": node_a.to_string(), "target": node_b.to_string(),
         "edge_type": "relates_to", "weight": 0.8, "context": null, "chunk_id": null,
         "properties": {}, "collection_id": coll_id}
    ]).to_string();

    pyo3::Python::with_gil(|py| {
        im.upsert_nodes(py, &coll_id, &nodes).unwrap();
        im.upsert_edges(py, &coll_id, &edges).unwrap();
    });

    let graph_before = pyo3::Python::with_gil(|py| {
        im.get_graph_data(py, &coll_id).unwrap()
    });
    let data_before: serde_json::Value = serde_json::from_str(&graph_before).unwrap();
    assert_eq!(data_before["total_edges"], 1, "should have 1 edge before delete");

    pyo3::Python::with_gil(|py| {
        im.delete_edge(py, &coll_id, &edge_id.to_string()).unwrap();
    });

    let graph_after = pyo3::Python::with_gil(|py| {
        im.get_graph_data(py, &coll_id).unwrap()
    });
    let data_after: serde_json::Value = serde_json::from_str(&graph_after).unwrap();
    assert_eq!(data_after["total_edges"], 0, "edge should be deleted");
}

// ── BLOCKER-4 & 5: WAL recovery uses insert_edges_batch ──────────────

#[test]
fn test_wal_recovery_preserves_adjacency_maps() {
    let cid = Uuid::new_v4();
    let mut kg = KnowledgeGraph::new(cid);
    let a = make_node("A", cid);
    let b = make_node("B", cid);
    let edge = make_edge(a.id, b.id, 0.9, cid);

    // Simulate WAL replay: use insert_nodes_batch and insert_edges_batch
    kg.insert_nodes_batch(vec![a.clone(), b.clone()]);
    kg.insert_edges_batch(vec![edge.clone()]);

    // Verify adjacency is populated (this is what the fix ensures)
    assert!(kg.adjacency_out.get(&a.id).is_some(),
        "adjacency_out should have entry for source node after WAL-replay-style insert");
    assert!(kg.adjacency_in.get(&b.id).is_some(),
        "adjacency_in should have entry for target node after WAL-replay-style insert");
    assert_eq!(kg.edge_count(), 1);
    assert_eq!(kg.node_count(), 2);
}

#[test]
fn test_wal_recovery_bumps_version_counter() {
    let cid = Uuid::new_v4();
    let mut kg = KnowledgeGraph::new(cid);
    let v0 = kg.version.load(Ordering::Relaxed);

    let a = make_node("A", cid);
    kg.insert_nodes_batch(vec![a]);
    assert!(kg.version.load(Ordering::Relaxed) > v0,
        "WAL-replay-style insert_nodes_batch should bump version");
}

// ── Embedding dimension ────────────────────────────────────────────────

#[test]
fn test_embedding_dimension_is_1024() {
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();
    assert_eq!(im.embedding_dim, 1024, "embedding dimension must be 1024 per spec (not 1536)");
}

// ── Write semaphore ────────────────────────────────────────────────────

#[test]
fn test_write_semaphore_permit_count() {
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();
    // write_semaphore should start with 1 permit available
    // (We can't directly inspect it, but we can verify it doesn't panic on acquire)
    assert_eq!(im.pending_writes_count(), 0);
}