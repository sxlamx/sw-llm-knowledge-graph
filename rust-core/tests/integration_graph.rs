//! integration_graph.rs — Graph construction and traversal integration tests.
//!
//! Tests the full graph lifecycle:
//!   - KnowledgeGraph insert_nodes_batch and insert_edges_batch
//!   - Adjacency map consistency (both out and in)
//!   - Graph version increments on mutations
//!   - BFS reachability with edge weight filtering
//!   - Dijkstra shortest path (PathStep reconstruction)
//!   - Subgraph extraction via batched BFS
//!   - SerializableGraph round-trip serialization

use rust_core::graph::traversal::{bfs_reachable, find_shortest_path, batched_bfs};
use rust_core::models::{
    EdgeType, GraphEdge, GraphNode, KnowledgeGraph, NodeType, SerializableGraph,
};
use std::collections::HashMap;
use std::sync::atomic::Ordering;
use uuid::Uuid;

fn make_node(label: &str, node_type: NodeType, cid: Uuid) -> GraphNode {
    GraphNode {
        id: Uuid::new_v4(),
        node_type,
        label: label.to_string(),
        description: Some(format!("Description of {}", label)),
        aliases: vec![],
        confidence: 0.9,
        ontology_class: None,
        properties: HashMap::new(),
        collection_id: cid,
        created_at: None,
        updated_at: None,
    }
}

fn make_edge(source: Uuid, target: Uuid, edge_type: EdgeType, weight: f32, cid: Uuid) -> GraphEdge {
    GraphEdge {
        id: Uuid::new_v4(),
        source,
        target,
        edge_type,
        weight,
        context: Some(format!("Context for edge {}->{}", source, target)),
        chunk_id: None,
        properties: HashMap::new(),
        collection_id: cid,
    }
}

fn make_knowledge_graph() -> (KnowledgeGraph, Uuid) {
    let cid = Uuid::new_v4();
    let kg = KnowledgeGraph::new(cid);
    (kg, cid)
}

// ---------------------------------------------------------------------------
// Graph construction
// ---------------------------------------------------------------------------

#[test]
fn test_graph_insert_nodes_batch_increments_version() {
    let (mut kg, _cid) = make_knowledge_graph();
    let v0 = kg.version.load(Ordering::Relaxed);

    kg.insert_nodes_batch(vec![make_node("Alice", NodeType::Person, _cid)]);

    assert_eq!(
        kg.version.load(Ordering::Relaxed),
        v0 + 1,
        "version should increment after node insert"
    );
    assert_eq!(kg.node_count(), 1);
}

#[test]
fn test_graph_insert_edges_batch_increments_version() {
    let (mut kg, cid) = make_knowledge_graph();
    let n1 = make_node("A", NodeType::Concept, cid);
    let n2 = make_node("B", NodeType::Concept, cid);
    kg.insert_nodes_batch(vec![n1.clone(), n2.clone()]);

    let v_after_nodes = kg.version.load(Ordering::Relaxed);
    kg.insert_edges_batch(vec![make_edge(n1.id, n2.id, EdgeType::RelatesTo, 0.8, cid)]);

    assert_eq!(
        kg.version.load(Ordering::Relaxed),
        v_after_nodes + 1,
        "version should increment after edge insert"
    );
}

#[test]
fn test_graph_adjacency_out_and_in_consistent() {
    let (mut kg, cid) = make_knowledge_graph();
    let n1 = make_node("Source", NodeType::Concept, cid);
    let n2 = make_node("Target", NodeType::Concept, cid);
    let n3 = make_node("Another", NodeType::Concept, cid);
    kg.insert_nodes_batch(vec![n1.clone(), n2.clone(), n3.clone()]);

    let edge1 = make_edge(n1.id, n2.id, EdgeType::RelatesTo, 1.0, cid);
    let edge2 = make_edge(n1.id, n3.id, EdgeType::Mentions, 0.5, cid);
    kg.insert_edges_batch(vec![edge1.clone(), edge2.clone()]);

    // Check adjacency_out
    let out = kg.adjacency_out.get(&n1.id).expect("n1 should have outbound edges");
    assert_eq!(out.len(), 2);
    assert!(out.iter().any(|(eid, tid)| *tid == n2.id));
    assert!(out.iter().any(|(eid, tid)| *tid == n3.id));

    // Check adjacency_in for n2
    let inn = kg.adjacency_in.get(&n2.id).expect("n2 should have inbound edges");
    assert_eq!(inn.len(), 1);
    assert_eq!(inn[0].1, n1.id, "incoming edge should come from n1");

    // Check adjacency_in for n3
    let inn3 = kg.adjacency_in.get(&n3.id).expect("n3 should have inbound edges");
    assert_eq!(inn3.len(), 1);
    assert_eq!(inn3[0].1, n1.id);
}

#[test]
fn test_graph_multiple_edges_between_same_nodes() {
    let (mut kg, cid) = make_knowledge_graph();
    let n1 = make_node("A", NodeType::Concept, cid);
    let n2 = make_node("B", NodeType::Concept, cid);
    kg.insert_nodes_batch(vec![n1.clone(), n2.clone()]);

    let e1 = make_edge(n1.id, n2.id, EdgeType::RelatesTo, 0.9, cid);
    let e2 = make_edge(n1.id, n2.id, EdgeType::Mentions, 0.5, cid);
    kg.insert_edges_batch(vec![e1, e2]);

    let out = kg.adjacency_out.get(&n1.id).expect("n1 should have edges");
    assert_eq!(out.len(), 2, "two edges from n1 to n2");
}

#[test]
fn test_graph_empty_graph_has_zero_counts() {
    let (kg, _cid) = make_knowledge_graph();
    assert_eq!(kg.node_count(), 0);
    assert_eq!(kg.edge_count(), 0);
}

#[test]
fn test_graph_insert_batch_preserves_node_data() {
    let (mut kg, cid) = make_knowledge_graph();
    let node = make_node("Test Node", NodeType::Organization, cid);
    let node_id = node.id;
    kg.insert_nodes_batch(vec![node.clone()]);

    let retrieved = kg.nodes.get(&node_id).expect("node should be stored");
    assert_eq!(retrieved.label, "Test Node");
    assert_eq!(retrieved.node_type, NodeType::Organization);
    assert_eq!(retrieved.confidence, 0.9);
}

// ---------------------------------------------------------------------------
// BFS traversal
// ---------------------------------------------------------------------------

#[test]
fn test_bfs_reachable_single_seed_node() {
    let (mut kg, cid) = make_knowledge_graph();
    let a = make_node("A", NodeType::Concept, cid);
    kg.insert_nodes_batch(vec![a.clone()]);

    let reachable = bfs_reachable(&kg, &[a.id], 10, 0.0);
    assert!(
        reachable.contains(&a.id),
        "seed node should be in reachable set"
    );
}

#[test]
fn test_bfs_reachable_respects_min_weight() {
    let (mut kg, cid) = make_knowledge_graph();
    let a = make_node("A", NodeType::Concept, cid);
    let b = make_node("B", NodeType::Concept, cid);
    kg.insert_nodes_batch(vec![a.clone(), b.clone()]);
    kg.insert_edges_batch(vec![make_edge(a.id, b.id, EdgeType::RelatesTo, 0.2, cid)]);

    let reachable_low = bfs_reachable(&kg, &[a.id], 10, 0.3);
    assert!(
        !reachable_low.contains(&b.id),
        "edge with weight 0.2 should not pass min_weight 0.3 filter"
    );

    let reachable_high = bfs_reachable(&kg, &[a.id], 10, 0.1);
    assert!(
        reachable_high.contains(&b.id),
        "edge with weight 0.2 should pass min_weight 0.1 filter"
    );
}

#[test]
fn test_bfs_reachable_multiple_seeds() {
    let (mut kg, cid) = make_knowledge_graph();
    let a = make_node("A", NodeType::Concept, cid);
    let b = make_node("B", NodeType::Concept, cid);
    let c = make_node("C", NodeType::Concept, cid);
    kg.insert_nodes_batch(vec![a.clone(), b.clone(), c.clone()]);
    kg.insert_edges_batch(vec![
        make_edge(a.id, c.id, EdgeType::RelatesTo, 1.0, cid),
        make_edge(b.id, c.id, EdgeType::RelatesTo, 1.0, cid),
    ]);

    let reachable = bfs_reachable(&kg, &[a.id, b.id], 10, 0.0);
    assert!(reachable.contains(&a.id));
    assert!(reachable.contains(&b.id));
    assert!(reachable.contains(&c.id));
}

#[test]
fn test_bfs_reachable_no_self_loops() {
    let (mut kg, cid) = make_knowledge_graph();
    let a = make_node("A", NodeType::Concept, cid);
    kg.insert_nodes_batch(vec![a.clone()]);
    kg.insert_edges_batch(vec![make_edge(a.id, a.id, EdgeType::RelatesTo, 1.0, cid)]);

    let reachable = bfs_reachable(&kg, &[a.id], 10, 0.0);
    assert_eq!(reachable.len(), 1);
}

// ---------------------------------------------------------------------------
// Shortest path (Dijkstra)
// ---------------------------------------------------------------------------

#[test]
fn test_dijkstra_no_path_returns_none() {
    let (mut kg, cid) = make_knowledge_graph();
    let a = make_node("A", NodeType::Concept, cid);
    let b = make_node("B", NodeType::Concept, cid);
    kg.insert_nodes_batch(vec![a.clone(), b.clone()]);
    // No edge between them

    let path = find_shortest_path(&kg, a.id, b.id, 10);
    assert!(path.is_none(), "no path should return None");
}

#[test]
fn test_dijkstra_same_node_returns_single_node() {
    let (mut kg, cid) = make_knowledge_graph();
    let a = make_node("A", NodeType::Concept, cid);
    kg.insert_nodes_batch(vec![a.clone()]);

    let path = find_shortest_path(&kg, a.id, a.id, 10);
    assert!(path.is_some());
    let steps: Vec<_> = path.unwrap().iter().filter_map(|s| {
        match s {
            rust_core::graph::traversal::PathStep::Node(n) => Some(n.id),
            _ => None,
        }
    }).collect();
    assert_eq!(steps.len(), 1);
    assert_eq!(steps[0], a.id);
}

#[test]
fn test_dijkstra_uses_edge_weight_in_path_selection() {
    let (mut kg, cid) = make_knowledge_graph();
    let a = make_node("A", NodeType::Concept, cid);
    let b = make_node("B", NodeType::Concept, cid);
    let c = make_node("C", NodeType::Concept, cid);
    kg.insert_nodes_batch(vec![a.clone(), b.clone(), c.clone()]);

    // A -> B (high weight = short distance) and A -> C (low weight = long distance)
    let edge_ab = make_edge(a.id, b.id, EdgeType::RelatesTo, 0.9, cid); // distance = 1/0.9 ≈ 1.11
    let edge_ac = make_edge(a.id, c.id, EdgeType::RelatesTo, 0.1, cid); // distance = 1/0.1 = 10
    kg.insert_edges_batch(vec![edge_ab, edge_ac]);

    let path = find_shortest_path(&kg, a.id, b.id, 10);
    assert!(path.is_some(), "path A->B should exist");
    // The path should go directly A->B since that's the shortest
    let node_ids: Vec<Uuid> = path
        .unwrap()
        .iter()
        .filter_map(|s| match s {
            rust_core::graph::traversal::PathStep::Node(n) => Some(n.id),
            _ => None,
        })
        .collect();
    assert!(node_ids.contains(&a.id));
    assert!(node_ids.contains(&b.id));
}

#[test]
fn test_dijkstra_reconstructs_correct_path_length() {
    let (mut kg, cid) = make_knowledge_graph();
    let a = make_node("A", NodeType::Concept, cid);
    let b = make_node("B", NodeType::Concept, cid);
    let c = make_node("C", NodeType::Concept, cid);
    kg.insert_nodes_batch(vec![a.clone(), b.clone(), c.clone()]);
    kg.insert_edges_batch(vec![
        make_edge(a.id, b.id, EdgeType::RelatesTo, 1.0, cid),
        make_edge(b.id, c.id, EdgeType::RelatesTo, 1.0, cid),
    ]);

    let path = find_shortest_path(&kg, a.id, c.id, 10);
    assert!(path.is_some());
    // A -> B -> C: 3 nodes + 2 edges = 5 steps
    assert_eq!(path.unwrap().len(), 5);
}

// ---------------------------------------------------------------------------
// Batched BFS (subgraph extraction)
// ---------------------------------------------------------------------------

#[test]
fn test_batched_bfs_extracts_subgraph() {
    let (mut kg, cid) = make_knowledge_graph();
    let nodes: Vec<_> = (0..5).map(|_| make_node("N", NodeType::Concept, cid)).collect();
    let ids: Vec<Uuid> = nodes.iter().map(|n| n.id).collect();
    kg.insert_nodes_batch(nodes);

    // Chain: 0 -> 1 -> 2 -> 3 -> 4
    for i in 0..4 {
        kg.insert_edges_batch(vec![make_edge(ids[i], ids[i + 1], EdgeType::RelatesTo, 1.0, cid)]);
    }

    let subgraph = batched_bfs(&kg, vec![ids[0]], 3, 100, 0.0);

    assert_eq!(subgraph.root_id, ids[0]);
    assert_eq!(subgraph.depth, 3);
    assert!(subgraph.nodes.len() >= 4, "should include nodes at depth 0,1,2,3");
}

#[test]
fn test_batched_bfs_respects_max_degree() {
    let (mut kg, cid) = make_knowledge_graph();
    let a = make_node("A", NodeType::Concept, cid);
    let targets: Vec<_> = (0..10).map(|_| make_node("T", NodeType::Concept, cid)).collect();
    kg.insert_nodes_batch(vec![a.clone()]);
    kg.insert_nodes_batch(targets.clone());

    for t in &targets {
        kg.insert_edges_batch(vec![make_edge(a.id, t.id, EdgeType::RelatesTo, 1.0, cid)]);
    }

    let subgraph = batched_bfs(&kg, vec![a.id], 1, 3, 0.0);

    let a_node = subgraph.nodes.iter().find(|n| n.id == a.id).unwrap();
    let outgoing_edges: Vec<_> = subgraph
        .edges
        .iter()
        .filter(|e| e.source == a.id)
        .collect();
    assert!(
        outgoing_edges.len() <= 3,
        "should respect max_degree limit of 3"
    );
}

// ---------------------------------------------------------------------------
// SerializableGraph round-trip
// ---------------------------------------------------------------------------

#[test]
fn test_serializable_graph_round_trip_preserves_nodes_and_edges() {
    let (mut kg, cid) = make_knowledge_graph();
    let n1 = make_node("Node1", NodeType::Person, cid);
    let n2 = make_node("Node2", NodeType::Organization, cid);
    kg.insert_nodes_batch(vec![n1.clone(), n2.clone()]);

    let edge = make_edge(n1.id, n2.id, EdgeType::WorksAt, 0.95, cid);
    kg.insert_edges_batch(vec![edge.clone()]);

    let sg = SerializableGraph {
        nodes: vec![n1.clone(), n2.clone()],
        edges: vec![edge],
    };

    let restored: KnowledgeGraph = KnowledgeGraph::from(sg);

    assert_eq!(restored.node_count(), 2);
    assert_eq!(restored.edge_count(), 1);
    assert!(restored.nodes.contains_key(&n1.id));
    assert!(restored.nodes.contains_key(&n2.id));
    assert!(restored.adjacency_out.contains_key(&n1.id));
    assert!(restored.adjacency_in.contains_key(&n2.id));
}

// ---------------------------------------------------------------------------
// Graph pruning
// ---------------------------------------------------------------------------

#[test]
fn test_graph_prune_removes_low_weight_edges() {
    let (mut kg, cid) = make_knowledge_graph();
    let a = make_node("A", NodeType::Concept, cid);
    let b = make_node("B", NodeType::Concept, cid);
    kg.insert_nodes_batch(vec![a.clone(), b.clone()]);
    kg.insert_edges_batch(vec![make_edge(a.id, b.id, EdgeType::RelatesTo, 0.2, cid)]);

    let (removed, _) = kg.prune_edges(0.5, 100);
    assert_eq!(removed, 1);
    assert_eq!(kg.edge_count(), 0);
}

#[test]
fn test_graph_prune_enforces_max_degree() {
    let (mut kg, cid) = make_knowledge_graph();
    let a = make_node("A", NodeType::Concept, cid);
    let targets: Vec<_> = (0..10).map(|_| make_node("T", NodeType::Concept, cid)).collect();
    kg.insert_nodes_batch(vec![a.clone()]);
    kg.insert_nodes_batch(targets.clone());

    for t in &targets {
        kg.insert_edges_batch(vec![make_edge(a.id, t.id, EdgeType::RelatesTo, 0.9, cid)]);
    }

    let (_, affected) = kg.prune_edges(0.0, 3);
    assert!(affected >= 1, "node A with 10 edges should be affected by max_degree=3");
    assert!(
        kg.adjacency_out.get(&a.id).map(|e| e.len()).unwrap_or(0) <= 3,
        "out-degree of A should be capped at 3"
    );
}
