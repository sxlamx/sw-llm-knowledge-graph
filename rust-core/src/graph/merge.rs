//! Deterministic merge strategies for graph nodes and edges.
//!
//! LLM-based strategies (llm_balanced, llm_prefer_first, llm_prefer_last) are
//! handled in Python via EntityMerger — Rust only handles the three deterministic
//! strategies that don't require LLM calls.

use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::models::{GraphEdge, GraphNode};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum DeterministicMergeStrategy {
    KeepFirst,
    KeepLast,
    FieldOverwrite,
}

impl DeterministicMergeStrategy {
    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            "keep_first" => Some(Self::KeepFirst),
            "keep_last" => Some(Self::KeepLast),
            "field_overwrite" => Some(Self::FieldOverwrite),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FieldConflict {
    pub field_name: String,
    pub existing_value: Option<serde_json::Value>,
    pub incoming_value: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MergeConflict {
    pub existing_id: Uuid,
    pub incoming_id: Uuid,
    pub dedup_key: String,
    pub item_type: String,
    pub field_conflicts: Vec<FieldConflict>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MergeReport {
    pub merged: usize,
    pub inserted: usize,
    pub conflicted: usize,
}

pub fn merge_nodes_deterministic(
    existing: &GraphNode,
    incoming: &GraphNode,
    strategy: &DeterministicMergeStrategy,
) -> GraphNode {
    match strategy {
        DeterministicMergeStrategy::KeepFirst => existing.clone(),
        DeterministicMergeStrategy::KeepLast => {
            let mut merged = incoming.clone();
            merged.id = existing.id;
            merged
        }
        DeterministicMergeStrategy::FieldOverwrite => {
            let mut merged = existing.clone();
            if incoming.description.is_some() && merged.description.is_none() {
                merged.description = incoming.description.clone();
            } else if incoming.description.is_some() && merged.description.is_some() {
                merged.description = Some(format!(
                    "{} | {}",
                    merged.description.as_ref().unwrap(),
                    incoming.description.as_ref().unwrap()
                ));
            }
            for alias in &incoming.aliases {
                if !merged.aliases.contains(alias) {
                    merged.aliases.push(alias.clone());
                }
            }
            if incoming.display_label.is_some() && merged.display_label.is_none() {
                merged.display_label = incoming.display_label.clone();
            }
            if !incoming.doc_origins.is_empty() {
                for origin in &incoming.doc_origins {
                    if !merged.doc_origins.contains(origin) {
                        merged.doc_origins.push(origin.clone());
                    }
                }
            }
            merged.confidence = (existing.confidence + incoming.confidence) / 2.0;
            if let Some(oc) = &incoming.ontology_class {
                if merged.ontology_class.is_none() {
                    merged.ontology_class = Some(oc.clone());
                }
            }
            for (k, v) in &incoming.properties {
                if !merged.properties.contains_key(k) {
                    merged.properties.insert(k.clone(), v.clone());
                }
            }
            merged
        }
    }
}

pub fn merge_edges_deterministic(
    existing: &GraphEdge,
    incoming: &GraphEdge,
    strategy: &DeterministicMergeStrategy,
) -> GraphEdge {
    match strategy {
        DeterministicMergeStrategy::KeepFirst => existing.clone(),
        DeterministicMergeStrategy::KeepLast => {
            let mut merged = incoming.clone();
            merged.id = existing.id;
            merged
        }
        DeterministicMergeStrategy::FieldOverwrite => {
            let mut merged = existing.clone();
            if incoming.context.is_some() && merged.context.is_none() {
                merged.context = incoming.context.clone();
            } else if incoming.context.is_some() && merged.context.is_some() {
                merged.context = Some(format!(
                    "{} | {}",
                    merged.context.as_ref().unwrap(),
                    incoming.context.as_ref().unwrap()
                ));
            }
            if !incoming.predicate.is_empty() && merged.predicate.is_empty() {
                merged.predicate = incoming.predicate.clone();
            }
            if incoming.time.is_some() && merged.time.is_none() {
                merged.time = incoming.time.clone();
            }
            if incoming.location.is_some() && merged.location.is_none() {
                merged.location = incoming.location.clone();
            }
            if let Some(ref p) = incoming.participants {
                if let Some(ref mut existing_p) = merged.participants {
                    for pid in p {
                        if !existing_p.contains(pid) {
                            existing_p.push(*pid);
                        }
                    }
                } else {
                    merged.participants = Some(p.clone());
                }
            }
            for origin in &incoming.doc_origins {
                if !merged.doc_origins.contains(origin) {
                    merged.doc_origins.push(*origin);
                }
            }
            if incoming.display_label.is_some() && merged.display_label.is_none() {
                merged.display_label = incoming.display_label.clone();
            }
            merged.weight = (existing.weight + incoming.weight) / 2.0;
            for (k, v) in &incoming.properties {
                if !merged.properties.contains_key(k) {
                    merged.properties.insert(k.clone(), v.clone());
                }
            }
            merged
        }
    }
}

pub fn detect_node_conflicts(
    existing_nodes: &[GraphNode],
    incoming_nodes: &[GraphNode],
) -> Vec<MergeConflict> {
    let mut dedup_map: std::collections::HashMap<String, Uuid> = std::collections::HashMap::new();
    for n in existing_nodes {
        if let Some(ref dk) = n.dedup_key {
            dedup_map.insert(dk.clone(), n.id);
        }
    }

    let mut conflicts = Vec::new();
    for inc in incoming_nodes {
        if let Some(ref dk) = inc.dedup_key {
            if let Some(&existing_id) = dedup_map.get(dk) {
                let existing = match existing_nodes.iter().find(|n| n.id == existing_id) {
                    Some(n) => n,
                    None => continue,
                };
                let field_conflicts = diff_node_fields(existing, inc);
                if !field_conflicts.is_empty() {
                    conflicts.push(MergeConflict {
                        existing_id,
                        incoming_id: inc.id,
                        dedup_key: dk.clone(),
                        item_type: "node".to_string(),
                        field_conflicts,
                    });
                }
            }
        }
    }
    conflicts
}

pub fn detect_edge_conflicts(
    existing_edges: &[GraphEdge],
    incoming_edges: &[GraphEdge],
) -> Vec<MergeConflict> {
    let mut dedup_map: std::collections::HashMap<String, Uuid> = std::collections::HashMap::new();
    for e in existing_edges {
        if let Some(ref dk) = e.dedup_key {
            dedup_map.insert(dk.clone(), e.id);
        }
    }

    let mut conflicts = Vec::new();
    for inc in incoming_edges {
        if let Some(ref dk) = inc.dedup_key {
            if let Some(&existing_id) = dedup_map.get(dk) {
                let existing = match existing_edges.iter().find(|e| e.id == existing_id) {
                    Some(e) => e,
                    None => continue,
                };
                let field_conflicts = diff_edge_fields(existing, inc);
                if !field_conflicts.is_empty() {
                    conflicts.push(MergeConflict {
                        existing_id,
                        incoming_id: inc.id,
                        dedup_key: dk.clone(),
                        item_type: "edge".to_string(),
                        field_conflicts,
                    });
                }
            }
        }
    }
    conflicts
}

pub fn diff_node_fields(a: &GraphNode, b: &GraphNode) -> Vec<FieldConflict> {
    let mut conflicts = Vec::new();
    if a.label != b.label {
        conflicts.push(FieldConflict {
            field_name: "label".to_string(),
            existing_value: Some(serde_json::Value::String(a.label.clone())),
            incoming_value: Some(serde_json::Value::String(b.label.clone())),
        });
    }
    if a.description != b.description {
        conflicts.push(FieldConflict {
            field_name: "description".to_string(),
            existing_value: a.description.as_ref().map(|s| serde_json::Value::String(s.clone())),
            incoming_value: b.description.as_ref().map(|s| serde_json::Value::String(s.clone())),
        });
    }
    if a.confidence != b.confidence {
        conflicts.push(FieldConflict {
            field_name: "confidence".to_string(),
            existing_value: Some(serde_json::json!(a.confidence)),
            incoming_value: Some(serde_json::json!(b.confidence)),
        });
    }
    if a.aliases != b.aliases {
        conflicts.push(FieldConflict {
            field_name: "aliases".to_string(),
            existing_value: Some(serde_json::json!(a.aliases)),
            incoming_value: Some(serde_json::json!(b.aliases)),
        });
    }
    if a.ontology_class != b.ontology_class {
        conflicts.push(FieldConflict {
            field_name: "ontology_class".to_string(),
            existing_value: a.ontology_class.as_ref().map(|s| serde_json::Value::String(s.clone())),
            incoming_value: b.ontology_class.as_ref().map(|s| serde_json::Value::String(s.clone())),
        });
    }
    if a.display_label != b.display_label {
        conflicts.push(FieldConflict {
            field_name: "display_label".to_string(),
            existing_value: a.display_label.as_ref().map(|s| serde_json::Value::String(s.clone())),
            incoming_value: b.display_label.as_ref().map(|s| serde_json::Value::String(s.clone())),
        });
    }
    conflicts
}

pub fn diff_edge_fields(a: &GraphEdge, b: &GraphEdge) -> Vec<FieldConflict> {
    let mut conflicts = Vec::new();
    if a.predicate != b.predicate {
        conflicts.push(FieldConflict {
            field_name: "predicate".to_string(),
            existing_value: Some(serde_json::Value::String(a.predicate.clone())),
            incoming_value: Some(serde_json::Value::String(b.predicate.clone())),
        });
    }
    if a.context != b.context {
        conflicts.push(FieldConflict {
            field_name: "context".to_string(),
            existing_value: a.context.as_ref().map(|s| serde_json::Value::String(s.clone())),
            incoming_value: b.context.as_ref().map(|s| serde_json::Value::String(s.clone())),
        });
    }
    if a.weight != b.weight {
        conflicts.push(FieldConflict {
            field_name: "weight".to_string(),
            existing_value: Some(serde_json::json!(a.weight)),
            incoming_value: Some(serde_json::json!(b.weight)),
        });
    }
    if a.time != b.time {
        conflicts.push(FieldConflict {
            field_name: "time".to_string(),
            existing_value: a.time.as_ref().map(|s| serde_json::Value::String(s.clone())),
            incoming_value: b.time.as_ref().map(|s| serde_json::Value::String(s.clone())),
        });
    }
    if a.location != b.location {
        conflicts.push(FieldConflict {
            field_name: "location".to_string(),
            existing_value: a.location.as_ref().map(|s| serde_json::Value::String(s.clone())),
            incoming_value: b.location.as_ref().map(|s| serde_json::Value::String(s.clone())),
        });
    }
    if a.display_label != b.display_label {
        conflicts.push(FieldConflict {
            field_name: "display_label".to_string(),
            existing_value: a.display_label.as_ref().map(|s| serde_json::Value::String(s.clone())),
            incoming_value: b.display_label.as_ref().map(|s| serde_json::Value::String(s.clone())),
        });
    }
    conflicts
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use crate::models::{NodeType, EdgeType};

    fn make_node(id: Uuid, label: &str, desc: Option<&str>, aliases: Vec<&str>, confidence: f32) -> GraphNode {
        GraphNode {
            id, node_type: NodeType::Person, label: label.to_string(),
            description: desc.map(|s| s.to_string()), aliases: aliases.iter().map(|s| s.to_string()).collect(),
            confidence, ontology_class: None, properties: HashMap::new(),
            collection_id: Uuid::new_v4(), display_label: None, dedup_key: None,
            doc_origins: vec![], created_at: None, updated_at: None,
        }
    }

    fn make_edge(id: Uuid, source: Uuid, target: Uuid, weight: f32) -> GraphEdge {
        GraphEdge {
            id, source, target, edge_type: EdgeType::RelatesTo, weight,
            context: None, chunk_id: None, properties: HashMap::new(),
            collection_id: Uuid::new_v4(), display_label: None, dedup_key: None,
            predicate: String::new(), time: None, location: None,
            participants: None, doc_origins: vec![],
        }
    }

    #[test]
    fn test_keep_first_preserves_existing() {
        let existing_id = Uuid::new_v4();
        let existing = make_node(existing_id, "Alice", Some("Original"), vec![], 0.8);
        let incoming = make_node(Uuid::new_v4(), "Alice", Some("Updated"), vec![], 0.9);
        let result = merge_nodes_deterministic(&existing, &incoming, &DeterministicMergeStrategy::KeepFirst);
        assert_eq!(result.description, Some("Original".to_string()));
        assert_eq!(result.id, existing_id);
    }

    #[test]
    fn test_keep_last_preserves_id() {
        let existing_id = Uuid::new_v4();
        let existing = make_node(existing_id, "Alice", Some("Original"), vec![], 0.8);
        let incoming = make_node(Uuid::new_v4(), "Alice", Some("Updated"), vec![], 0.9);
        let result = merge_nodes_deterministic(&existing, &incoming, &DeterministicMergeStrategy::KeepLast);
        assert_eq!(result.id, existing_id);
        assert_eq!(result.description, Some("Updated".to_string()));
    }

    #[test]
    fn test_field_overwrite_fills_nulls() {
        let existing = make_node(Uuid::new_v4(), "Alice", None, vec![], 0.8);
        let incoming = make_node(Uuid::new_v4(), "Alice", Some("A person"), vec![], 0.6);
        let result = merge_nodes_deterministic(&existing, &incoming, &DeterministicMergeStrategy::FieldOverwrite);
        assert_eq!(result.description, Some("A person".to_string()));
    }

    #[test]
    fn test_field_overwrite_appends_aliases() {
        let existing = make_node(Uuid::new_v4(), "Alice", Some("Original"), vec!["Al"], 0.8);
        let incoming = make_node(Uuid::new_v4(), "Alice", Some("Updated"), vec!["Ali"], 0.6);
        let result = merge_nodes_deterministic(&existing, &incoming, &DeterministicMergeStrategy::FieldOverwrite);
        assert!(result.aliases.contains(&"Al".to_string()));
        assert!(result.aliases.contains(&"Ali".to_string()));
    }

    #[test]
    fn test_field_overwrite_averages_confidence() {
        let existing = make_node(Uuid::new_v4(), "Alice", None, vec![], 0.8);
        let incoming = make_node(Uuid::new_v4(), "Alice", None, vec![], 0.6);
        let result = merge_nodes_deterministic(&existing, &incoming, &DeterministicMergeStrategy::FieldOverwrite);
        assert!((result.confidence - 0.7).abs() < 0.01);
    }

    #[test]
    fn test_does_not_mutate_inputs() {
        let existing = make_node(Uuid::new_v4(), "Alice", Some("Original"), vec![], 0.8);
        let incoming = make_node(Uuid::new_v4(), "Alice", Some("Updated"), vec![], 0.9);
        let _result = merge_nodes_deterministic(&existing, &incoming, &DeterministicMergeStrategy::KeepLast);
        assert_eq!(existing.description, Some("Original".to_string()));
    }

    #[test]
    fn test_detect_no_conflicts() {
        let existing = vec![make_node(Uuid::new_v4(), "Alice", Some("Original"), vec![], 0.8)];
        let incoming = vec![make_node(Uuid::new_v4(), "Bob", Some("New person"), vec![], 0.7)];
        let conflicts = detect_node_conflicts(&existing, &incoming);
        assert!(conflicts.is_empty());
    }

    #[test]
    fn test_detect_node_conflicts_on_label_diff() {
        let mut existing = make_node(Uuid::new_v4(), "Alice", Some("Same"), vec![], 0.9);
        existing.dedup_key = Some("dk1".to_string());
        let mut incoming = make_node(Uuid::new_v4(), "Bob", Some("Same"), vec![], 0.9);
        incoming.dedup_key = Some("dk1".to_string());
        let conflicts = detect_node_conflicts(&[existing], &[incoming]);
        assert_eq!(conflicts.len(), 1);
        assert_eq!(conflicts[0].field_conflicts[0].field_name, "label");
    }

    #[test]
    fn test_field_overwrite_no_duplicate_aliases() {
        let mut existing = make_node(Uuid::new_v4(), "Alice", None, vec!["Al", "Ali"], 0.8);
        existing.dedup_key = Some("dk1".to_string());
        let mut incoming = make_node(Uuid::new_v4(), "Alice", None, vec!["Al", "Alicia"], 0.6);
        incoming.dedup_key = Some("dk1".to_string());
        let result = merge_nodes_deterministic(&existing, &incoming, &DeterministicMergeStrategy::FieldOverwrite);
        assert_eq!(result.aliases.iter().filter(|a| *a == "Al").count(), 1);
        assert!(result.aliases.contains(&"Ali".to_string()));
        assert!(result.aliases.contains(&"Alicia".to_string()));
    }

    #[test]
    fn test_field_overwrite_appends_doc_origins() {
        let mut existing = make_node(Uuid::new_v4(), "Alice", None, vec![], 0.8);
        let doc1 = Uuid::new_v4();
        existing.doc_origins = vec![doc1];
        let mut incoming = make_node(Uuid::new_v4(), "Alice", None, vec![], 0.6);
        let doc2 = Uuid::new_v4();
        incoming.doc_origins = vec![doc2];
        let result = merge_nodes_deterministic(&existing, &incoming, &DeterministicMergeStrategy::FieldOverwrite);
        assert!(result.doc_origins.contains(&doc1));
        assert!(result.doc_origins.contains(&doc2));
    }

    #[test]
    fn test_edge_field_overwrite_unions_participants() {
        let sid = Uuid::new_v4();
        let tid = Uuid::new_v4();
        let pid1 = Uuid::new_v4();
        let pid2 = Uuid::new_v4();
        let mut existing = make_edge(Uuid::new_v4(), sid, tid, 0.8);
        existing.participants = Some(vec![pid1]);
        let mut incoming = make_edge(Uuid::new_v4(), sid, tid, 0.5);
        incoming.participants = Some(vec![pid2]);
        let result = merge_edges_deterministic(&existing, &incoming, &DeterministicMergeStrategy::FieldOverwrite);
        let participants = result.participants.unwrap();
        assert!(participants.contains(&pid1));
        assert!(participants.contains(&pid2));
    }

    #[test]
    fn test_edge_field_overwrite_averages_weight() {
        let sid = Uuid::new_v4();
        let tid = Uuid::new_v4();
        let existing = make_edge(Uuid::new_v4(), sid, tid, 0.8);
        let incoming = make_edge(Uuid::new_v4(), sid, tid, 0.6);
        let result = merge_edges_deterministic(&existing, &incoming, &DeterministicMergeStrategy::FieldOverwrite);
        assert!((result.weight - 0.7).abs() < 0.01);
    }

    #[test]
    fn test_edge_field_overwrite_preserves_existing_predicate() {
        let sid = Uuid::new_v4();
        let tid = Uuid::new_v4();
        let mut existing = make_edge(Uuid::new_v4(), sid, tid, 0.8);
        existing.predicate = "works_at".to_string();
        let incoming = make_edge(Uuid::new_v4(), sid, tid, 0.5);
        let result = merge_edges_deterministic(&existing, &incoming, &DeterministicMergeStrategy::FieldOverwrite);
        assert_eq!(result.predicate, "works_at");
    }

    #[test]
    fn test_edge_field_overwrite_appends_doc_origins() {
        let sid = Uuid::new_v4();
        let tid = Uuid::new_v4();
        let doc1 = Uuid::new_v4();
        let doc2 = Uuid::new_v4();
        let mut existing = make_edge(Uuid::new_v4(), sid, tid, 0.8);
        existing.doc_origins = vec![doc1];
        let mut incoming = make_edge(Uuid::new_v4(), sid, tid, 0.5);
        incoming.doc_origins = vec![doc2];
        let result = merge_edges_deterministic(&existing, &incoming, &DeterministicMergeStrategy::FieldOverwrite);
        assert!(result.doc_origins.contains(&doc1));
        assert!(result.doc_origins.contains(&doc2));
    }

    #[test]
    fn test_edge_keep_first_preserves_existing() {
        let sid = Uuid::new_v4();
        let tid = Uuid::new_v4();
        let mut existing = make_edge(Uuid::new_v4(), sid, tid, 0.8);
        existing.context = Some("original ctx".to_string());
        let mut incoming = make_edge(Uuid::new_v4(), sid, tid, 0.5);
        incoming.context = Some("updated ctx".to_string());
        let result = merge_edges_deterministic(&existing, &incoming, &DeterministicMergeStrategy::KeepFirst);
        assert_eq!(result.context, Some("original ctx".to_string()));
        assert_eq!(result.weight, 0.8);
    }

    #[test]
    fn test_from_str_roundtrip() {
        assert_eq!(DeterministicMergeStrategy::from_str("keep_first"), Some(DeterministicMergeStrategy::KeepFirst));
        assert_eq!(DeterministicMergeStrategy::from_str("keep_last"), Some(DeterministicMergeStrategy::KeepLast));
        assert_eq!(DeterministicMergeStrategy::from_str("field_overwrite"), Some(DeterministicMergeStrategy::FieldOverwrite));
        assert_eq!(DeterministicMergeStrategy::from_str("invalid"), None);
    }
}