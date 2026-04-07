//! Graph operations — traversal, path finding, export.

use crate::models::{GraphEdge, GraphNode, KnowledgeGraph, SubGraph};
use ordered_float::OrderedFloat;
use std::collections::{HashMap, HashSet, VecDeque};
use uuid::Uuid;

/// BFS subgraph — returns nodes + edges for the visited subgraph within max_hops.
/// The seed node IS included in the result (it is inserted into visited when popped).
pub fn bfs_subgraph(
    graph: &KnowledgeGraph,
    start: Uuid,
    max_hops: u32,
    min_weight: f32,
) -> SubGraph {
    let reachable = bfs_reachable(graph, &[start], max_hops, min_weight);
    let nodes: Vec<GraphNode> = reachable
        .iter()
        .filter_map(|id| graph.nodes.get(id).cloned())
        .collect();
    let edges: Vec<GraphEdge> = graph
        .edges
        .values()
        .filter(|e| reachable.contains(&e.source) && reachable.contains(&e.target))
        .cloned()
        .collect();
    SubGraph {
        nodes,
        edges,
        root_id: start,
        depth: max_hops,
    }
}

/// Breadth-first search — returns all nodes reachable from `seeds` within `max_depth` hops.
/// The seed nodes ARE included in the returned HashSet (they are inserted into visited when
/// popped from the frontier, before expanding their neighbors).
pub fn bfs_reachable(
    graph: &KnowledgeGraph,
    seeds: &[Uuid],
    max_depth: u32,
    min_weight: f32,
) -> HashSet<Uuid> {
    let mut visited: HashSet<Uuid> = HashSet::new();
    let mut frontier: VecDeque<(Uuid, u32)> = seeds.iter().map(|&id| (id, 0)).collect();

    while let Some((node_id, depth)) = frontier.pop_front() {
        if visited.contains(&node_id) {
            continue;
        }
        visited.insert(node_id);

        if depth < max_depth {
            if let Some(neighbors) = graph.adjacency_out.get(&node_id) {
                for &(edge_id, neighbor_id) in neighbors {
                    if !visited.contains(&neighbor_id) {
                        if let Some(edge) = graph.edges.get(&edge_id) {
                            if edge.weight >= min_weight {
                                frontier.push_back((neighbor_id, depth + 1));
                            }
                        }
                    }
                }
            }
        }
    }
    visited
}

pub fn find_shortest_path(
    graph: &KnowledgeGraph,
    from: Uuid,
    to: Uuid,
    _max_depth: u32,
) -> Option<Vec<PathStep>> {
    use std::collections::BinaryHeap;

    let mut dist: HashMap<Uuid, f32> = HashMap::new();
    let mut prev: HashMap<Uuid, (Uuid, Uuid)> = HashMap::new();
    let mut heap: BinaryHeap<(OrderedFloat<f32>, Uuid)> = BinaryHeap::new();

    dist.insert(from, 0.0);
    heap.push((OrderedFloat(0.0), from));

    while let Some((neg_cost, u)) = heap.pop() {
        let cost = -neg_cost.0;
        if cost > *dist.get(&u).unwrap_or(&f32::INFINITY) {
            continue;
        }
        if u == to {
            break;
        }

        if let Some(neighbors) = graph.adjacency_out.get(&u) {
            for &(edge_id, v) in neighbors {
                if let Some(edge) = graph.edges.get(&edge_id) {
                    let edge_cost = 1.0 / edge.weight.max(0.001);
                    let new_cost = cost + edge_cost;
                    if new_cost < *dist.get(&v).unwrap_or(&f32::INFINITY) {
                        dist.insert(v, new_cost);
                        prev.insert(v, (u, edge_id));
                        heap.push((OrderedFloat(-new_cost), v));
                    }
                }
            }
        }
    }

    if !prev.contains_key(&to) && from != to {
        return None;
    }

    let mut path = Vec::new();
    let mut current = to;
    while current != from {
        if let Some((prev_node, edge_id)) = prev.get(&current) {
            if let Some(node) = graph.nodes.get(&current) {
                path.push(PathStep::Node(node.clone()));
            }
            if let Some(edge) = graph.edges.get(edge_id) {
                path.push(PathStep::Edge(edge.clone()));
            }
            current = *prev_node;
        } else {
            break;
        }
    }
    if let Some(node) = graph.nodes.get(&from) {
        path.push(PathStep::Node(node.clone()));
    }
    path.reverse();

    Some(path)
}

pub fn batched_bfs(
    graph: &KnowledgeGraph,
    seeds: Vec<Uuid>,
    max_depth: u32,
    max_degree: usize,
    min_weight: f32,
) -> SubGraph {
    let mut all_nodes: HashMap<Uuid, GraphNode> = HashMap::new();
    let mut all_edges: Vec<GraphEdge> = Vec::new();
    let mut frontier: Vec<Uuid> = seeds.clone();

    let root_id = seeds.first().copied().unwrap_or(Uuid::nil());

    for _ in 0..=max_depth {
        if frontier.is_empty() {
            break;
        }

        let mut next_frontier: Vec<Uuid> = Vec::new();

        for &node_id in &frontier {
            if let Some(node) = graph.nodes.get(&node_id) {
                all_nodes.insert(node_id, node.clone());
            }

            if let Some(out_edges) = graph.adjacency_out.get(&node_id) {
                let edge_targets: Vec<(Uuid, f32)> = out_edges
                    .iter()
                    .filter_map(|(edge_id, target_id)| {
                        graph
                            .edges
                            .get(edge_id)
                            .filter(|e| e.weight >= min_weight)
                            .map(|e| (*target_id, e.weight))
                    })
                    .collect();

                let target_set: HashSet<Uuid> = edge_targets.iter().take(max_degree).map(|(tid, _)| *tid).collect();

                for &(edge_id, target_id) in out_edges {
                    if target_set.contains(&target_id) {
                        if let Some(edge) = graph.edges.get(&edge_id) {
                            all_edges.push(edge.clone());
                            if !all_nodes.contains_key(&target_id) {
                                next_frontier.push(target_id);
                            }
                        }
                    }
                }
            }
        }

        frontier = next_frontier;
    }

    SubGraph {
        nodes: all_nodes.into_values().collect(),
        edges: all_edges,
        root_id,
        depth: max_depth,
    }
}

pub fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    let dot: f32 = a.iter().zip(b.iter()).map(|(x, y)| x * y).sum();
    let mag_a: f32 = a.iter().map(|x| x * x).sum::<f32>().sqrt();
    let mag_b: f32 = b.iter().map(|x| x * x).sum::<f32>().sqrt();
    if mag_a == 0.0 || mag_b == 0.0 {
        0.0
    } else {
        dot / (mag_a * mag_b)
    }
}

pub fn normalize_name(name: &str) -> String {
    name.trim()
        .to_lowercase()
        .chars()
        .filter(|c| c.is_alphanumeric() || c.is_whitespace())
        .collect::<String>()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

#[derive(Debug, Clone, serde::Serialize)]
#[serde(tag = "type", content = "data")]
pub enum PathStep {
    Node(GraphNode),
    Edge(GraphEdge),
}

impl std::fmt::Display for PathStep {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PathStep::Node(n) => write!(f, "Node({})", n.label),
            PathStep::Edge(e) => write!(f, "Edge({})", e.edge_type),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{EdgeType, GraphEdge, GraphNode, KnowledgeGraph, NodeType};
    use std::collections::HashMap;

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
            context: Some(format!("Context {}->{}", source, target)),
            chunk_id: None,
            properties: HashMap::new(),
            collection_id: cid,
        }
    }

    fn make_graph(cid: Uuid) -> KnowledgeGraph {
        KnowledgeGraph::new(cid)
    }

    #[test]
    fn test_bfs_includes_seed_node() {
        let cid = Uuid::new_v4();
        let mut kg = make_graph(cid);
        let a = make_node("A", NodeType::Concept, cid);
        let b = make_node("B", NodeType::Concept, cid);
        kg.insert_nodes_batch(vec![a.clone(), b.clone()]);
        kg.insert_edges_batch(vec![make_edge(a.id, b.id, EdgeType::RelatesTo, 1.0, cid)]);

        let reachable = bfs_reachable(&kg, &[a.id], 2, 0.0);
        assert!(
            reachable.contains(&a.id),
            "Seed node must be in result (BFS-001)"
        );
    }

    #[test]
    fn test_bfs_respects_max_hops() {
        let cid = Uuid::new_v4();
        let mut kg = make_graph(cid);
        let nodes: Vec<_> = (0..4)
            .map(|_| make_node("N", NodeType::Concept, cid))
            .collect();
        let ids: Vec<Uuid> = nodes.iter().map(|n| n.id).collect();
        kg.insert_nodes_batch(nodes);

        // Chain: 0 -> 1 -> 2 -> 3
        for i in 0..3 {
            kg.insert_edges_batch(vec![make_edge(ids[i], ids[i + 1], EdgeType::RelatesTo, 1.0, cid)]);
        }

        let reachable = bfs_reachable(&kg, &[ids[0]], 1, 0.0);
        assert!(!reachable.contains(&ids[2]), "max_hops=1 should NOT reach node at depth 2");
        assert!(!reachable.contains(&ids[3]), "max_hops=1 should NOT reach node at depth 3");
    }

    #[test]
    fn test_bfs_prunes_low_weight_edges() {
        let cid = Uuid::new_v4();
        let mut kg = make_graph(cid);
        let a = make_node("A", NodeType::Concept, cid);
        let b = make_node("B", NodeType::Concept, cid);
        kg.insert_nodes_batch(vec![a.clone(), b.clone()]);
        kg.insert_edges_batch(vec![make_edge(a.id, b.id, EdgeType::RelatesTo, 0.1, cid)]);

        let reachable = bfs_reachable(&kg, &[a.id], 3, 0.5);
        assert!(
            !reachable.contains(&b.id),
            "Edge with weight 0.1 should be pruned by min_weight=0.5"
        );
    }

    #[test]
    fn test_dijkstra_returns_path_step_alternating() {
        let cid = Uuid::new_v4();
        let mut kg = make_graph(cid);
        let a = make_node("A", NodeType::Concept, cid);
        let b = make_node("B", NodeType::Concept, cid);
        let c = make_node("C", NodeType::Concept, cid);
        kg.insert_nodes_batch(vec![a.clone(), b.clone(), c.clone()]);
        kg.insert_edges_batch(vec![
            make_edge(a.id, b.id, EdgeType::RelatesTo, 1.0, cid),
            make_edge(b.id, c.id, EdgeType::RelatesTo, 1.0, cid),
        ]);

        let path = find_shortest_path(&kg, a.id, c.id, 10);
        let path = path.expect("path should exist");

        // A -> B -> C: 3 nodes + 2 edges = 5 steps alternating Node/Edge/Node/Edge/Node
        assert_eq!(path.len(), 5, "Path A->B->C should have 5 steps");
        assert!(matches!(path[0], PathStep::Node(_)));
        assert!(matches!(path[1], PathStep::Edge(_)));
        assert!(matches!(path[2], PathStep::Node(_)));
        assert!(matches!(path[3], PathStep::Edge(_)));
        assert!(matches!(path[4], PathStep::Node(_)));
    }

    #[test]
    fn test_dijkstra_returns_empty_for_disconnected_nodes() {
        let cid = Uuid::new_v4();
        let mut kg = make_graph(cid);
        let a = make_node("A", NodeType::Concept, cid);
        let b = make_node("B", NodeType::Concept, cid);
        kg.insert_nodes_batch(vec![a.clone(), b.clone()]);
        // No edge between them

        let path = find_shortest_path(&kg, a.id, b.id, 10);
        assert!(
            path.is_none() || path.as_ref().map_or(true, |p| p.is_empty()),
            "Disconnected nodes should return None or empty path (not panic)"
        );
    }

    #[test]
    fn test_dijkstra_prefers_higher_weight_edges() {
        let cid = Uuid::new_v4();
        let mut kg = make_graph(cid);
        let a = make_node("A", NodeType::Concept, cid);
        let b = make_node("B", NodeType::Concept, cid);
        let c = make_node("C", NodeType::Concept, cid);
        let d = make_node("D", NodeType::Concept, cid);
        kg.insert_nodes_batch(vec![a.clone(), b.clone(), c.clone(), d.clone()]);

        // Path 1: A -> B -> D (weights 0.5 and 0.5, total cost = 1/0.5 + 1/0.5 = 4)
        // Path 2: A -> C -> D (weights 0.9 and 0.9, total cost = 1/0.9 + 1/0.9 ≈ 2.22)
        kg.insert_edges_batch(vec![
            make_edge(a.id, b.id, EdgeType::RelatesTo, 0.5, cid),
            make_edge(b.id, d.id, EdgeType::RelatesTo, 0.5, cid),
            make_edge(a.id, c.id, EdgeType::RelatesTo, 0.9, cid),
            make_edge(c.id, d.id, EdgeType::RelatesTo, 0.9, cid),
        ]);

        let path = find_shortest_path(&kg, a.id, d.id, 10);
        let path = path.expect("path should exist");

        // Extract node IDs from path
        let path_node_ids: Vec<Uuid> = path
            .iter()
            .filter_map(|s| match s {
                PathStep::Node(n) => Some(n.id),
                _ => None,
            })
            .collect();

        // Should go through C (higher weight path), not B
        assert!(
            path_node_ids.contains(&c.id),
            "Dijkstra should prefer higher-weight path via C (cost ≈2.22) over lower-weight via B (cost=4)"
        );
        assert!(
            !path_node_ids.contains(&b.id),
            "Dijkstra should not take the lower-weight path through B"
        );
    }

    #[test]
    fn test_bfs_subgraph_includes_seed_node() {
        let cid = Uuid::new_v4();
        let mut kg = make_graph(cid);
        let a = make_node("A", NodeType::Concept, cid);
        let b = make_node("B", NodeType::Concept, cid);
        kg.insert_nodes_batch(vec![a.clone(), b.clone()]);
        kg.insert_edges_batch(vec![make_edge(a.id, b.id, EdgeType::RelatesTo, 1.0, cid)]);

        let subgraph = bfs_subgraph(&kg, a.id, 2, 0.0);
        assert!(
            subgraph.nodes.iter().any(|n| n.id == a.id),
            "bfs_subgraph result must include the seed node (BFS-001)"
        );
    }

    #[test]
    fn test_bfs_subgraph_returns_edges_for_visited_nodes() {
        let cid = Uuid::new_v4();
        let mut kg = make_graph(cid);
        let a = make_node("A", NodeType::Concept, cid);
        let b = make_node("B", NodeType::Concept, cid);
        kg.insert_nodes_batch(vec![a.clone(), b.clone()]);
        let edge = make_edge(a.id, b.id, EdgeType::RelatesTo, 1.0, cid);
        kg.insert_edges_batch(vec![edge.clone()]);

        let subgraph = bfs_subgraph(&kg, a.id, 1, 0.0);
        assert!(
            subgraph.edges.len() >= 1,
            "bfs_subgraph should include edges of visited nodes"
        );
    }

    #[test]
    fn test_dijkstra_same_node_returns_node_only() {
        let cid = Uuid::new_v4();
        let mut kg = make_graph(cid);
        let a = make_node("A", NodeType::Concept, cid);
        kg.insert_nodes_batch(vec![a.clone()]);

        let path = find_shortest_path(&kg, a.id, a.id, 10);
        assert!(path.is_some(), "same-node query should return Some");
        let steps = path.unwrap();
        // Should contain only the single Node step (no edges)
        let node_count = steps.iter().filter(|s| matches!(s, PathStep::Node(_))).count();
        assert_eq!(node_count, 1, "same-node path should have exactly 1 Node step");
    }
}
