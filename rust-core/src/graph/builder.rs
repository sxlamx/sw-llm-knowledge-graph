//! Graph operations — builder, resolution.

use crate::graph::traversal::{cosine_similarity, normalize_name};
use crate::models::{
    EdgeType, ExtractedEntity, ExtractedRelationship, GraphEdge, GraphNode, NodeType,
};
use std::collections::HashMap;
use uuid::Uuid;

pub struct EntityResolver {
    levenshtein_threshold: usize,
    embedding_threshold: f32,
}

impl EntityResolver {
    pub fn new() -> Self {
        Self {
            levenshtein_threshold: 3,
            embedding_threshold: 0.92,
        }
    }

    pub fn resolve(
        &self,
        candidate: &ExtractedEntity,
        existing_nodes: &[GraphNode],
        embeddings: &HashMap<String, Vec<f32>>,
    ) -> Resolution {
        let normalized = normalize_name(&candidate.name);

        if let Some(node) = existing_nodes.iter().find(|n| {
            normalize_name(&n.label) == normalized
                || n.aliases.iter().any(|a| normalize_name(a) == normalized)
        }) {
            return Resolution::Merge {
                existing_id: node.id,
                strategy: MergeStrategy::ExactMatch,
            };
        }

        let candidate_emb = embeddings.get(&candidate.name).cloned().unwrap_or_default();

        for node in existing_nodes {
            if node.node_type.to_string().to_lowercase()
                != candidate.entity_type.to_lowercase()
            {
                continue;
            }

            let dist = strsim::levenshtein(&normalized, &normalize_name(&node.label));
            if dist < self.levenshtein_threshold {
                let node_emb = embeddings.get(&node.label).cloned().unwrap_or_default();
                if !candidate_emb.is_empty() && !node_emb.is_empty() {
                    let cos_sim = cosine_similarity(&candidate_emb, &node_emb);
                    if cos_sim > self.embedding_threshold {
                        return Resolution::Merge {
                            existing_id: node.id,
                            strategy: MergeStrategy::FuzzyMatch {
                                distance: dist,
                                cosine_sim: cos_sim,
                            },
                        };
                    }
                }
            }
        }

        Resolution::NewNode
    }
}

impl Default for EntityResolver {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Debug)]
pub enum Resolution {
    Merge {
        existing_id: Uuid,
        strategy: MergeStrategy,
    },
    NewNode,
}

#[derive(Debug)]
pub enum MergeStrategy {
    ExactMatch,
    FuzzyMatch { distance: usize, cosine_sim: f32 },
}

pub fn merge_nodes(canonical: &mut GraphNode, incoming: &ExtractedEntity) {
    if !canonical.aliases.contains(&incoming.name) {
        canonical.aliases.push(incoming.name.clone());
    }

    let new_aliases: Vec<_> = incoming
        .aliases
        .iter()
        .filter(|a| !canonical.aliases.contains(a))
        .cloned()
        .collect();
    canonical.aliases.extend(new_aliases);

    canonical.confidence = (canonical.confidence + incoming.confidence) / 2.0;

    if incoming.description.len() > canonical.description.as_deref().unwrap_or("").len() {
        canonical.description = Some(incoming.description.clone());
    }
}

pub fn build_graph_nodes(
    entities: Vec<ExtractedEntity>,
    collection_id: Uuid,
    existing: &[GraphNode],
    embeddings: &HashMap<String, Vec<f32>>,
    resolver: &EntityResolver,
) -> (Vec<GraphNode>, Vec<GraphNode>, HashMap<String, Uuid>) {
    let mut new_nodes = Vec::new();
    let mut merged_nodes = Vec::new();
    let mut node_id_map: HashMap<String, Uuid> = HashMap::new();

    for entity in entities {
        let resolution = resolver.resolve(&entity, existing, embeddings);

        match resolution {
            Resolution::Merge { existing_id, .. } => {
                node_id_map.insert(entity.name.clone(), existing_id);

                if let Some(node) = existing.iter().find(|n| n.id == existing_id) {
                    let mut updated = node.clone();
                    merge_nodes(&mut updated, &entity);
                    merged_nodes.push(updated);
                }
            }
            Resolution::NewNode => {
                let node_type = entity.entity_type.parse().unwrap_or(NodeType::Concept);
                let node = GraphNode {
                    id: Uuid::new_v4(),
                    node_type,
                    label: entity.name.clone(),
                    description: Some(entity.description.clone()),
                    aliases: entity.aliases.clone(),
                    confidence: entity.confidence,
                    ontology_class: None,
                    properties: HashMap::new(),
                    collection_id,
                    display_label: None,
                    dedup_key: None,
                    doc_origins: vec![],
                    created_at: Some(chrono::Utc::now()),
                    updated_at: Some(chrono::Utc::now()),
                };
                node_id_map.insert(entity.name.clone(), node.id);
                new_nodes.push(node);
            }
        }
    }

    (new_nodes, merged_nodes, node_id_map)
}

pub fn build_graph_edges(
    relationships: Vec<ExtractedRelationship>,
    node_id_map: &HashMap<String, Uuid>,
    collection_id: Uuid,
    chunk_id: Option<Uuid>,
) -> Vec<GraphEdge> {
    relationships
        .into_iter()
        .filter_map(|rel| {
            let source_id = node_id_map.get(&rel.source)?;
            let target_id = node_id_map.get(&rel.target)?;
            Some(GraphEdge {
                id: Uuid::new_v4(),
                source: *source_id,
                target: *target_id,
                edge_type: rel.predicate.parse().unwrap_or(EdgeType::RelatesTo),
                weight: rel.confidence,
                context: Some(rel.context.clone()),
                chunk_id,
                properties: HashMap::new(),
                collection_id,
                display_label: None,
                dedup_key: None,
                predicate: rel.predicate.clone(),
                time: None,
                location: None,
                participants: None,
                doc_origins: vec![],
            })
         })
         .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{NodeType, KnowledgeGraph, EdgeType};

    fn make_entity(name: &str, entity_type: &str, confidence: f32) -> ExtractedEntity {
        ExtractedEntity {
            name: name.to_string(),
            entity_type: entity_type.to_string(),
            description: name.to_string(),
            aliases: vec![],
            confidence,
        }
    }

    fn make_node(id: Uuid, label: &str, node_type: NodeType, cid: Uuid) -> GraphNode {
        GraphNode {
            id,
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

    fn make_edge(source: Uuid, target: Uuid, weight: f32, cid: Uuid) -> GraphEdge {
        GraphEdge {
            id: Uuid::new_v4(), source, target, edge_type: EdgeType::RelatesTo, weight,
            context: None, chunk_id: None, properties: HashMap::new(),
            collection_id: cid, display_label: None, dedup_key: None,
            predicate: String::new(), time: None, location: None,
            participants: None, doc_origins: vec![],
        }
    }

    #[test]
    fn test_exact_match_case_insensitive() {
        let resolver = EntityResolver::new();
        let node_id = Uuid::new_v4();
        let node = make_node(node_id, "Apple Inc", NodeType::Organization, Uuid::nil());
        let candidate = make_entity("apple inc", "ORGANIZATION", 0.8);
        let result = resolver.resolve(&candidate, &[node.clone()], &HashMap::new());
        match result {
            Resolution::Merge { strategy: MergeStrategy::ExactMatch, .. } => {}
            other => panic!("Expected ExactMatch, got {:?}", other),
        }
    }

    #[test]
    fn test_exact_match_via_alias() {
        let resolver = EntityResolver::new();
        let node_id = Uuid::new_v4();
        let mut node = make_node(node_id, "Apple Inc", NodeType::Organization, Uuid::nil());
        node.aliases = vec!["OAI".to_string(), "Apple".to_string()];
        let candidate = make_entity("OAI", "ORGANIZATION", 0.8);
        let result = resolver.resolve(&candidate, &[node.clone()], &HashMap::new());
        match result {
            Resolution::Merge { strategy: MergeStrategy::ExactMatch, .. } => {}
            other => panic!("Expected ExactMatch via alias, got {:?}", other),
        }
    }

    #[test]
    fn test_entity_type_case_insensitive_comparison() {
        let resolver = EntityResolver::new();
        let node_id = Uuid::new_v4();
        let node = make_node(node_id, "Apple Inc", NodeType::Organization, Uuid::nil());
        let candidate = make_entity("Apple Inc", "ORGANIZATION", 0.8);
        let result = resolver.resolve(&candidate, &[node.clone()], &HashMap::new());
        assert!(matches!(result, Resolution::Merge { .. }));
    }

    #[test]
    fn test_no_merge_different_entity_type() {
        let resolver = EntityResolver::new();
        let node_id = Uuid::new_v4();
        let node = make_node(node_id, "Apple Inc", NodeType::Location, Uuid::nil());
        let candidate = make_entity("Apple Inc", "ORGANIZATION", 0.8);
        let result = resolver.resolve(&candidate, &[node.clone()], &HashMap::new());
        assert!(matches!(result, Resolution::NewNode));
    }

    #[test]
    fn test_levenshtein_threshold_strictly_less_than_3() {
        let resolver = EntityResolver::new();
        let node_id = Uuid::new_v4();
        let node = make_node(node_id, "Apple Inc", NodeType::Organization, Uuid::nil());
        let mut embeddings = HashMap::new();
        embeddings.insert("Apple Inc".to_string(), vec![0.1f32; 16]);
        let mut candidate_emb_map = HashMap::new();
        candidate_emb_map.insert("Aple Inc".to_string(), vec![0.1f32; 16]);
        let candidate = ExtractedEntity {
            name: "Aple Inc".to_string(),
            entity_type: "ORGANIZATION".to_string(),
            description: String::new(),
            aliases: vec![],
            confidence: 0.8,
        };
        let result = resolver.resolve(&candidate, &[node.clone()], &embeddings);
        assert!(matches!(result, Resolution::Merge { .. }));
    }

    #[test]
    fn test_levenshtein_distance_exactly_3_skipped() {
        let resolver = EntityResolver::new();
        let node_id = Uuid::new_v4();
        let node = make_node(node_id, "Apple Inc", NodeType::Organization, Uuid::nil());
        let mut embeddings = HashMap::new();
        embeddings.insert("Apple Inc".to_string(), vec![1.0f32; 16]);
        let candidate = ExtractedEntity {
            name: "Appla Incc".to_string(),
            entity_type: "ORGANIZATION".to_string(),
            description: String::new(),
            aliases: vec![],
            confidence: 0.8,
        };
        let result = resolver.resolve(&candidate, &[node.clone()], &embeddings);
        assert!(matches!(result, Resolution::NewNode));
    }

    #[test]
    fn test_no_merge_below_cosine_threshold() {
        let resolver = EntityResolver::new();
        let node_id = Uuid::new_v4();
        let node = make_node(node_id, "Apple Inc", NodeType::Organization, Uuid::nil());
        let candidate = ExtractedEntity {
            name: "Aple Inc".to_string(),
            entity_type: "ORGANIZATION".to_string(),
            description: String::new(),
            aliases: vec![],
            confidence: 0.8,
        };
        let candidate_emb_map: HashMap<String, Vec<f32>> = HashMap::new();
        let result = resolver.resolve(&candidate, &[node.clone()], &candidate_emb_map);
        assert!(matches!(result, Resolution::NewNode));
    }

    #[test]
    fn test_merge_unions_aliases() {
        let mut canonical = GraphNode {
            id: Uuid::new_v4(),
            node_type: NodeType::Organization,
            label: "Apple Inc".to_string(),
            description: Some("Tech company".to_string()),
            aliases: vec!["OAI".to_string()],
            confidence: 0.9,
            ontology_class: None,
            properties: HashMap::new(),
            collection_id: Uuid::nil(),
            display_label: None,
            dedup_key: None,
            doc_origins: vec![],
            created_at: None,
            updated_at: None,
        };
        let incoming = ExtractedEntity {
            name: "Apple".to_string(),
            entity_type: "ORGANIZATION".to_string(),
            description: "A technology company".to_string(),
            aliases: vec!["Apple Corp".to_string()],
            confidence: 0.8,
        };
        merge_nodes(&mut canonical, &incoming);
        assert!(canonical.aliases.contains(&"OAI".to_string()));
        assert!(canonical.aliases.contains(&"Apple".to_string()));
        assert!(canonical.aliases.contains(&"Apple Corp".to_string()));
        assert_eq!(canonical.aliases.len(), 3);
    }

    #[test]
    fn test_merge_averages_confidence() {
        let mut canonical = GraphNode {
            id: Uuid::new_v4(),
            node_type: NodeType::Person,
            label: "Alice".to_string(),
            description: None,
            aliases: vec![],
            confidence: 0.8,
            ontology_class: None,
            properties: HashMap::new(),
            collection_id: Uuid::nil(),
            display_label: None,
            dedup_key: None,
            doc_origins: vec![],
            created_at: None,
            updated_at: None,
        };
        let incoming = ExtractedEntity {
            name: "Alice".to_string(),
            entity_type: "PERSON".to_string(),
            description: String::new(),
            aliases: vec![],
            confidence: 0.6,
        };
        merge_nodes(&mut canonical, &incoming);
        let expected = (0.8 + 0.6) / 2.0;
        assert!((canonical.confidence - expected).abs() < 0.001);
    }

    #[test]
    fn test_merge_preserves_longer_description() {
        let shorter = GraphNode {
            id: Uuid::new_v4(),
            node_type: NodeType::Person,
            label: "Alice".to_string(),
            description: Some("Short".to_string()),
            aliases: vec![],
            confidence: 0.8,
            ontology_class: None,
            properties: HashMap::new(),
            collection_id: Uuid::nil(),
            display_label: None,
            dedup_key: None,
            doc_origins: vec![],
            created_at: None,
            updated_at: None,
        };
        let mut canonical = shorter;
        let incoming = ExtractedEntity {
            name: "Alice".to_string(),
            entity_type: "PERSON".to_string(),
            description: "A longer description of Alice".to_string(),
            aliases: vec![],
            confidence: 0.6,
        };
        merge_nodes(&mut canonical, &incoming);
        assert_eq!(canonical.description.as_deref().unwrap(), "A longer description of Alice");
    }

    #[test]
    fn test_new_node_when_no_match() {
        let resolver = EntityResolver::new();
        let existing: Vec<GraphNode> = vec![];
        let candidate = make_entity("Unknown Corp", "ORGANIZATION", 0.8);
        let result = resolver.resolve(&candidate, &existing, &HashMap::new());
        assert!(matches!(result, Resolution::NewNode));
    }

    // ── prune_dangling_edges tests ──────────────────────────────────────

    #[test]
    fn test_prune_removes_binary_dangling_edges() {
        let cid = Uuid::new_v4();
        let mut kg = KnowledgeGraph::new(cid);
        let a = make_node(Uuid::new_v4(), "A", NodeType::Person, cid);
        let b = make_node(Uuid::new_v4(), "B", NodeType::Organization, cid);
        kg.insert_nodes_batch(vec![a.clone(), b.clone()]);

        let valid_edge = make_edge(a.id, b.id, 0.8, cid);
        let fake_id = Uuid::new_v4();
        let dangling_edge = GraphEdge {
            id: Uuid::new_v4(),
            source: fake_id,
            target: b.id,
            edge_type: EdgeType::RelatesTo,
            weight: 0.5,
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
        };
        kg.insert_edges_batch(vec![valid_edge.clone(), dangling_edge.clone()]);

        let pruned = kg.prune_dangling_edges();
        assert_eq!(pruned, 1);
        assert_eq!(kg.edge_count(), 1);
        assert!(kg.edges.contains_key(&valid_edge.id));
        assert!(!kg.edges.contains_key(&dangling_edge.id));
    }

    #[test]
    fn test_prune_removes_hyperedge_with_dangling_participant() {
        let cid = Uuid::new_v4();
        let mut kg = KnowledgeGraph::new(cid);
        let a = make_node(Uuid::new_v4(), "A", NodeType::Person, cid);
        let b = make_node(Uuid::new_v4(), "B", NodeType::Person, cid);
        let fake = Uuid::new_v4();
        kg.insert_nodes_batch(vec![a.clone(), b.clone()]);

        let valid_hyper = GraphEdge {
            id: Uuid::new_v4(),
            source: a.id,
            target: b.id,
            edge_type: EdgeType::ParticipatedIn,
            weight: 0.9,
            context: None,
            chunk_id: None,
            properties: HashMap::new(),
            collection_id: cid,
            display_label: None,
            dedup_key: None,
            predicate: String::new(),
            time: None,
            location: None,
            participants: Some(vec![a.id, b.id]),
            doc_origins: vec![],
        };
        let dangling_hyper = GraphEdge {
            id: Uuid::new_v4(),
            source: a.id,
            target: b.id,
            edge_type: EdgeType::ParticipatedIn,
            weight: 0.7,
            context: None,
            chunk_id: None,
            properties: HashMap::new(),
            collection_id: cid,
            display_label: None,
            dedup_key: None,
            predicate: String::new(),
            time: None,
            location: None,
            participants: Some(vec![a.id, fake]),
            doc_origins: vec![],
        };
        kg.insert_edges_batch(vec![valid_hyper.clone(), dangling_hyper.clone()]);

        let pruned = kg.prune_dangling_edges();
        assert_eq!(pruned, 1);
        assert_eq!(kg.edge_count(), 1);
        assert!(kg.edges.contains_key(&valid_hyper.id));
        assert!(!kg.edges.contains_key(&dangling_hyper.id));
    }

    #[test]
    fn test_prune_preserves_all_valid_binary_edges() {
        let cid = Uuid::new_v4();
        let mut kg = KnowledgeGraph::new(cid);
        let a = make_node(Uuid::new_v4(), "A", NodeType::Concept, cid);
        let b = make_node(Uuid::new_v4(), "B", NodeType::Concept, cid);
        let c = make_node(Uuid::new_v4(), "C", NodeType::Concept, cid);
        kg.insert_nodes_batch(vec![a.clone(), b.clone(), c.clone()]);
        kg.insert_edges_batch(vec![
            make_edge(a.id, b.id, 0.8, cid),
            make_edge(b.id, c.id, 0.7, cid),
            make_edge(a.id, c.id, 0.6, cid),
        ]);

        let pruned = kg.prune_dangling_edges();
        assert_eq!(pruned, 0);
        assert_eq!(kg.edge_count(), 3);
    }

    #[test]
    fn test_prune_empty_graph() {
        let cid = Uuid::new_v4();
        let mut kg = KnowledgeGraph::new(cid);
        let pruned = kg.prune_dangling_edges();
        assert_eq!(pruned, 0);
    }

    #[test]
    fn test_prune_rebuilds_adjacency() {
        let cid = Uuid::new_v4();
        let mut kg = KnowledgeGraph::new(cid);
        let a = make_node(Uuid::new_v4(), "A", NodeType::Concept, cid);
        let b = make_node(Uuid::new_v4(), "B", NodeType::Concept, cid);
        let c = make_node(Uuid::new_v4(), "C", NodeType::Concept, cid);
        let d = make_node(Uuid::new_v4(), "D", NodeType::Concept, cid);
        kg.insert_nodes_batch(vec![a.clone(), b.clone(), c.clone(), d.clone()]);

        let keep_edge = make_edge(a.id, b.id, 0.8, cid);
        let drop_edge = make_edge(c.id, d.id, 0.5, cid);
        kg.insert_edges_batch(vec![keep_edge.clone(), drop_edge.clone()]);

        let _ = kg.prune_dangling_edges();

        let adj_out_a = kg.adjacency_out.get(&a.id).unwrap();
        assert!(adj_out_a.iter().any(|(eid, _)| *eid == keep_edge.id));
        assert!(!adj_out_a.iter().any(|(eid, _)| *eid == drop_edge.id));
    }
}
