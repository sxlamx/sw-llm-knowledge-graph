//! Graph operations — traversal, path finding, export.

use crate::models::{GraphEdge, GraphNode, KnowledgeGraph, SubGraph};
use ordered_float::OrderedFloat;
use std::collections::{HashMap, HashSet, VecDeque};
use uuid::Uuid;

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
            if let Some(edge) = graph.edges.get(&edge_id) {
                path.push(PathStep::Edge(EdgePathInfo {
                    edge_id: *edge_id,
                    predicate: edge.edge_type.to_string(),
                    weight: edge.weight,
                }));
            }
            path.push(PathStep::Node(current));
            current = *prev_node;
        } else {
            break;
        }
    }
    path.push(PathStep::Node(from));
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

    for _ in 0..max_depth {
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
pub enum PathStep {
    Node(Uuid),
    Edge(EdgePathInfo),
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct EdgePathInfo {
    pub edge_id: Uuid,
    pub predicate: String,
    pub weight: f32,
}

impl std::fmt::Display for EdgePathInfo {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} (weight: {})", self.predicate, self.weight)
    }
}

impl std::fmt::Display for PathStep {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PathStep::Node(id) => write!(f, "{}", id),
            PathStep::Edge(info) => write!(f, "{}", info),
        }
    }
}
