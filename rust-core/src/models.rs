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
        for node in nodes {
            self.nodes.insert(node.id, node);
        }
        self.version
            .fetch_add(1, std::sync::atomic::Ordering::Release);
    }

    pub fn insert_edges_batch(&mut self, edges: Vec<GraphEdge>) {
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
    pub doc_origins: Vec<String>,
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
