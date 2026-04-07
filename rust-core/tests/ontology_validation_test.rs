//! ontology_validation_test.rs — Phase 3 ontology validation edge cases.
//!
//! Covers all ValidationError variants:
//!   - EmptyEntityName
//!   - ConfidenceBelowThreshold
//!   - UnknownEntityType
//!   - UnknownRelationshipType
//!   - InvalidDomain (source/target not in valid entities)
//! Also tests happy-path acceptance of valid entities and relationships.

use rust_core::models::{ExtractedEntity, ExtractedRelationship};
use rust_core::ontology::{Ontology, OntologyValidator};

// ---------------------------------------------------------------------------
// Helper builders
// ---------------------------------------------------------------------------

fn entity(name: &str, entity_type: &str, confidence: f32) -> ExtractedEntity {
    ExtractedEntity {
        name: name.to_string(),
        entity_type: entity_type.to_string(),
        description: "test entity".to_string(),
        aliases: vec![],
        confidence,
    }
}

fn relationship(source: &str, target: &str, predicate: &str) -> ExtractedRelationship {
    ExtractedRelationship {
        source: source.to_string(),
        target: target.to_string(),
        predicate: predicate.to_string(),
        context: "test context".to_string(),
        confidence: 0.8,
    }
}

fn default_validator(threshold: f32) -> OntologyValidator {
    OntologyValidator::new(Ontology::default_ontology(), threshold)
}

// ---------------------------------------------------------------------------
// Test 1: Happy path — valid entity types and relationships pass through.
// ---------------------------------------------------------------------------

#[test]
fn test_valid_entities_and_relationships_accepted() {
    let v = default_validator(0.4);
    let entities = vec![
        entity("Alice", "PERSON", 0.9),
        entity("Acme Corp", "ORGANIZATION", 0.85),
    ];
    let rels = vec![relationship("Alice", "Acme Corp", "works_at")];

    let report = v.validate_batch(entities, rels);

    assert_eq!(report.valid_entities.len(), 2);
    assert_eq!(report.valid_relationships.len(), 1);
    assert!(report.dropped_entities.is_empty());
    assert!(report.dropped_relationships.is_empty());
}

// ---------------------------------------------------------------------------
// Test 2: EmptyEntityName — entity with blank name is dropped.
// ---------------------------------------------------------------------------

#[test]
fn test_empty_entity_name_dropped() {
    let v = default_validator(0.4);
    let entities = vec![
        entity("", "PERSON", 0.9),
        entity("   ", "ORGANIZATION", 0.9),
        entity("Bob", "PERSON", 0.9),
    ];

    let report = v.validate_batch(entities, vec![]);

    assert_eq!(report.valid_entities.len(), 1, "only Bob should be valid");
    assert_eq!(report.dropped_entities.len(), 2);

    for (dropped, err) in &report.dropped_entities {
        assert!(
            matches!(err, rust_core::ontology::ValidationError::EmptyEntityName),
            "expected EmptyEntityName, got {:?}",
            err
        );
    }
}

// ---------------------------------------------------------------------------
// Test 3: ConfidenceBelowThreshold — low-confidence entities are dropped.
// ---------------------------------------------------------------------------

#[test]
fn test_confidence_below_threshold_dropped() {
    let threshold = 0.6;
    let v = default_validator(threshold);
    let entities = vec![
        entity("Alice", "PERSON", 0.3),   // below
        entity("Bob", "PERSON", 0.59),    // below (exclusive)
        entity("Carol", "PERSON", 0.60),  // exactly at threshold — accepted
        entity("Dave", "PERSON", 0.9),    // above
    ];

    let report = v.validate_batch(entities, vec![]);

    // Carol (0.60 >= 0.60) should be valid
    assert_eq!(report.valid_entities.len(), 2, "Carol and Dave should pass");
    assert_eq!(report.dropped_entities.len(), 2);

    for (e, err) in &report.dropped_entities {
        match err {
            rust_core::ontology::ValidationError::ConfidenceBelowThreshold {
                confidence,
                threshold: t,
                ..
            } => {
                assert!(*confidence < *t, "dropped confidence must be below threshold");
            }
            other => panic!("unexpected error: {:?}", other),
        }
    }
}

// ---------------------------------------------------------------------------
// Test 4: UnknownEntityType — entity with a type not in the ontology is dropped.
// ---------------------------------------------------------------------------

#[test]
fn test_unknown_entity_type_dropped() {
    let v = default_validator(0.0);
    let entities = vec![
        entity("Some Widget", "GADGET", 0.9),   // "GADGET" not in ontology
        entity("Another", "FLYINGCAR", 0.9),    // also unknown
        entity("Alice", "PERSON", 0.9),          // valid
    ];

    let report = v.validate_batch(entities, vec![]);

    assert_eq!(report.valid_entities.len(), 1);
    assert_eq!(report.dropped_entities.len(), 2);

    for (_, err) in &report.dropped_entities {
        assert!(
            matches!(err, rust_core::ontology::ValidationError::UnknownEntityType { .. }),
            "expected UnknownEntityType"
        );
    }
}

// ---------------------------------------------------------------------------
// Test 5: Subtypes are accepted as valid entity types.
// ---------------------------------------------------------------------------

#[test]
fn test_ontology_subtypes_accepted() {
    let v = default_validator(0.0);
    // "Researcher" is a subtype of "PERSON" in default_ontology
    let entities = vec![entity("Dr. Smith", "PERSON", 0.9)];

    let report = v.validate_batch(entities, vec![]);

    assert_eq!(report.valid_entities.len(), 1, "PERSON should be accepted");
    assert!(report.dropped_entities.is_empty());
}

// ---------------------------------------------------------------------------
// Test 6: UnknownRelationshipType — predicate not in ontology is dropped.
// ---------------------------------------------------------------------------

#[test]
fn test_unknown_relationship_type_dropped() {
    let v = default_validator(0.0);
    let entities = vec![
        entity("Alice", "PERSON", 0.9),
        entity("Berlin", "LOCATION", 0.9),
    ];
    let rels = vec![
        relationship("Alice", "Berlin", "invented"),  // "invented" not in ontology
    ];

    let report = v.validate_batch(entities, rels);

    assert_eq!(report.valid_entities.len(), 2);
    assert_eq!(report.valid_relationships.len(), 0);
    assert_eq!(report.dropped_relationships.len(), 1);

    let (_, err) = &report.dropped_relationships[0];
    assert!(
        matches!(err, rust_core::ontology::ValidationError::UnknownRelationshipType { .. }),
        "expected UnknownRelationshipType"
    );
}

// ---------------------------------------------------------------------------
// Test 7: Relationship dropped when source entity was itself dropped.
// ---------------------------------------------------------------------------

#[test]
fn test_relationship_dropped_when_source_entity_invalid() {
    let v = default_validator(0.5);
    // Alice is below threshold, so her relationship should also be dropped
    let entities = vec![
        entity("Alice", "PERSON", 0.1),       // dropped (confidence)
        entity("Acme Corp", "ORGANIZATION", 0.9),
    ];
    let rels = vec![relationship("Alice", "Acme Corp", "works_at")];

    let report = v.validate_batch(entities, rels);

    assert_eq!(report.valid_entities.len(), 1);
    assert_eq!(report.valid_relationships.len(), 0,
        "relationship must be dropped when source entity is invalid");
}

// ---------------------------------------------------------------------------
// Test 8: related_to requires both domain and range to be Concept.
// ---------------------------------------------------------------------------

#[test]
fn test_related_to_requires_concept_domain_and_range() {
    let v = default_validator(0.0);
    let entities = vec![
        entity("Machine Learning", "CONCEPT", 0.9),
        entity("Neural Networks", "CONCEPT", 0.9),
        entity("Alice", "PERSON", 0.9),
    ];
    let rels = vec![
        // valid: CONCEPT → CONCEPT
        relationship("Machine Learning", "Neural Networks", "related_to"),
        // invalid: PERSON → CONCEPT (domain = CONCEPT only)
        relationship("Alice", "Neural Networks", "related_to"),
    ];

    let report = v.validate_batch(entities, rels);

    assert_eq!(report.valid_relationships.len(), 1);
    assert_eq!(report.dropped_relationships.len(), 1);
}

// ---------------------------------------------------------------------------
// Test 9: Empty input — no panic, empty report.
// ---------------------------------------------------------------------------

#[test]
fn test_empty_input_no_panic() {
    let v = default_validator(0.5);
    let report = v.validate_batch(vec![], vec![]);
    assert!(report.valid_entities.is_empty());
    assert!(report.valid_relationships.is_empty());
    assert!(report.dropped_entities.is_empty());
    assert!(report.dropped_relationships.is_empty());
}

// ---------------------------------------------------------------------------
// Test 10: mentions relationship — many-to-many entity types accepted.
// ---------------------------------------------------------------------------

#[test]
fn test_mentions_relationship_accepted_for_all_core_types() {
    let v = default_validator(0.0);
    let entities = vec![
        entity("Alice", "PERSON", 0.9),
        entity("Acme", "ORGANIZATION", 0.9),
        entity("Paris", "LOCATION", 0.9),
        entity("AI", "CONCEPT", 0.9),
        entity("Summit 2024", "EVENT", 0.9),
    ];
    let rels = vec![
        relationship("Alice", "Acme", "mentions"),
        relationship("Acme", "Paris", "mentions"),
        relationship("AI", "Summit 2024", "mentions"),
    ];

    let report = v.validate_batch(entities, rels);

    assert_eq!(report.valid_relationships.len(), 3,
        "mentions should be valid for all core entity type combinations");
}
