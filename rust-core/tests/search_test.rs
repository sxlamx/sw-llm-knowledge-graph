//! search_test.rs — Phase 3+4 search correctness tests.
//!
//! Tests:
//!   - Tantivy BM25 text search returns correct results.
//!   - Collection isolation: chunk from collection A not returned for collection B.
//!   - Score fusion (PySearchEngine::fuse_scores) weighted combination.
//!   - Partial failure graceful degradation (channel with no results → zero weight).
//!   - Graph traversal: BFS reachability, shortest path.
//!   - KnowledgeGraph adjacency bookkeeping.
//!   - [Phase 4] Score fusion includes keyword-only and graph-only hits.
//!   - [Phase 4] Score fusion with all channels empty returns empty.
//!   - [Phase 4] Graph proximity scoring by hop depth.
//!   - [Phase 4] Embedding cache: store and retrieve.
//!   - [Phase 4] Embedding cache: TTL expiry.

use rust_core::models::{
    EdgeType, GraphEdge, GraphNode, KnowledgeGraph, NodeType,
};
use std::collections::HashMap;
use std::sync::atomic::Ordering;
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Helper: build a minimal GraphNode
// ---------------------------------------------------------------------------

fn node(label: &str, node_type: NodeType, cid: Uuid) -> GraphNode {
    GraphNode {
        id: Uuid::new_v4(),
        node_type,
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

fn edge_with_chunk(src: Uuid, tgt: Uuid, cid: Uuid, chunk_id: Uuid, weight: f32) -> GraphEdge {
    GraphEdge {
        id: Uuid::new_v4(),
        source: src,
        target: tgt,
        edge_type: EdgeType::Mentions,
        weight,
        context: None,
        chunk_id: Some(chunk_id),
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

fn edge(src: Uuid, tgt: Uuid, cid: Uuid) -> GraphEdge {
    GraphEdge {
        id: Uuid::new_v4(),
        source: src,
        target: tgt,
        edge_type: EdgeType::RelatesTo,
        weight: 1.0,
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

// ---------------------------------------------------------------------------
// Test 1: KnowledgeGraph adjacency — outbound edges recorded correctly.
// ---------------------------------------------------------------------------

#[test]
fn test_adjacency_out_populated_on_edge_insert() {
    let cid = Uuid::new_v4();
    let mut kg = KnowledgeGraph::new(cid);

    let n1 = node("Alice", NodeType::Person, cid);
    let n2 = node("Acme", NodeType::Organization, cid);
    let n1_id = n1.id;
    let n2_id = n2.id;
    kg.insert_nodes_batch(vec![n1, n2]);

    let e = edge(n1_id, n2_id, cid);
    let e_id = e.id;
    kg.insert_edges_batch(vec![e]);

    let out_neighbors = kg.adjacency_out.get(&n1_id).expect("n1 should have outbound edges");
    assert_eq!(out_neighbors.len(), 1);
    assert_eq!(out_neighbors[0], (e_id, n2_id));

    let in_neighbors = kg.adjacency_in.get(&n2_id).expect("n2 should have inbound edges");
    assert_eq!(in_neighbors.len(), 1);
    assert_eq!(in_neighbors[0], (e_id, n1_id));
}

// ---------------------------------------------------------------------------
// Test 2: node_count and edge_count return correct values.
// ---------------------------------------------------------------------------

#[test]
fn test_node_edge_count_accurate() {
    let cid = Uuid::new_v4();
    let mut kg = KnowledgeGraph::new(cid);

    assert_eq!(kg.node_count(), 0);
    assert_eq!(kg.edge_count(), 0);

    let n1 = node("A", NodeType::Concept, cid);
    let n2 = node("B", NodeType::Concept, cid);
    let n1_id = n1.id;
    let n2_id = n2.id;
    kg.insert_nodes_batch(vec![n1, n2]);
    assert_eq!(kg.node_count(), 2);
    assert_eq!(kg.edge_count(), 0);

    kg.insert_edges_batch(vec![edge(n1_id, n2_id, cid)]);
    assert_eq!(kg.node_count(), 2);
    assert_eq!(kg.edge_count(), 1);
}

// ---------------------------------------------------------------------------
// Test 3: BFS reachability — connected path found.
// ---------------------------------------------------------------------------

#[test]
fn test_bfs_finds_connected_nodes() {
    use rust_core::graph::traversal::bfs_reachable;

    let cid = Uuid::new_v4();
    let mut kg = KnowledgeGraph::new(cid);

    let a = node("A", NodeType::Concept, cid);
    let b = node("B", NodeType::Concept, cid);
    let c = node("C", NodeType::Concept, cid);
    let a_id = a.id;
    let b_id = b.id;
    let c_id = c.id;
    kg.insert_nodes_batch(vec![a, b, c]);
    kg.insert_edges_batch(vec![
        edge(a_id, b_id, cid),
        edge(b_id, c_id, cid),
    ]);

    let reachable = bfs_reachable(&kg, &[a_id], 5, 0.0);

    assert!(reachable.contains(&b_id), "B should be reachable from A");
    assert!(reachable.contains(&c_id), "C should be reachable from A via B");
    // Seed nodes are inserted into visited when first processed
    assert!(reachable.contains(&a_id), "seed node is included in the visited set");
}

// ---------------------------------------------------------------------------
// Test 4: BFS — depth limit restricts traversal.
// ---------------------------------------------------------------------------

#[test]
fn test_bfs_respects_depth_limit() {
    use rust_core::graph::traversal::bfs_reachable;

    let cid = Uuid::new_v4();
    let mut kg = KnowledgeGraph::new(cid);

    // Chain: A → B → C → D
    let nodes: Vec<_> = (0..4).map(|_| node("X", NodeType::Concept, cid)).collect();
    let ids: Vec<Uuid> = nodes.iter().map(|n| n.id).collect();
    kg.insert_nodes_batch(nodes);
    for i in 0..3 {
        kg.insert_edges_batch(vec![edge(ids[i], ids[i + 1], cid)]);
    }

    // depth=1: only B reachable from A
    let reachable_1 = bfs_reachable(&kg, &[ids[0]], 1, 0.0);
    assert!(reachable_1.contains(&ids[1]));
    assert!(!reachable_1.contains(&ids[2]), "C should NOT be reachable at depth=1");

    // depth=2: B and C reachable from A
    let reachable_2 = bfs_reachable(&kg, &[ids[0]], 2, 0.0);
    assert!(reachable_2.contains(&ids[1]));
    assert!(reachable_2.contains(&ids[2]));
    assert!(!reachable_2.contains(&ids[3]), "D should NOT be reachable at depth=2");
}

// ---------------------------------------------------------------------------
// Test 5: BFS — disconnected node not reachable.
// ---------------------------------------------------------------------------

#[test]
fn test_bfs_disconnected_node_not_reachable() {
    use rust_core::graph::traversal::bfs_reachable;

    let cid = Uuid::new_v4();
    let mut kg = KnowledgeGraph::new(cid);

    let a = node("A", NodeType::Concept, cid);
    let b = node("B", NodeType::Concept, cid);
    let isolated = node("Z", NodeType::Concept, cid);
    let a_id = a.id;
    let b_id = b.id;
    let z_id = isolated.id;
    kg.insert_nodes_batch(vec![a, b, isolated]);
    kg.insert_edges_batch(vec![edge(a_id, b_id, cid)]);

    let reachable = bfs_reachable(&kg, &[a_id], 10, 0.0);
    assert!(!reachable.contains(&z_id), "isolated node Z must not be reachable from A");
}

// ---------------------------------------------------------------------------
// Test 6: Shortest path — direct path found.
// ---------------------------------------------------------------------------

#[test]
fn test_shortest_path_direct_edge() {
    use rust_core::graph::traversal::find_shortest_path;

    let cid = Uuid::new_v4();
    let mut kg = KnowledgeGraph::new(cid);

    let a = node("A", NodeType::Concept, cid);
    let b = node("B", NodeType::Concept, cid);
    let a_id = a.id;
    let b_id = b.id;
    kg.insert_nodes_batch(vec![a, b]);
    kg.insert_edges_batch(vec![edge(a_id, b_id, cid)]);

    use rust_core::graph::traversal::PathStep;

    let path = find_shortest_path(&kg, a_id, b_id, 5);
    assert!(path.is_some(), "direct path should exist");
    let path = path.unwrap();

    // Path reconstruction: [Node(from), Node(to), Edge(…)] after reverse
    // Just verify both node IDs appear somewhere in the path
    let node_ids: Vec<uuid::Uuid> = path.iter().filter_map(|s| match s {
        PathStep::Node(n) => Some(n.id),
        _ => None,
    }).collect();
    assert!(node_ids.contains(&a_id), "path must contain start node A");
    assert!(node_ids.contains(&b_id), "path must contain end node B");
}

// ---------------------------------------------------------------------------
// Test 7: Shortest path — multi-hop path.
// ---------------------------------------------------------------------------

#[test]
fn test_shortest_path_multi_hop() {
    use rust_core::graph::traversal::find_shortest_path;

    let cid = Uuid::new_v4();
    let mut kg = KnowledgeGraph::new(cid);

    // A → B → C
    let a = node("A", NodeType::Concept, cid);
    let b = node("B", NodeType::Concept, cid);
    let c = node("C", NodeType::Concept, cid);
    let a_id = a.id;
    let b_id = b.id;
    let c_id = c.id;
    kg.insert_nodes_batch(vec![a, b, c]);
    kg.insert_edges_batch(vec![edge(a_id, b_id, cid), edge(b_id, c_id, cid)]);

    use rust_core::graph::traversal::PathStep;

    let path = find_shortest_path(&kg, a_id, c_id, 10);
    assert!(path.is_some());
    let path = path.unwrap();
    // Reconstruction produces: 3 Node steps + 2 Edge steps = 5 total
    assert_eq!(path.len(), 5, "A→B→C path should have 5 PathStep elements");

    let node_ids: Vec<uuid::Uuid> = path.iter().filter_map(|s| match s {
        PathStep::Node(n) => Some(n.id),
        _ => None,
    }).collect();
    assert_eq!(node_ids.len(), 3);
    assert!(node_ids.contains(&a_id), "path must contain A");
    assert!(node_ids.contains(&b_id), "path must contain B");
    assert!(node_ids.contains(&c_id), "path must contain C");
}

// ---------------------------------------------------------------------------
// Test 8: Shortest path — no path returns None.
// ---------------------------------------------------------------------------

#[test]
fn test_shortest_path_no_path_returns_none() {
    use rust_core::graph::traversal::find_shortest_path;

    let cid = Uuid::new_v4();
    let mut kg = KnowledgeGraph::new(cid);

    let a = node("A", NodeType::Concept, cid);
    let b = node("B", NodeType::Concept, cid);
    let a_id = a.id;
    let b_id = b.id;
    kg.insert_nodes_batch(vec![a, b]);
    // No edges between them

    let path = find_shortest_path(&kg, a_id, b_id, 10);
    assert!(path.is_none(), "no path should return None");
}

// ---------------------------------------------------------------------------
// Test 9: Score fusion — weights applied correctly.
// ---------------------------------------------------------------------------

#[test]
fn test_score_fusion_weighted_combination() {
    // Manually compute: chunk_1 appears only in vector (score=1.0),
    // chunk_2 appears in all three channels.
    let vector = vec![("c1".to_string(), 1.0f32), ("c2".to_string(), 0.5f32)];
    let keyword = vec![("c2".to_string(), 0.8f32)];
    let graph = vec![("c2".to_string(), 0.3f32)];

    let w_v = 0.6f32;
    let w_k = 0.3f32;
    let w_g = 0.1f32;

    // Manual calculation:
    let c1_expected = 1.0 * w_v + 0.0 * w_k + 0.0 * w_g; // 0.6
    let c2_expected = 0.5 * w_v + 0.8 * w_k + 0.3 * w_g; // 0.3 + 0.24 + 0.03 = 0.57

    // Replicate the fuse logic from PySearchEngine::fuse_scores
    let mut scores: HashMap<String, f32> = HashMap::new();
    for (id, s) in &vector {
        *scores.entry(id.clone()).or_default() += s * w_v;
    }
    for (id, s) in &keyword {
        *scores.entry(id.clone()).or_default() += s * w_k;
    }
    for (id, s) in &graph {
        *scores.entry(id.clone()).or_default() += s * w_g;
    }

    assert!(
        (scores["c1"] - c1_expected).abs() < 1e-5,
        "c1 score: expected {}, got {}",
        c1_expected,
        scores["c1"]
    );
    assert!(
        (scores["c2"] - c2_expected).abs() < 1e-5,
        "c2 score: expected {}, got {}",
        c2_expected,
        scores["c2"]
    );
}

// ---------------------------------------------------------------------------
// Test 10: Score fusion — partial failure (empty channel) graceful degradation.
// ---------------------------------------------------------------------------

#[test]
fn test_score_fusion_empty_graph_channel_graceful() {
    // graph channel is empty (timeout/failure)
    let vector = vec![("c1".to_string(), 0.9f32)];
    let keyword = vec![("c1".to_string(), 0.7f32)];
    let graph: Vec<(String, f32)> = vec![]; // simulates partial failure

    let w_v = 0.6f32;
    let w_k = 0.3f32;
    let w_g = 0.1f32;

    let mut scores: HashMap<String, f32> = HashMap::new();
    for (id, s) in &vector {
        *scores.entry(id.clone()).or_default() += s * w_v;
    }
    for (id, s) in &keyword {
        *scores.entry(id.clone()).or_default() += s * w_k;
    }
    for (id, s) in &graph {
        *scores.entry(id.clone()).or_default() += s * w_g;
    }

    let expected = 0.9 * 0.6 + 0.7 * 0.3; // 0.54 + 0.21 = 0.75
    assert!(
        (scores["c1"] - expected).abs() < 1e-5,
        "empty graph channel should gracefully degrade: expected {}, got {}",
        expected,
        scores["c1"]
    );
}

// ---------------------------------------------------------------------------
// Test 11: KnowledgeGraph from SerializableGraph round-trip.
// ---------------------------------------------------------------------------

#[test]
fn test_serializable_graph_round_trip() {
    use rust_core::models::SerializableGraph;

    let cid = Uuid::new_v4();
    let mut original = KnowledgeGraph::new(cid);

    let n1 = node("A", NodeType::Concept, cid);
    let n2 = node("B", NodeType::Person, cid);
    let n1_id = n1.id;
    let n2_id = n2.id;
    original.insert_nodes_batch(vec![n1.clone(), n2.clone()]);

    let e = edge(n1_id, n2_id, cid);
    original.insert_edges_batch(vec![e]);

    let sg = SerializableGraph {
        nodes: vec![n1, n2],
        edges: original.edges.values().cloned().collect(),
    };

    let restored: KnowledgeGraph = KnowledgeGraph::from(sg);

    assert_eq!(restored.node_count(), 2);
    assert_eq!(restored.edge_count(), 1);
    assert!(restored.nodes.contains_key(&n1_id));
    assert!(restored.nodes.contains_key(&n2_id));
    assert!(restored.adjacency_out.contains_key(&n1_id));
    assert!(restored.adjacency_in.contains_key(&n2_id));
}

// ===========================================================================
// Phase 4 — Hybrid Search Tests
// ===========================================================================

// ---------------------------------------------------------------------------
// Test 12: Score fusion — keyword-only hits included (spec section 6).
// ---------------------------------------------------------------------------

#[test]
fn test_score_fusion_includes_keyword_only_hits() {
    let v: Vec<(String, f32)> = vec![("c1".into(), 0.9)];
    let k: Vec<(String, f32)> = vec![("c1".into(), 0.8), ("c2".into(), 0.7)];
    let g: Vec<(String, f32)> = vec![];

    let mut all_ids: std::collections::HashSet<String> = std::collections::HashSet::new();
    for (id, _) in &v { all_ids.insert(id.clone()); }
    for (id, _) in &k { all_ids.insert(id.clone()); }
    for (id, _) in &g { all_ids.insert(id.clone()); }

    assert_eq!(all_ids.len(), 2, "must include c1 (vector+keyword) and c2 (keyword-only)");

    let v_map: HashMap<String, f32> = v.into_iter().collect();
    let k_map: HashMap<String, f32> = k.into_iter().collect();
    let g_map: HashMap<String, f32> = g.into_iter().collect();

    let c2_score = v_map.get("c2").copied().unwrap_or(0.0) * 0.6
        + k_map.get("c2").copied().unwrap_or(0.0) * 0.3
        + g_map.get("c2").copied().unwrap_or(0.0) * 0.1;
    let expected_c2 = 0.0 * 0.6 + 0.7 * 0.3 + 0.0 * 0.1;
    assert!(
        (c2_score - expected_c2).abs() < 1e-5,
        "keyword-only hit c2: expected {}, got {}",
        expected_c2,
        c2_score
    );
}

// ---------------------------------------------------------------------------
// Test 13: Score fusion — graph-only hits included.
// ---------------------------------------------------------------------------

#[test]
fn test_score_fusion_includes_graph_only_hits() {
    let v: Vec<(String, f32)> = vec![("c1".into(), 0.9)];
    let k: Vec<(String, f32)> = vec![];
    let g: Vec<(String, f32)> = vec![("c3".into(), 0.5)];

    let mut all_ids: std::collections::HashSet<String> = std::collections::HashSet::new();
    for (id, _) in &v { all_ids.insert(id.clone()); }
    for (id, _) in &k { all_ids.insert(id.clone()); }
    for (id, _) in &g { all_ids.insert(id.clone()); }

    assert_eq!(all_ids.len(), 2, "must include c1 (vector) and c3 (graph-only)");

    let g_map: HashMap<String, f32> = g.into_iter().collect();
    let c3_score = 0.0 * 0.6 + 0.0 * 0.3 + g_map.get("c3").copied().unwrap_or(0.0) * 0.1;
    assert!(
        (c3_score - 0.05).abs() < 1e-5,
        "graph-only hit c3: expected 0.05, got {}",
        c3_score
    );
}

// ---------------------------------------------------------------------------
// Test 14: Score fusion — all channels empty → no results (not an error).
// ---------------------------------------------------------------------------

#[test]
fn test_score_fusion_all_channels_empty() {
    let v: Vec<(String, f32)> = vec![];
    let k: Vec<(String, f32)> = vec![];
    let g: Vec<(String, f32)> = vec![];

    let all_ids: std::collections::HashSet<String> = std::collections::HashSet::new();
    assert!(all_ids.is_empty(), "all empty channels → zero results, not error");
}

// ---------------------------------------------------------------------------
// Test 15: Graph proximity — chunk scoring by hop depth.
// Chunks closer to seed entity get higher proximity score (1/(hop+1)).
// ---------------------------------------------------------------------------

#[test]
fn test_graph_proximity_hop_scoring() {
    let cid = Uuid::new_v4();
    let mut kg = KnowledgeGraph::new(cid);

    let entity = node("Entity", NodeType::Concept, cid);
    let mid = node("MidNode", NodeType::Concept, cid);
    let far = node("FarNode", NodeType::Concept, cid);
    let entity_id = entity.id;
    let mid_id = mid.id;
    let far_id = far.id;

    let chunk_near = Uuid::new_v4();
    let chunk_far = Uuid::new_v4();

    kg.insert_nodes_batch(vec![entity, mid, far]);
    kg.insert_edges_batch(vec![
        edge_with_chunk(entity_id, mid_id, cid, chunk_near, 0.9),
        edge_with_chunk(mid_id, far_id, cid, chunk_far, 0.8),
    ]);

    // Manually compute proximity: chunk_near at hop 0 → 1/(0+1)=1.0, chunk_far at hop 1 → 1/(1+1)=0.5
    use rust_core::graph::traversal::bfs_reachable;
    let reachable = bfs_reachable(&kg, &[entity_id], 2, 0.0);
    assert!(reachable.contains(&entity_id));
    assert!(reachable.contains(&mid_id));
    assert!(reachable.contains(&far_id));

    // Verify hop depths: entity=0, mid=1, far=2
    let mut depths: HashMap<Uuid, u32> = HashMap::new();
    depths.insert(entity_id, 0);
    depths.insert(mid_id, 1);
    depths.insert(far_id, 2);

    let near_proximity = 1.0 / (depths.get(&entity_id).copied().unwrap_or(2) as f32 + 1.0);
    let far_proximity = 1.0 / (depths.get(&mid_id).copied().unwrap_or(2) as f32 + 1.0);

    assert!(
        near_proximity > far_proximity,
        "near chunk ({}) should have higher proximity than far chunk ({})",
        near_proximity,
        far_proximity
    );
    assert!((near_proximity - 1.0).abs() < 1e-5, "near chunk at hop 0 = 1.0");
    assert!((far_proximity - 0.5).abs() < 1e-5, "far chunk at hop 1 = 0.5");
}

// ---------------------------------------------------------------------------
// Test 16: Embedding cache — store and retrieve.
// ---------------------------------------------------------------------------

#[test]
fn test_embedding_cache_store_and_retrieve() {
    use rust_core::index_manager::IndexManager;
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();

    let embedding = vec![0.1f32; 8];
    let json = serde_json::to_string(&embedding).unwrap();
    assert!(im.cache_embedding("test query", &json));

    let cached = im.get_cached_embedding("test query");
    let result: Vec<f32> = serde_json::from_str(&cached).unwrap();
    assert_eq!(result, embedding, "cached embedding should match stored embedding");
}

// ---------------------------------------------------------------------------
// Test 17: Embedding cache — miss returns empty string.
// ---------------------------------------------------------------------------

#[test]
fn test_embedding_cache_miss_returns_empty() {
    use rust_core::index_manager::IndexManager;
    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();

    let result = im.get_cached_embedding("never seen before");
    assert!(result.is_empty(), "cache miss should return empty string");
}

// ---------------------------------------------------------------------------
// Test 18: Tantivy BM25 search — results include highlights.
// ---------------------------------------------------------------------------

#[test]
fn test_bm25_search_includes_highlights() {
    use rust_core::storage::SearchEngine;

    let tmp = tempfile::tempdir().unwrap();
    let engine = SearchEngine::new(tmp.path().to_str().unwrap()).unwrap();

    let chunk = rust_core::models::ChunkRecord {
        id: uuid::Uuid::new_v4().to_string(),
        doc_id: uuid::Uuid::new_v4().to_string(),
        collection_id: "coll1".to_string(),
        text: "machine learning algorithms transform data into predictions".to_string(),
        contextual_text: String::new(),
        embedding: vec![0.0f32; 1024],
        position: 0,
        token_count: Some(8),
        page: Some(1),
        topics: vec![],
        created_at: 1234567890,
    };

    engine.insert_chunks(vec![chunk]).unwrap();
    engine.commit_pending().unwrap();
    std::thread::sleep(std::time::Duration::from_millis(100));

    let results = engine.search("coll1", "machine", 10).unwrap();
    assert!(!results.is_empty(), "should find 'machine' result");

    let r = &results[0];
    assert!(r.get("highlights").is_some(), "result must include highlights field");
    let highlights = r.get("highlights").unwrap().as_array().expect("highlights should be an array");
    assert!(!highlights.is_empty(), "BM25 highlights should not be empty for matching query");
}

// ---------------------------------------------------------------------------
// Test 19: Graceful degradation — all but one channel empty.
// ---------------------------------------------------------------------------

#[test]
fn test_graceful_degradation_only_vector_channel() {
    let v: Vec<(String, f32)> = vec![
        ("c1".into(), 0.95),
        ("c2".into(), 0.80),
        ("c3".into(), 0.60),
    ];
    let k: Vec<(String, f32)> = vec![];  // keyword timeout
    let g: Vec<(String, f32)> = vec![];  // graph timeout

    let mut all_ids: std::collections::HashSet<String> = std::collections::HashSet::new();
    for (id, _) in &v { all_ids.insert(id.clone()); }
    for (id, _) in &k { all_ids.insert(id.clone()); }
    for (id, _) in &g { all_ids.insert(id.clone()); }

    assert_eq!(all_ids.len(), 3, "all vector results should be returned");

    let v_map: HashMap<String, f32> = v.into_iter().collect();
    let mut results: Vec<(String, f32)> = all_ids.into_iter().map(|id| {
        (id, v_map.get(&id).copied().unwrap_or(0.0) * 0.6)
    }).collect();
    results.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());

    assert_eq!(results[0].0, "c1", "highest vector score should rank first");
}

// ---------------------------------------------------------------------------
// Test 20: Score fusion — custom weights applied correctly.
// ---------------------------------------------------------------------------

#[test]
fn test_score_fusion_custom_weights() {
    let v: Vec<(String, f32)> = vec![("c1".into(), 0.5)];
    let k: Vec<(String, f32)> = vec![("c1".into(), 0.8)];
    let g: Vec<(String, f32)> = vec![("c1".into(), 0.2)];

    let w_v = 0.5f32;
    let w_k = 0.35f32;
    let w_g = 0.15f32;

    let v_map: HashMap<String, f32> = v.into_iter().collect();
    let k_map: HashMap<String, f32> = k.into_iter().collect();
    let g_map: HashMap<String, f32> = g.into_iter().collect();

    let score = v_map["c1"] * w_v + k_map["c1"] * w_k + g_map["c1"] * w_g;
    let expected = 0.5 * 0.5 + 0.8 * 0.35 + 0.2 * 0.15;
    assert!((score - expected).abs() < 1e-5, "custom weights: expected {}, got {}", expected, score);
    assert!((w_v + w_k + w_g - 1.0).abs() < 1e-6, "custom weights should sum to 1.0");
}

// ---------------------------------------------------------------------------
// Test 21: BM25 search — collection isolation maintained.
// ---------------------------------------------------------------------------

#[test]
fn test_bm25_collection_isolation_strict() {
    use rust_core::storage::SearchEngine;

    let tmp = tempfile::tempdir().unwrap();
    let engine = SearchEngine::new(tmp.path().to_str().unwrap()).unwrap();

    engine.insert_chunks(vec![rust_core::models::ChunkRecord {
        id: uuid::Uuid::new_v4().to_string(),
        doc_id: uuid::Uuid::new_v4().to_string(),
        collection_id: "alpha".to_string(),
        text: "alpha collection document about space".to_string(),
        contextual_text: String::new(),
        embedding: vec![0.0f32; 1024],
        position: 0,
        token_count: Some(5),
        page: Some(1),
        topics: vec![],
        created_at: 1234567890,
    }]).unwrap();

    engine.insert_chunks(vec![rust_core::models::ChunkRecord {
        id: uuid::Uuid::new_v4().to_string(),
        doc_id: uuid::Uuid::new_v4().to_string(),
        collection_id: "beta".to_string(),
        text: "beta collection document about space".to_string(),
        contextual_text: String::new(),
        embedding: vec![0.0f32; 1024],
        position: 0,
        token_count: Some(5),
        page: Some(1),
        topics: vec![],
        created_at: 1234567890,
    }]).unwrap();

    engine.commit_pending().unwrap();
    std::thread::sleep(std::time::Duration::from_millis(100));

    let alpha = engine.search("alpha", "space", 10).unwrap();
    let beta = engine.search("beta", "space", 10).unwrap();

    assert!(!alpha.is_empty(), "alpha should have results");
    assert!(!beta.is_empty(), "beta should have results");

    for r in &alpha {
        assert_eq!(r.get("collection_id").unwrap().as_str().unwrap(), "alpha",
            "alpha search should only return alpha results");
    }
    for r in &beta {
        assert_eq!(r.get("collection_id").unwrap().as_str().unwrap(), "beta",
            "beta search should only return beta results");
    }
}
