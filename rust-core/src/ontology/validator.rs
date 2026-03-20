//! Ontology validation.

use crate::models::{ExtractedEntity, ExtractedRelationship};
use crate::ontology::Ontology;
use std::collections::HashMap;

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub enum ValidationError {
    UnknownEntityType {
        entity_name: String,
        type_name: String,
    },
    UnknownRelationshipType {
        predicate: String,
    },
    InvalidDomain {
        predicate: String,
        source_type: String,
    },
    InvalidRange {
        predicate: String,
        target_type: String,
    },
    ConfidenceBelowThreshold {
        entity_name: String,
        confidence: f32,
        threshold: f32,
    },
    EmptyEntityName,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ValidationReport {
    pub valid_entities: Vec<ExtractedEntity>,
    pub valid_relationships: Vec<ExtractedRelationship>,
    pub dropped_entities: Vec<(ExtractedEntity, ValidationError)>,
    pub dropped_relationships: Vec<(ExtractedRelationship, ValidationError)>,
}

pub struct OntologyValidator {
    ontology: Ontology,
    confidence_threshold: f32,
}

impl OntologyValidator {
    pub fn new(ontology: Ontology, confidence_threshold: f32) -> Self {
        Self {
            ontology,
            confidence_threshold,
        }
    }

    pub fn validate_batch(
        &self,
        entities: Vec<ExtractedEntity>,
        relationships: Vec<ExtractedRelationship>,
    ) -> ValidationReport {
        let mut valid_entities = Vec::new();
        let mut dropped_entities = Vec::new();

        for entity in entities {
            if entity.name.trim().is_empty() {
                dropped_entities.push((entity, ValidationError::EmptyEntityName));
                continue;
            }

            if entity.confidence < self.confidence_threshold {
                let name = entity.name.clone();
                let confidence = entity.confidence;
                dropped_entities.push((
                    entity,
                    ValidationError::ConfidenceBelowThreshold {
                        entity_name: name,
                        confidence,
                        threshold: self.confidence_threshold,
                    },
                ));
                continue;
            }

            if !self.ontology.is_valid_entity_type(&entity.entity_type) {
                let name = entity.name.clone();
                let type_name = entity.entity_type.clone();
                dropped_entities.push((
                    entity,
                    ValidationError::UnknownEntityType {
                        entity_name: name,
                        type_name,
                    },
                ));
                continue;
            }

            valid_entities.push(entity);
        }

        let entity_type_map: HashMap<String, String> = valid_entities
            .iter()
            .map(|e| (e.name.clone(), e.entity_type.clone()))
            .collect();

        let mut valid_relationships = Vec::new();
        let mut dropped_relationships = Vec::new();

        for rel in relationships {
            if !self
                .ontology
                .relationship_types
                .contains_key(&rel.predicate)
            {
                let predicate = rel.predicate.clone();
                dropped_relationships.push((
                    rel,
                    ValidationError::UnknownRelationshipType {
                        predicate,
                    },
                ));
                continue;
            }

            let source_type = entity_type_map.get(&rel.source).cloned();
            let target_type = entity_type_map.get(&rel.target).cloned();

            if source_type.is_none() || target_type.is_none() {
                let predicate = rel.predicate.clone();
                let source = source_type.unwrap_or_else(|| "unknown".to_string());
                dropped_relationships.push((
                    rel,
                    ValidationError::InvalidDomain {
                        predicate,
                        source_type: source,
                    },
                ));
                continue;
            }

            let source_type = source_type.unwrap();
            let target_type = target_type.unwrap();

            if !self
                .ontology
                .is_valid_relationship(&rel.predicate, &source_type, &target_type)
            {
                let predicate = rel.predicate.clone();
                dropped_relationships.push((
                    rel,
                    ValidationError::InvalidDomain {
                        predicate,
                        source_type,
                    },
                ));
                continue;
            }

            valid_relationships.push(rel);
        }

        ValidationReport {
            valid_entities,
            valid_relationships,
            dropped_entities,
            dropped_relationships,
        }
    }
}
