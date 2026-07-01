//! Core data models for the knowledge graph engine.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use uuid::Uuid;

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NodeType {
    Person,
    Organization,
    Location,
    Concept,
    Event,
    Document,
    Chunk,
    Topic,
    Custom(String),
}

impl std::fmt::Display for NodeType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            NodeType::Person => write!(f, "person"),
            NodeType::Organization => write!(f, "organization"),
            NodeType::Location => write!(f, "location"),
            NodeType::Concept => write!(f, "concept"),
            NodeType::Event => write!(f, "event"),
            NodeType::Document => write!(f, "document"),
            NodeType::Chunk => write!(f, "chunk"),
            NodeType::Topic => write!(f, "topic"),
            NodeType::Custom(s) => write!(f, "{}", s),
        }
    }
}

impl std::str::FromStr for NodeType {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.to_lowercase().as_str() {
            "person" => Ok(NodeType::Person),
            "organization" => Ok(NodeType::Organization),
            "location" => Ok(NodeType::Location),
            "concept" => Ok(NodeType::Concept),
            "event" => Ok(NodeType::Event),
            "document" => Ok(NodeType::Document),
            "chunk" => Ok(NodeType::Chunk),
            "topic" => Ok(NodeType::Topic),
            _ => Ok(NodeType::Custom(s.to_string())),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EdgeType {
    Mentions,
    RelatesTo,
    WorksAt,
    Founded,
    LocatedIn,
    ParticipatedIn,
    BelongsToTopic,
    DerivedFrom,
    SimilarTo,
    Next,
    Custom(String),
}

impl std::fmt::Display for EdgeType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            EdgeType::Mentions => write!(f, "mentions"),
            EdgeType::RelatesTo => write!(f, "relates_to"),
            EdgeType::WorksAt => write!(f, "works_at"),
            EdgeType::Founded => write!(f, "founded"),
            EdgeType::LocatedIn => write!(f, "located_in"),
            EdgeType::ParticipatedIn => write!(f, "participated_in"),
            EdgeType::BelongsToTopic => write!(f, "belongs_to_topic"),
            EdgeType::DerivedFrom => write!(f, "derived_from"),
            EdgeType::SimilarTo => write!(f, "similar_to"),
            EdgeType::Next => write!(f, "next"),
            EdgeType::Custom(s) => write!(f, "{}", s),
        }
    }
}

impl std::str::FromStr for EdgeType {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.to_lowercase().as_str() {
            "mentions" => Ok(EdgeType::Mentions),
            "relates_to" => Ok(EdgeType::RelatesTo),
            "works_at" => Ok(EdgeType::WorksAt),
            "founded" => Ok(EdgeType::Founded),
            "located_in" => Ok(EdgeType::LocatedIn),
            "participated_in" => Ok(EdgeType::ParticipatedIn),
            "belongs_to_topic" => Ok(EdgeType::BelongsToTopic),
            "derived_from" => Ok(EdgeType::DerivedFrom),
            "similar_to" => Ok(EdgeType::SimilarTo),
            "next" => Ok(EdgeType::Next),
            _ => Ok(EdgeType::Custom(s.to_string())),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphNode {
    pub id: Uuid,
    pub node_type: NodeType,
    pub label: String,
    pub description: Option<String>,
    pub aliases: Vec<String>,
    pub confidence: f32,
    pub ontology_class: Option<String>,
    pub properties: HashMap<String, serde_json::Value>,
    pub collection_id: Uuid,
    #[serde(default)]
    pub display_label: Option<String>,
    #[serde(default)]
    pub dedup_key: Option<String>,
    #[serde(default)]
    pub doc_origins: Vec<Uuid>,
    #[serde(with = "chrono::serde::ts_microseconds_option")]
    pub created_at: Option<chrono::DateTime<chrono::Utc>>,
    #[serde(with = "chrono::serde::ts_microseconds_option")]
    pub updated_at: Option<chrono::DateTime<chrono::Utc>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphEdge {
    pub id: Uuid,
    pub source: Uuid,
    pub target: Uuid,
    pub edge_type: EdgeType,
    pub weight: f32,
    pub context: Option<String>,
    pub chunk_id: Option<Uuid>,
    pub properties: HashMap<String, serde_json::Value>,
    pub collection_id: Uuid,
    #[serde(default)]
    pub display_label: Option<String>,
    #[serde(default)]
    pub dedup_key: Option<String>,
    #[serde(default)]
    pub predicate: String,
    #[serde(default)]
    pub time: Option<String>,
    #[serde(default)]
    pub location: Option<String>,
    #[serde(default)]
    pub participants: Option<Vec<Uuid>>,
    #[serde(default)]
    pub doc_origins: Vec<Uuid>,
}

#[derive(Debug)]
pub struct KnowledgeGraph {
    pub nodes: HashMap<Uuid, GraphNode>,
    pub adjacency_out: HashMap<Uuid, Vec<(Uuid, Uuid)>>,
    pub adjacency_in: HashMap<Uuid, Vec<(Uuid, Uuid)>>,
    pub edges: HashMap<Uuid, GraphEdge>,
    pub version: std::sync::atomic::AtomicU64,
    pub collection_id: Uuid,
}

impl KnowledgeGraph {
    pub fn new(collection_id: Uuid) -> Self {
        Self {
            nodes: HashMap::new(),
            adjacency_out: HashMap::new(),
            adjacency_in: HashMap::new(),
            edges: HashMap::new(),
            version: std::sync::atomic::AtomicU64::new(0),
            collection_id,
        }
    }

    pub fn insert_nodes_batch(&mut self, nodes: Vec<GraphNode>) {
        if nodes.is_empty() {
            return;
        }
        for node in nodes {
            self.nodes.insert(node.id, node);
        }
        self.version
            .fetch_add(1, std::sync::atomic::Ordering::Release);
    }

    pub fn insert_edges_batch(&mut self, edges: Vec<GraphEdge>) {
        if edges.is_empty() {
            return;
        }
        for edge in edges {
            self.adjacency_out
                .entry(edge.source)
                .or_default()
                .push((edge.id, edge.target));
            self.adjacency_in
                .entry(edge.target)
                .or_default()
                .push((edge.id, edge.source));
            self.edges.insert(edge.id, edge);
        }
        self.version
            .fetch_add(1, std::sync::atomic::Ordering::Release);
    }

    pub fn node_count(&self) -> usize {
        self.nodes.len()
    }
    pub fn edge_count(&self) -> usize {
        self.edges.len()
    }

    /// Prune edges that reference non-existent nodes.
    /// For binary edges: checks source and target.
    /// For hyperedges (participants is Some): checks ALL participants.
    /// Returns the number of pruned edges and rebuilds adjacency maps.
    pub fn prune_dangling_edges(&mut self) -> usize {
        use std::collections::HashSet;
        let valid_node_ids: HashSet<Uuid> = self.nodes.keys().copied().collect();

        let dangling: Vec<Uuid> = self.edges.iter()
            .filter(|(_, edge)| {
                if let Some(participants) = &edge.participants {
                    participants.iter().any(|p| !valid_node_ids.contains(p))
                } else {
                    !valid_node_ids.contains(&edge.source) || !valid_node_ids.contains(&edge.target)
                }
            })
            .map(|(id, _)| *id)
            .collect();

        let count = dangling.len();
        for id in &dangling {
            self.edges.remove(id);
        }
        if count > 0 {
            self.rebuild_adjacency();
        }
        count
    }

    pub(crate) fn rebuild_adjacency(&mut self) {
        self.adjacency_out.clear();
        self.adjacency_in.clear();
        for (eid, edge) in &self.edges {
            if let Some(participants) = &edge.participants {
                // Hyperedge: add adjacency entries between all participant pairs
                for i in 0..participants.len() {
                    for j in 0..participants.len() {
                        if i != j {
                            self.adjacency_out
                                .entry(participants[i])
                                .or_default()
                                .push((*eid, participants[j]));
                            self.adjacency_in
                                .entry(participants[j])
                                .or_default()
                                .push((*eid, participants[i]));
                        }
                    }
                }
            } else {
                // Binary edge: standard source → target
                self.adjacency_out
                    .entry(edge.source)
                    .or_default()
                    .push((*eid, edge.target));
                self.adjacency_in
                    .entry(edge.target)
                    .or_default()
                    .push((*eid, edge.source));
            }
        }
        self.version.fetch_add(1, std::sync::atomic::Ordering::Release);
    }

    /// Prune low-weight edges and enforce a per-node out-degree cap.
    ///
    /// Steps:
    ///   1. Remove all edges whose `weight < min_weight`.
    ///   2. For each node, if out-degree exceeds `max_degree`, keep only the
    ///      `max_degree` highest-weight outbound edges and drop the rest.
    ///   3. Rebuild adjacency maps to stay consistent with the pruned edge set.
    ///   4. Bumps the graph version so caches are invalidated.
    ///
    /// Returns `(edges_removed, nodes_affected)`.
    pub fn prune_edges(&mut self, min_weight: f32, max_degree: usize) -> (usize, usize) {
        let before = self.edges.len();

        // ── Step 1: Remove below-threshold edges ──────────────────────────
        self.edges.retain(|_, e| e.weight >= min_weight);

        // ── Step 2: Enforce max out-degree ────────────────────────────────
        // Group edges by source node.
        let mut by_source: HashMap<Uuid, Vec<Uuid>> = HashMap::new();
        for (eid, edge) in &self.edges {
            by_source.entry(edge.source).or_default().push(*eid);
        }

        let mut over_degree_nodes = 0usize;
        for (_, edge_ids) in by_source.iter_mut() {
            if edge_ids.len() > max_degree {
                // Sort descending by weight; keep top `max_degree`, drop the rest.
                edge_ids.sort_by(|a, b| {
                    let wa = self.edges.get(a).map(|e| e.weight).unwrap_or(0.0);
                    let wb = self.edges.get(b).map(|e| e.weight).unwrap_or(0.0);
                    wb.partial_cmp(&wa).unwrap_or(std::cmp::Ordering::Equal)
                });
                let to_remove = edge_ids.split_off(max_degree);
                for eid in to_remove {
                    self.edges.remove(&eid);
                }
                over_degree_nodes += 1;
            }
        }

        // ── Step 3: Rebuild adjacency maps ───────────────────────────────
        self.adjacency_out.clear();
        self.adjacency_in.clear();
        for (eid, edge) in &self.edges {
            self.adjacency_out
                .entry(edge.source)
                .or_default()
                .push((*eid, edge.target));
            self.adjacency_in
                .entry(edge.target)
                .or_default()
                .push((*eid, edge.source));
        }

        // ── Step 4: Bump version ─────────────────────────────────────────
        let removed = before - self.edges.len();
        if removed > 0 {
            self.version.fetch_add(1, std::sync::atomic::Ordering::Release);
        }

        (removed, over_degree_nodes)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchResult {
    pub chunk_id: Uuid,
    pub doc_id: Uuid,
    pub text: String,
    pub contextual_text: String,
    pub vector_score: f32,
    pub keyword_score: f32,
    pub graph_proximity_score: f32,
    pub final_score: f32,
    pub page: Option<i32>,
    pub topics: Vec<String>,
    #[serde(default)]
    pub highlights: Vec<String>,
}

impl Default for SearchResult {
    fn default() -> Self {
        Self {
            chunk_id: Uuid::nil(),
            doc_id: Uuid::nil(),
            text: String::new(),
            contextual_text: String::new(),
            vector_score: 0.0,
            keyword_score: 0.0,
            graph_proximity_score: 0.0,
            final_score: 0.0,
            page: None,
            topics: Vec::new(),
            highlights: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SubGraph {
    pub nodes: Vec<GraphNode>,
    pub edges: Vec<GraphEdge>,
    pub root_id: Uuid,
    pub depth: u32,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum JobStatus {
    Pending,
    Running {
        processed: u32,
        total: u32,
        current_file: String,
    },
    Completed {
        processed: u32,
        duration_secs: f64,
    },
    Failed {
        error: String,
    },
    Cancelled,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IngestJob {
    pub id: Uuid,
    pub collection_id: Uuid,
    pub folder_path: String,
    pub status: JobStatus,
    pub options: IngestOptions,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IngestOptions {
    pub max_cost_usd: Option<f64>,
    pub ocr_enabled: bool,
    pub max_files: usize,
    pub max_depth: usize,
    pub chunk_size_tokens: usize,
    pub chunk_overlap_tokens: usize,
}

impl Default for IngestOptions {
    fn default() -> Self {
        Self {
            max_cost_usd: None,
            ocr_enabled: false,
            max_files: 10_000,
            max_depth: 5,
            chunk_size_tokens: 512,
            chunk_overlap_tokens: 50,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChunkRecord {
    pub id: String,
    pub doc_id: String,
    pub collection_id: String,
    pub text: String,
    pub contextual_text: String,
    pub embedding: Vec<f32>,
    pub position: i32,
    pub token_count: Option<i32>,
    pub page: Option<i32>,
    pub topics: Vec<String>,
    pub created_at: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NodeRecord {
    pub id: String,
    pub collection_id: String,
    pub label: String,
    pub node_type: String,
    pub description: Option<String>,
    pub aliases: Vec<String>,
    pub embedding: Vec<f32>,
    pub confidence: f32,
    pub ontology_class: Option<String>,
    pub metadata: Option<String>,
    #[serde(default)]
    pub doc_origins: Vec<Uuid>,
    pub created_at: i64,
    pub updated_at: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EdgeRecord {
    pub id: String,
    pub collection_id: String,
    pub source_id: String,
    pub target_id: String,
    pub predicate: String,
    pub weight: f32,
    pub context: Option<String>,
    pub chunk_id: Option<String>,
    #[serde(default)]
    pub doc_origins: Vec<Uuid>,
    pub created_at: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DocumentRecord {
    pub id: String,
    pub collection_id: String,
    pub title: String,
    pub source: String,
    pub path: String,
    pub file_type: String,
    pub file_hash: Option<String>,
    pub raw_content: Vec<u8>,
    pub doc_summary: Option<String>,
    pub metadata: Option<String>,
    pub created_at: i64,
    pub updated_at: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExtractedEntity {
    pub name: String,
    pub entity_type: String,
    pub description: String,
    pub aliases: Vec<String>,
    pub confidence: f32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExtractedRelationship {
    pub source: String,
    pub target: String,
    pub predicate: String,
    pub context: String,
    pub confidence: f32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExtractionResult {
    pub entities: Vec<ExtractedEntity>,
    pub relationships: Vec<ExtractedRelationship>,
    pub topics: Vec<String>,
    pub summary: String,
}

// Serializable wrapper for KnowledgeGraph (used for JSON deserialization in pyfunctions)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SerializableGraph {
    pub nodes: Vec<GraphNode>,
    pub edges: Vec<GraphEdge>,
}

impl From<SerializableGraph> for KnowledgeGraph {
    fn from(sg: SerializableGraph) -> Self {
        let mut kg = KnowledgeGraph::new(Uuid::nil());
        for node in sg.nodes {
            kg.nodes.insert(node.id, node);
        }
        for edge in sg.edges {
            kg.adjacency_out
                .entry(edge.source)
                .or_default()
                .push((edge.id, edge.target));
            kg.adjacency_in
                .entry(edge.target)
                .or_default()
                .push((edge.id, edge.source));
            kg.edges.insert(edge.id, edge);
        }
        kg
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::str::FromStr;
    use std::sync::atomic::Ordering;

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

    // ── KnowledgeGraph basics ────────────────────────────────────────────

    #[test]
    fn test_knowledge_graph_new_is_empty() {
        let cid = Uuid::new_v4();
        let kg = KnowledgeGraph::new(cid);
        assert_eq!(kg.node_count(), 0);
        assert_eq!(kg.edge_count(), 0);
        assert_eq!(kg.version.load(Ordering::Relaxed), 0);
        assert_eq!(kg.collection_id, cid);
        assert!(kg.adjacency_out.is_empty());
        assert!(kg.adjacency_in.is_empty());
    }

    #[test]
    fn test_insert_nodes_batch_increments_version() {
        let cid = Uuid::new_v4();
        let mut kg = KnowledgeGraph::new(cid);
        let v0 = kg.version.load(Ordering::Relaxed);

        kg.insert_nodes_batch(vec![
            make_node("A", NodeType::Person, cid),
            make_node("B", NodeType::Organization, cid),
        ]);

        assert_eq!(kg.version.load(Ordering::Relaxed), v0 + 1);
        assert_eq!(kg.node_count(), 2);
    }

    #[test]
    fn test_insert_edges_batch_increments_version() {
        let cid = Uuid::new_v4();
        let mut kg = KnowledgeGraph::new(cid);
        let a = make_node("A", NodeType::Person, cid);
        let b = make_node("B", NodeType::Organization, cid);
        kg.insert_nodes_batch(vec![a.clone(), b.clone()]);

        let v_before = kg.version.load(Ordering::Relaxed);
        kg.insert_edges_batch(vec![make_edge(a.id, b.id, 0.8, cid)]);
        assert_eq!(kg.version.load(Ordering::Relaxed), v_before + 1);
    }

    #[test]
    fn test_insert_edges_updates_both_adjacency_maps() {
        let cid = Uuid::new_v4();
        let mut kg = KnowledgeGraph::new(cid);
        let src = make_node("Src", NodeType::Person, cid);
        let tgt = make_node("Tgt", NodeType::Organization, cid);
        kg.insert_nodes_batch(vec![src.clone(), tgt.clone()]);

        let edge = make_edge(src.id, tgt.id, 0.9, cid);
        let edge_id = edge.id;
        kg.insert_edges_batch(vec![edge]);

        let adj_out = kg.adjacency_out.get(&src.id).expect("adjacency_out[src] should exist");
        assert!(adj_out.iter().any(|(eid, tid)| *eid == edge_id && *tid == tgt.id),
            "adjacency_out should contain (edge_id, target_id)");

        let adj_in = kg.adjacency_in.get(&tgt.id).expect("adjacency_in[tgt] should exist");
        assert!(adj_in.iter().any(|(eid, sid)| *eid == edge_id && *sid == src.id),
            "adjacency_in should contain (edge_id, source_id)");
    }

    #[test]
    fn test_multiple_edges_between_same_nodes() {
        let cid = Uuid::new_v4();
        let mut kg = KnowledgeGraph::new(cid);
        let a = make_node("A", NodeType::Person, cid);
        let b = make_node("B", NodeType::Organization, cid);
        kg.insert_nodes_batch(vec![a.clone(), b.clone()]);

        let e1 = make_edge(a.id, b.id, 0.8, cid);
        let e2 = make_edge(a.id, b.id, 0.5, cid);
        kg.insert_edges_batch(vec![e1.clone(), e2.clone()]);

        let adj_out = kg.adjacency_out.get(&a.id).unwrap();
        assert_eq!(adj_out.len(), 2, "should have two outbound edges from A to B");
        assert_eq!(kg.edge_count(), 2);
    }

    #[test]
    fn test_insert_nodes_batch_upserts_on_duplicate_id() {
        let cid = Uuid::new_v4();
        let mut kg = KnowledgeGraph::new(cid);
        let mut node = make_node("Alice", NodeType::Person, cid);
        let node_id = node.id;
        kg.insert_nodes_batch(vec![node.clone()]);

        node.label = "Alice Updated".to_string();
        kg.insert_nodes_batch(vec![node]);

        assert_eq!(kg.node_count(), 1);
        assert_eq!(kg.nodes.get(&node_id).unwrap().label, "Alice Updated");
    }

    #[test]
    fn test_insert_empty_batch_does_not_change_version() {
        let cid = Uuid::new_v4();
        let mut kg = KnowledgeGraph::new(cid);
        let v0 = kg.version.load(Ordering::Relaxed);

        kg.insert_nodes_batch(vec![]);
        assert_eq!(kg.version.load(Ordering::Relaxed), v0, "empty batch should not bump version");

        kg.insert_edges_batch(vec![]);
        assert_eq!(kg.version.load(Ordering::Relaxed), v0, "empty edge batch should not bump version");
    }

    // ── prune_edges ─────────────────────────────────────────────────────

    #[test]
    fn test_prune_removes_below_min_weight() {
        let cid = Uuid::new_v4();
        let mut kg = KnowledgeGraph::new(cid);
        let a = make_node("A", NodeType::Concept, cid);
        let b = make_node("B", NodeType::Concept, cid);
        let c = make_node("C", NodeType::Concept, cid);
        kg.insert_nodes_batch(vec![a.clone(), b.clone(), c.clone()]);
        kg.insert_edges_batch(vec![
            make_edge(a.id, b.id, 0.8, cid),
            make_edge(a.id, c.id, 0.2, cid),
        ]);

        let (removed, _) = kg.prune_edges(0.5, 100);
        assert_eq!(removed, 1);
        assert_eq!(kg.edge_count(), 1);
        assert!(kg.edges.values().all(|e| e.weight >= 0.5));
    }

    #[test]
    fn test_prune_rebuilds_adjacency_maps() {
        let cid = Uuid::new_v4();
        let mut kg = KnowledgeGraph::new(cid);
        let a = make_node("A", NodeType::Concept, cid);
        let b = make_node("B", NodeType::Concept, cid);
        let c = make_node("C", NodeType::Concept, cid);
        kg.insert_nodes_batch(vec![a.clone(), b.clone(), c.clone()]);
        let keep_edge = make_edge(a.id, b.id, 0.8, cid);
        let drop_edge = make_edge(a.id, c.id, 0.1, cid);
        kg.insert_edges_batch(vec![keep_edge.clone(), drop_edge.clone()]);

        kg.prune_edges(0.5, 100);

        let adj_out = kg.adjacency_out.get(&a.id).unwrap();
        assert!(adj_out.iter().any(|(eid, _)| *eid == keep_edge.id));
        assert!(!adj_out.iter().any(|(eid, _)| *eid == drop_edge.id),
            "pruned edge should not appear in adjacency_out");
    }

    #[test]
    fn test_prune_enforces_max_degree() {
        let cid = Uuid::new_v4();
        let mut kg = KnowledgeGraph::new(cid);
        let hub = make_node("Hub", NodeType::Concept, cid);
        kg.insert_nodes_batch(vec![hub.clone()]);

        let mut edges = Vec::new();
        for i in 0..5u32 {
            let spoke = make_node(&format!("Spoke{}", i), NodeType::Concept, cid);
            kg.insert_nodes_batch(vec![spoke.clone()]);
            edges.push(make_edge(hub.id, spoke.id, 0.5 + (i as f32 * 0.1), cid));
        }
        kg.insert_edges_batch(edges);

        let (removed, affected) = kg.prune_edges(0.0, 3);
        assert_eq!(removed, 2, "max_degree=3 should remove 2 of 5 edges");
        assert_eq!(affected, 1, "only the hub node should be affected");
        assert_eq!(kg.edge_count(), 3);
    }

    #[test]
    fn test_prune_bumps_version() {
        let cid = Uuid::new_v4();
        let mut kg = KnowledgeGraph::new(cid);
        let a = make_node("A", NodeType::Concept, cid);
        let b = make_node("B", NodeType::Concept, cid);
        kg.insert_nodes_batch(vec![a.clone(), b.clone()]);
        kg.insert_edges_batch(vec![make_edge(a.id, b.id, 0.1, cid)]);

        let v_before = kg.version.load(Ordering::Relaxed);
        kg.prune_edges(0.5, 100);
        assert!(kg.version.load(Ordering::Relaxed) > v_before,
            "prune with removals should bump version");
    }

    #[test]
    fn test_prune_no_removal_does_not_bump_version() {
        let cid = Uuid::new_v4();
        let mut kg = KnowledgeGraph::new(cid);
        let a = make_node("A", NodeType::Concept, cid);
        let b = make_node("B", NodeType::Concept, cid);
        kg.insert_nodes_batch(vec![a.clone(), b.clone()]);
        kg.insert_edges_batch(vec![make_edge(a.id, b.id, 0.9, cid)]);

        let v_before = kg.version.load(Ordering::Relaxed);
        kg.prune_edges(0.5, 100);
        assert_eq!(kg.version.load(Ordering::Relaxed), v_before,
            "prune with no removals should not bump version");
    }

    // ── NodeType / EdgeType parsing ──────────────────────────────────────

    #[test]
    fn test_node_type_from_str_roundtrip() {
        let types = vec![
            ("person", NodeType::Person),
            ("organization", NodeType::Organization),
            ("location", NodeType::Location),
            ("concept", NodeType::Concept),
            ("event", NodeType::Event),
            ("document", NodeType::Document),
            ("chunk", NodeType::Chunk),
            ("topic", NodeType::Topic),
        ];
        for (s, expected) in types {
            assert_eq!(NodeType::from_str(s).unwrap(), expected);
            assert_eq!(expected.to_string(), s);
        }
    }

    #[test]
    fn test_node_type_custom_fallback() {
        let nt: NodeType = "custom_type".parse().unwrap();
        assert_eq!(nt, NodeType::Custom("custom_type".to_string()));
    }

    #[test]
    fn test_edge_type_from_str_roundtrip() {
        let types = vec![
            ("mentions", EdgeType::Mentions),
            ("relates_to", EdgeType::RelatesTo),
            ("works_at", EdgeType::WorksAt),
            ("founded", EdgeType::Founded),
            ("located_in", EdgeType::LocatedIn),
            ("participated_in", EdgeType::ParticipatedIn),
            ("belongs_to_topic", EdgeType::BelongsToTopic),
            ("derived_from", EdgeType::DerivedFrom),
            ("similar_to", EdgeType::SimilarTo),
            ("next", EdgeType::Next),
        ];
        for (s, expected) in types {
            assert_eq!(EdgeType::from_str(s).unwrap(), expected);
            assert_eq!(expected.to_string(), s);
        }
    }

    #[test]
    fn test_edge_type_custom_fallback() {
        let et: EdgeType = "acquired".parse().unwrap();
        assert_eq!(et, EdgeType::Custom("acquired".to_string()));
    }

    // ── SerializableGraph → KnowledgeGraph round-trip ───────────────────

    #[test]
    fn test_serializable_graph_roundtrip_preserves_adjacency() {
        let cid = Uuid::new_v4();
        let a = make_node("A", NodeType::Concept, cid);
        let b = make_node("B", NodeType::Concept, cid);
        let edge = make_edge(a.id, b.id, 0.9, cid);

        let sg = SerializableGraph {
            nodes: vec![a.clone(), b.clone()],
            edges: vec![edge.clone()],
        };
        let kg = KnowledgeGraph::from(sg);

        assert_eq!(kg.nodes.len(), 2);
        assert_eq!(kg.edges.len(), 1);
        assert!(kg.adjacency_out.get(&a.id).unwrap().iter().any(|(eid, tid)| *eid == edge.id && *tid == b.id));
        assert!(kg.adjacency_in.get(&b.id).unwrap().iter().any(|(eid, sid)| *eid == edge.id && *sid == a.id));
    }

    // ── Default implementations ─────────────────────────────────────────

    #[test]
    fn test_search_result_default() {
        let sr = SearchResult::default();
        assert_eq!(sr.chunk_id, Uuid::nil());
        assert_eq!(sr.doc_id, Uuid::nil());
        assert!(sr.text.is_empty());
        assert_eq!(sr.vector_score, 0.0);
        assert_eq!(sr.final_score, 0.0);
    }

    #[test]
    fn test_ingest_options_default() {
        let opts = IngestOptions::default();
        assert_eq!(opts.max_files, 10_000);
        assert_eq!(opts.max_depth, 5);
        assert_eq!(opts.chunk_size_tokens, 512);
        assert_eq!(opts.chunk_overlap_tokens, 50);
        assert!(!opts.ocr_enabled);
    }
}
