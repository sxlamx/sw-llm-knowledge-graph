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
            id, edge.source, edge.target, edge.edge_type, edge.weight
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
