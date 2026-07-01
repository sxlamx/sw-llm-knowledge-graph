//! Graph export.

use crate::models::KnowledgeGraph;

pub fn export_graphml(graph: &KnowledgeGraph) -> String {
    let mut xml = String::from(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<graphml xmlns="http://graphml.graphdrawing.org/graphml">
  <graph id="G" edgedefault="directed">
"#,
    );

    for (id, node) in &graph.nodes {
        xml.push_str(&format!(
            "    <node id=\"{}\">\n      <data key=\"label\">{}</data>\n      <data key=\"type\">{}</data>\n    </node>\n",
            id, xml_escape(&node.label), node.node_type
        ));
    }

    for (id, edge) in &graph.edges {
        xml.push_str(&format!(
            "    <edge id=\"{}\" source=\"{}\" target=\"{}\">\n      <data key=\"predicate\">{}</data>\n      <data key=\"weight\">{}</data>\n    </edge>\n",
            id, edge.source, edge.target, xml_escape(&edge.predicate), edge.weight
        ));
    }

    xml.push_str("  </graph>\n</graphml>");
    xml
}

pub fn export_json(graph: &KnowledgeGraph) -> serde_json::Value {
    serde_json::json!({
        "nodes": graph.nodes.values().collect::<Vec<_>>(),
        "edges": graph.edges.values().collect::<Vec<_>>(),
        "stats": {
            "node_count": graph.node_count(),
            "edge_count": graph.edge_count(),
            "version": graph.version.load(std::sync::atomic::Ordering::Relaxed),
        }
    })
}

fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{EdgeType, GraphEdge, GraphNode, KnowledgeGraph, NodeType};
    use std::collections::HashMap;
    use uuid::Uuid;

    fn make_node(label: &str, cid: Uuid) -> GraphNode {
        GraphNode {
            id: Uuid::new_v4(),
            node_type: NodeType::Concept,
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

    #[test]
    fn test_export_graphml_valid_xml() {
        let cid = Uuid::new_v4();
        let mut kg = KnowledgeGraph::new(cid);
        let a = make_node("Alice", cid);
        let b = make_node("Bob", cid);
        kg.insert_nodes_batch(vec![a.clone(), b.clone()]);
        kg.insert_edges_batch(vec![make_edge(a.id, b.id, 0.8, cid)]);

        let xml = export_graphml(&kg);
        assert!(xml.starts_with("<?xml"), "should start with XML declaration");
        assert!(xml.contains("<graphml"), "should contain graphml root element");
        assert!(xml.contains(&a.id.to_string()), "should contain node A ID");
        assert!(xml.contains(&b.id.to_string()), "should contain node B ID");
        assert!(xml.contains("Alice"), "should contain node A label");
        assert!(xml.contains("Bob"), "should contain node B label");
    }

    #[test]
    fn test_export_graphml_escapes_special_chars() {
        let cid = Uuid::new_v4();
        let mut kg = KnowledgeGraph::new(cid);
        let node = GraphNode {
            id: Uuid::new_v4(),
            node_type: NodeType::Concept,
            label: "A & B < C > D \"E\" 'F'".to_string(),
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
        };
        kg.insert_nodes_batch(vec![node]);

        let xml = export_graphml(&kg);
        assert!(!xml.contains("A & B <"), "raw & and < should be escaped");
        assert!(xml.contains("&amp;"), "& should be escaped to &amp;");
        assert!(xml.contains("&lt;"), "< should be escaped to &lt;");
    }

    #[test]
    fn test_export_graphml_empty_graph() {
        let cid = Uuid::new_v4();
        let kg = KnowledgeGraph::new(cid);
        let xml = export_graphml(&kg);
        assert!(xml.contains("<graphml"));
        assert!(!xml.contains("<node"), "empty graph should have no nodes");
        assert!(!xml.contains("<edge"), "empty graph should have no edges");
    }

    #[test]
    fn test_export_json_contains_nodes_edges_stats() {
        let cid = Uuid::new_v4();
        let mut kg = KnowledgeGraph::new(cid);
        let a = make_node("A", cid);
        kg.insert_nodes_batch(vec![a.clone()]);

        let json = export_json(&kg);
        assert!(json.get("nodes").is_some(), "JSON should have 'nodes' key");
        assert!(json.get("edges").is_some(), "JSON should have 'edges' key");
        assert!(json.get("stats").is_some(), "JSON should have 'stats' key");

        let stats = json.get("stats").unwrap();
        assert_eq!(stats["node_count"], 1);
        assert_eq!(stats["edge_count"], 0);
    }

    #[test]
    fn test_export_json_version_in_stats() {
        let cid = Uuid::new_v4();
        let kg = KnowledgeGraph::new(cid);
        let json = export_json(&kg);
        let version = json.get("stats").unwrap().get("version").unwrap();
        assert_eq!(version.as_u64().unwrap(), 0, "new graph should have version 0");
    }
}
