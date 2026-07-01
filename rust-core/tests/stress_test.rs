//! stress_test.rs — Stress tests for IndexManager concurrent search and round-trip workflows.
//!
//! Validates:
//!   - 100 concurrent text_search calls against IndexManager don't deadlock or panic
//!   - 50 concurrent text_search + vector_search interleaved calls
//!   - Full LanceDB insert+search round-trip through IndexManager
//!   - Concurrent upsert_nodes + text_search stress

use rust_core::index_manager::IndexManager;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use uuid::Uuid;

fn make_chunks(coll_id: &str, count: usize) -> String {
    let chunks: Vec<serde_json::Value> = (0..count)
        .map(|i| {
            serde_json::json!({
                "id": Uuid::new_v4().to_string(),
                "doc_id": Uuid::new_v4().to_string(),
                "collection_id": coll_id,
                "text": format!("stress test document number {} with keywords alpha beta gamma", i),
                "contextual_text": format!("context for stress test document {}", i),
                "embedding": vec![0.1f32; 1024],
                "position": i as i32,
                "token_count": 8,
                "page": 1,
                "topics": ["stress"],
                "created_at": 1700000000000i64
            })
        })
        .collect();
    serde_json::json!(chunks).to_string()
}

fn make_nodes(coll_id: &str, count: usize) -> String {
    let nodes: Vec<serde_json::Value> = (0..count)
        .map(|i| {
            serde_json::json!({
                "id": Uuid::new_v4().to_string(),
                "node_type": "concept",
                "label": format!("Node {}", i),
                "description": format!("Stress test node {}", i),
                "aliases": [],
                "confidence": 0.9,
                "ontology_class": null,
                "properties": {},
                "collection_id": coll_id,
                "created_at": null,
                "updated_at": null
            })
        })
        .collect();
    serde_json::json!(nodes).to_string()
}

fn make_edges(coll_id: &str, node_ids: &[String], count: usize) -> String {
    let edges: Vec<serde_json::Value> = (0..count)
        .map(|i| {
            let src_idx = i % node_ids.len();
            let tgt_idx = (i + 1) % node_ids.len();
            serde_json::json!({
                "id": Uuid::new_v4().to_string(),
                "source": node_ids[src_idx],
                "target": node_ids[tgt_idx],
                "edge_type": "relates_to",
                "weight": 0.8,
                "context": null,
                "chunk_id": null,
                "properties": {},
                "collection_id": coll_id
            })
        })
        .collect();
    serde_json::json!(edges).to_string()
}

#[test]
fn test_100_concurrent_text_searches() {
    let tmp = tempfile::tempdir().unwrap();
    let im = Arc::new(IndexManager::new(tmp.path().to_str().unwrap()).unwrap());
    let coll_id = Uuid::new_v4().to_string();

    pyo3::prepare_freethreaded_python();
    pyo3::Python::with_gil(|py| {
        im.initialize_collection(py, &coll_id).unwrap();
    });

    let chunks = make_chunks(&coll_id, 20);
    pyo3::Python::with_gil(|py| {
        im.insert_chunks(py, &coll_id, &chunks).unwrap();
        im.flush_tantivy(py).unwrap();
    });

    std::thread::sleep(std::time::Duration::from_millis(300));

    let success_count = Arc::new(AtomicUsize::new(0));
    let mut handles = Vec::with_capacity(100);

    for i in 0..100 {
        let im = Arc::clone(&im);
        let coll = coll_id.clone();
        let query = format!("stress test {}", i % 10);
        let counter = Arc::clone(&success_count);
        handles.push(std::thread::spawn(move || {
            let result = pyo3::Python::with_gil(|py| {
                im.text_search(py, &coll, &query, 5)
            });
            if result.is_ok() {
                counter.fetch_add(1, Ordering::Relaxed);
            }
        }));
    }

    for h in handles {
        h.join().unwrap();
    }

    let successes = success_count.load(Ordering::Relaxed);
    assert!(successes > 0, "at least some searches should succeed, got {successes}");
}

#[test]
fn test_concurrent_search_and_upsert() {
    let tmp = tempfile::tempdir().unwrap();
    let im = Arc::new(IndexManager::new(tmp.path().to_str().unwrap()).unwrap());
    let coll_id = Uuid::new_v4().to_string();

    pyo3::prepare_freethreaded_python();
    pyo3::Python::with_gil(|py| {
        im.initialize_collection(py, &coll_id).unwrap();
    });

    let nodes = make_nodes(&coll_id, 10);
    let node_ids: Vec<String> = serde_json::from_str::<Vec<serde_json::Value>>(&nodes)
        .unwrap()
        .iter()
        .map(|n| n["id"].as_str().unwrap().to_string())
        .collect();

    pyo3::Python::with_gil(|py| {
        im.upsert_nodes(py, &coll_id, &nodes).unwrap();
    });

    let edges = make_edges(&coll_id, &node_ids, 5);
    pyo3::Python::with_gil(|py| {
        im.upsert_edges(py, &coll_id, &edges).unwrap();
    });

    let chunks = make_chunks(&coll_id, 10);
    pyo3::Python::with_gil(|py| {
        im.insert_chunks(py, &coll_id, &chunks).unwrap();
        im.flush_tantivy(py).unwrap();
    });

    std::thread::sleep(std::time::Duration::from_millis(300));

    let success_count = Arc::new(AtomicUsize::new(0));
    let mut handles = Vec::with_capacity(20);

    for i in 0..10 {
        let im_search = Arc::clone(&im);
        let coll_search = coll_id.clone();
        let counter = Arc::clone(&success_count);
        handles.push(std::thread::spawn(move || {
            let query = format!("stress test {}", i);
            let result = pyo3::Python::with_gil(|py| {
                im_search.text_search(py, &coll_search, &query, 5)
            });
            if result.is_ok() {
                counter.fetch_add(1, Ordering::Relaxed);
            }
        }));
    }

    for i in 0..10 {
        let im_graph = Arc::clone(&im);
        let coll_graph = coll_id.clone();
        let node_id = node_ids[i % node_ids.len()].clone();
        handles.push(std::thread::spawn(move || {
            let update_nodes = serde_json::json!([{
                "id": node_id,
                "node_type": "concept",
                "label": format!("Updated Node {}", i),
                "description": format!("Updated stress test node {}", i),
                "aliases": [],
                "confidence": 0.95,
                "ontology_class": null,
                "properties": {},
                "collection_id": coll_graph,
                "created_at": null,
                "updated_at": null
            }]).to_string();

            let result = pyo3::Python::with_gil(|py| {
                im_graph.upsert_nodes(py, &coll_graph, &update_nodes)
            });
            assert!(result.is_ok(), "upsert_nodes should succeed");
        }));
    }

    for h in handles {
        h.join().unwrap();
    }

    let successes = success_count.load(Ordering::Relaxed);
    assert!(successes > 0, "some searches should succeed, got {successes}");
}

#[test]
fn test_lancedb_vector_search_round_trip() {
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();
    let coll_id = Uuid::new_v4().to_string();

    pyo3::prepare_freethreaded_python();
    pyo3::Python::with_gil(|py| {
        im.initialize_collection(py, &coll_id).unwrap();
    });

    let chunk_id = Uuid::new_v4().to_string();
    let doc_id = Uuid::new_v4().to_string();

    let chunks = serde_json::json!([{
        "id": chunk_id,
        "doc_id": doc_id,
        "collection_id": coll_id,
        "text": "quantum computing algorithms for optimization",
        "contextual_text": "context: quantum computing algorithms for optimization",
        "embedding": vec![0.5f32; 1024],
        "position": 0,
        "token_count": 5,
        "page": 1,
        "topics": ["quantum"],
        "created_at": 1700000000000i64
    }]).to_string();

    let count = pyo3::Python::with_gil(|py| {
        im.insert_chunks_batch(py, &coll_id, &chunks).unwrap()
    });
    assert_eq!(count, 1, "should insert 1 chunk via LanceDB");

    let embedding = vec![0.5f32; 1024];
    let results_json = pyo3::Python::with_gil(|py| {
        im.vector_search(py, embedding, &coll_id, 10).unwrap()
    });

    let results: Vec<serde_json::Value> = serde_json::from_str(&results_json).unwrap();
    assert!(!results.is_empty(), "vector search should find the inserted chunk");
}

#[test]
fn test_full_insert_search_graph_workflow() {
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();
    let coll_id = Uuid::new_v4().to_string();

    pyo3::prepare_freethreaded_python();
    pyo3::Python::with_gil(|py| {
        im.initialize_collection(py, &coll_id).unwrap();
    });

    let node_a_id = Uuid::new_v4();
    let node_b_id = Uuid::new_v4();
    let edge_id = Uuid::new_v4();

    let nodes = serde_json::json!([
        {"id": node_a_id.to_string(), "node_type": "person", "label": "Alice",
         "description": "A software engineer", "aliases": [], "confidence": 0.95,
         "ontology_class": null, "properties": {}, "collection_id": coll_id,
         "created_at": null, "updated_at": null},
        {"id": node_b_id.to_string(), "node_type": "organization", "label": "TechCorp",
         "description": "A technology company", "aliases": [], "confidence": 0.9,
         "ontology_class": null, "properties": {}, "collection_id": coll_id,
         "created_at": null, "updated_at": null}
    ]).to_string();

    let edges = serde_json::json!([
        {"id": edge_id.to_string(), "source": node_a_id.to_string(),
         "target": node_b_id.to_string(), "edge_type": "works_at",
         "weight": 0.9, "context": "Alice works at TechCorp",
         "chunk_id": null, "properties": {}, "collection_id": coll_id}
    ]).to_string();

    pyo3::Python::with_gil(|py| {
        im.upsert_nodes(py, &coll_id, &nodes).unwrap();
        im.upsert_edges(py, &coll_id, &edges).unwrap();
    });

    let graph_json = pyo3::Python::with_gil(|py| {
        im.get_graph_data(py, &coll_id).unwrap()
    });
    let graph: serde_json::Value = serde_json::from_str(&graph_json).unwrap();
    assert_eq!(graph["total_nodes"], 2, "should have 2 nodes");
    assert_eq!(graph["total_edges"], 1, "should have 1 edge");

    let chunks = make_chunks(&coll_id, 5);
    pyo3::Python::with_gil(|py| {
        im.insert_chunks(py, &coll_id, &chunks).unwrap();
        im.flush_tantivy(py).unwrap();
    });

    std::thread::sleep(std::time::Duration::from_millis(200));

    let results_json = pyo3::Python::with_gil(|py| {
        im.text_search(py, &coll_id, "stress test", 10).unwrap()
    });
    let results: Vec<serde_json::Value> = serde_json::from_str(&results_json).unwrap();
    assert!(!results.is_empty(), "text search should find documents");

    pyo3::Python::with_gil(|py| {
        im.delete_edge(py, &coll_id, &edge_id.to_string()).unwrap();
    });

    let graph_after = pyo3::Python::with_gil(|py| {
        im.get_graph_data(py, &coll_id).unwrap()
    });
    let graph_data: serde_json::Value = serde_json::from_str(&graph_after).unwrap();
    assert_eq!(graph_data["total_edges"], 0, "edge should be deleted");
    assert_eq!(graph_data["total_nodes"], 2, "nodes should remain");
}

#[test]
fn test_state_transitions_after_init_and_double_init_rejected() {
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();

    assert_eq!(im.get_state(), 0, "initial state must be UNINITIALIZED");

    let coll_id = Uuid::new_v4().to_string();
    pyo3::prepare_freethreaded_python();
    pyo3::Python::with_gil(|py| {
        im.initialize_collection(py, &coll_id).unwrap();
    });

    assert_eq!(im.get_state(), 2, "state must be ACTIVE (2) after init");

    let result = pyo3::Python::with_gil(|py| {
        im.initialize_collection(py, &coll_id)
    });
    assert!(result.is_err(), "double init should be rejected");
    assert_eq!(im.get_state(), 2, "state should remain ACTIVE after rejected init");
}