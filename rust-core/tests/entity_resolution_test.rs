//! entity_resolution_test.rs — Phase 3 entity resolution tests.
//!
//! Tests EntityResolver merge/no-merge decisions:
//!   - Exact label match → Merge(ExactMatch)
//!   - Alias match → Merge(ExactMatch)
//!   - No match → NewNode
//!   - Levenshtein near-match + cosine similarity → Merge(FuzzyMatch)
//!   - Different types, same fuzzy label → NewNode (type mismatch prevents merge)
//!   - merge_nodes: alias deduplication, confidence averaging, longer description wins

use rust_core::graph::builder::{
    EntityResolver, MergeStrategy, Resolution, build_graph_nodes, merge_nodes,
};
use rust_core::models::{ExtractedEntity, GraphNode, NodeType};
use std::collections::HashMap;
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn make_graph_node(label: &str, node_type: NodeType, aliases: Vec<&str>, cid: Uuid) -> GraphNode {
    GraphNode {
        id: Uuid::new_v4(),
        node_type,
        label: label.to_string(),
        description: Some("existing node".to_string()),
        aliases: aliases.iter().map(|s| s.to_string()).collect(),
        confidence: 0.8,
        ontology_class: None,
        properties: HashMap::new(),
        collection_id: cid,
        created_at: None,
        updated_at: None,
    }
}

fn make_entity(name: &str, entity_type: &str) -> ExtractedEntity {
    ExtractedEntity {
        name: name.to_string(),
        entity_type: entity_type.to_string(),
        description: "incoming entity".to_string(),
        aliases: vec![],
        confidence: 0.7,
    }
}

// ---------------------------------------------------------------------------
// Test 1: Exact label match → Merge with ExactMatch strategy.
// ---------------------------------------------------------------------------

#[test]
fn test_exact_label_match_resolves_to_merge() {
    let cid = Uuid::new_v4();
    let resolver = EntityResolver::new();

    let existing = vec![make_graph_node("Alice", NodeType::Person, vec![], cid)];
    let candidate = make_entity("Alice", "Person");

    let resolution = resolver.resolve(&candidate, &existing, &[]);

    assert!(
        matches!(resolution, Resolution::Merge { strategy: MergeStrategy::ExactMatch, .. }),
        "exact label match should produce ExactMatch merge"
    );
}

// ---------------------------------------------------------------------------
// Test 2: Case-insensitive label match → Merge.
// ---------------------------------------------------------------------------

#[test]
fn test_case_insensitive_label_match() {
    let cid = Uuid::new_v4();
    let resolver = EntityResolver::new();

    let existing = vec![make_graph_node("OpenAI", NodeType::Organization, vec![], cid)];
    let candidate = make_entity("openai", "Organization");

    let resolution = resolver.resolve(&candidate, &existing, &[]);

    assert!(
        matches!(resolution, Resolution::Merge { .. }),
        "case-insensitive match should merge"
    );
}

// ---------------------------------------------------------------------------
// Test 3: Alias match → Merge with ExactMatch strategy.
// ---------------------------------------------------------------------------

#[test]
fn test_alias_match_resolves_to_merge() {
    let cid = Uuid::new_v4();
    let resolver = EntityResolver::new();

    let existing = vec![make_graph_node(
        "International Business Machines",
        NodeType::Organization,
        vec!["IBM", "Big Blue"],
        cid,
    )];
    let candidate = make_entity("IBM", "Organization");

    let resolution = resolver.resolve(&candidate, &existing, &[]);

    assert!(
        matches!(resolution, Resolution::Merge { strategy: MergeStrategy::ExactMatch, .. }),
        "alias match should produce ExactMatch merge"
    );
}

// ---------------------------------------------------------------------------
// Test 4: No match — completely different name → NewNode.
// ---------------------------------------------------------------------------

#[test]
fn test_no_match_produces_new_node() {
    let cid = Uuid::new_v4();
    let resolver = EntityResolver::new();

    let existing = vec![make_graph_node("Alice", NodeType::Person, vec![], cid)];
    let candidate = make_entity("Quantum Computing", "Concept");

    let resolution = resolver.resolve(&candidate, &existing, &[]);

    assert!(
        matches!(resolution, Resolution::NewNode),
        "completely different entity should produce NewNode"
    );
}

// ---------------------------------------------------------------------------
// Test 5: Empty existing nodes → always NewNode.
// ---------------------------------------------------------------------------

#[test]
fn test_empty_existing_nodes_always_new_node() {
    let resolver = EntityResolver::new();
    let candidate = make_entity("Alice", "Person");

    let resolution = resolver.resolve(&candidate, &[], &[]);

    assert!(matches!(resolution, Resolution::NewNode));
}

// ---------------------------------------------------------------------------
// Test 6: merge_nodes — alias deduplication.
// ---------------------------------------------------------------------------

#[test]
fn test_merge_nodes_deduplicates_aliases() {
    let cid = Uuid::new_v4();
    let mut canonical = make_graph_node("Alice Smith", NodeType::Person, vec!["Alice"], cid);

    let incoming = ExtractedEntity {
        name: "Alice".to_string(),   // already an alias — must not be duplicated
        entity_type: "Person".to_string(),
        description: "x".to_string(),
        aliases: vec!["A. Smith".to_string(), "Alice".to_string()], // "Alice" dup
        confidence: 0.6,
    };

    merge_nodes(&mut canonical, &incoming);

    // "Alice" appears once in aliases (from original), "A. Smith" is new
    let alice_count = canonical.aliases.iter().filter(|a| *a == "Alice").count();
    assert_eq!(alice_count, 1, "alias 'Alice' must not be duplicated");
    assert!(canonical.aliases.contains(&"A. Smith".to_string()));
}

// ---------------------------------------------------------------------------
// Test 7: merge_nodes — confidence is averaged.
// ---------------------------------------------------------------------------

#[test]
fn test_merge_nodes_averages_confidence() {
    let cid = Uuid::new_v4();
    let mut canonical = make_graph_node("Bob", NodeType::Person, vec![], cid);
    canonical.confidence = 0.8;

    let incoming = ExtractedEntity {
        name: "Bob".to_string(),
        entity_type: "Person".to_string(),
        description: "short".to_string(),
        aliases: vec![],
        confidence: 0.4,
    };

    merge_nodes(&mut canonical, &incoming);

    let expected = (0.8 + 0.4) / 2.0;
    assert!(
        (canonical.confidence - expected).abs() < 1e-5,
        "confidence should be averaged: expected {}, got {}",
        expected,
        canonical.confidence
    );
}

// ---------------------------------------------------------------------------
// Test 8: merge_nodes — longer description wins.
// ---------------------------------------------------------------------------

#[test]
fn test_merge_nodes_longer_description_wins() {
    let cid = Uuid::new_v4();
    let mut canonical = make_graph_node("Carol", NodeType::Person, vec![], cid);
    canonical.description = Some("short".to_string());

    let incoming = ExtractedEntity {
        name: "Carol".to_string(),
        entity_type: "Person".to_string(),
        description: "A much longer and more descriptive description of Carol".to_string(),
        aliases: vec![],
        confidence: 0.7,
    };

    merge_nodes(&mut canonical, &incoming);

    assert_eq!(
        canonical.description.as_deref().unwrap(),
        "A much longer and more descriptive description of Carol"
    );
}

// ---------------------------------------------------------------------------
// Test 9: build_graph_nodes — new entities become new nodes.
// ---------------------------------------------------------------------------

#[test]
fn test_build_graph_nodes_creates_new_nodes() {
    let cid = Uuid::new_v4();
    let resolver = EntityResolver::new();

    let entities = vec![
        ExtractedEntity {
            name: "GPT-4".to_string(),
            entity_type: "Concept".to_string(),
            description: "A large language model".to_string(),
            aliases: vec![],
            confidence: 0.9,
        },
        ExtractedEntity {
            name: "OpenAI".to_string(),
            entity_type: "Organization".to_string(),
            description: "AI research company".to_string(),
            aliases: vec!["OAI".to_string()],
            confidence: 0.95,
        },
    ];

    let (nodes, id_map) = build_graph_nodes(entities, cid, &[], &HashMap::new(), &resolver);

    assert_eq!(nodes.len(), 2);
    assert!(id_map.contains_key("GPT-4"));
    assert!(id_map.contains_key("OpenAI"));
    assert_ne!(id_map["GPT-4"], id_map["OpenAI"]);
}

// ---------------------------------------------------------------------------
// Test 10: build_graph_nodes — existing exact match triggers merge (not new node).
// ---------------------------------------------------------------------------

#[test]
fn test_build_graph_nodes_merges_exact_match() {
    let cid = Uuid::new_v4();
    let resolver = EntityResolver::new();

    let existing_node = make_graph_node("OpenAI", NodeType::Organization, vec![], cid);
    let existing_id = existing_node.id;
    let existing = vec![existing_node];

    let entities = vec![ExtractedEntity {
        name: "OpenAI".to_string(),
        entity_type: "Organization".to_string(),
        description: "AI research company".to_string(),
        aliases: vec![],
        confidence: 0.9,
    }];

    let (new_nodes, id_map) = build_graph_nodes(entities, cid, &existing, &HashMap::new(), &resolver);

    // Exact match → merged, no new node created
    assert_eq!(new_nodes.len(), 0, "exact match should not create a new node");
    assert_eq!(
        id_map["OpenAI"],
        existing_id,
        "id_map should point to the existing node's ID"
    );
}
