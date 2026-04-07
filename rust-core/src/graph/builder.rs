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
            if node.node_type.to_string() != candidate.entity_type {
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

pub enum Resolution {
    Merge {
        existing_id: Uuid,
        strategy: MergeStrategy,
    },
    NewNode,
}

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
) -> (Vec<GraphNode>, HashMap<String, Uuid>) {
    let mut new_nodes = Vec::new();
    let mut node_id_map: HashMap<String, Uuid> = HashMap::new();

    for entity in entities {
        let resolution = resolver.resolve(&entity, existing, embeddings);

        match resolution {
            Resolution::Merge { existing_id, .. } => {
                node_id_map.insert(entity.name.clone(), existing_id);

                if let Some(node) = existing.iter().find(|n| n.id == existing_id) {
                    let mut updated = node.clone();
                    merge_nodes(&mut updated, &entity);
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
                    created_at: Some(chrono::Utc::now()),
                    updated_at: Some(chrono::Utc::now()),
                };
                node_id_map.insert(entity.name.clone(), node.id);
                new_nodes.push(node);
            }
        }
    }

    (new_nodes, node_id_map)
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
            })
        })
        .collect()
}
