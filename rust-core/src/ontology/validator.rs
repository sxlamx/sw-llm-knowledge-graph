//! ontology validation.

use crate::models::{ExtractedEntity, ExtractedRelationship};
use crate::ontology::Ontology;
use crate::ontology::rules::{ValidationRule, ValidationError};
use rayon::prelude::*;
use std::collections::HashMap;

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ValidationReport {
    pub valid_entities: Vec<ExtractedEntity>,
    pub valid_relationships: Vec<ExtractedRelationship>,
    pub dropped_entities: Vec<(ExtractedEntity, ValidationError)>,
    pub dropped_relationships: Vec<(ExtractedRelationship, ValidationError)>,
}

pub struct NameNotEmptyRule;
impl ValidationRule for NameNotEmptyRule {
    fn name(&self) -> &str {
        "name_not_empty"
    }
    fn validate(&self, entity: &ExtractedEntity, _: &Ontology) -> Result<(), ValidationError> {
        if entity.name.trim().is_empty() {
            Err(ValidationError::EmptyEntityName)
        } else {
            Ok(())
        }
    }
}

pub struct KnownEntityTypeRule;
impl ValidationRule for KnownEntityTypeRule {
    fn name(&self) -> &str {
        "valid_entity_type"
    }
    fn validate(
        &self,
        entity: &ExtractedEntity,
        ontology: &Ontology,
    ) -> Result<(), ValidationError> {
        if !ontology.is_valid_entity_type(&entity.entity_type) {
            Err(ValidationError::UnknownEntityType {
                entity_name: entity.name.clone(),
                type_name: entity.entity_type.clone(),
            })
        } else {
            Ok(())
        }
    }
}

pub struct ConfidenceThresholdRule {
    pub threshold: f32,
}
impl ConfidenceThresholdRule {
    pub fn new(threshold: f32) -> Self {
        Self { threshold }
    }
}
impl ValidationRule for ConfidenceThresholdRule {
    fn name(&self) -> &str {
        "confidence_threshold"
    }
    fn validate(&self, entity: &ExtractedEntity, _: &Ontology) -> Result<(), ValidationError> {
        if entity.confidence < self.threshold {
            Err(ValidationError::ConfidenceBelowThreshold {
                entity_name: entity.name.clone(),
                confidence: entity.confidence,
                threshold: self.threshold,
            })
        } else {
            Ok(())
        }
    }
}

pub struct DomainRangeRule;
impl DomainRangeRule {
    pub fn check_rel(
        rel: &ExtractedRelationship,
        source_type: &str,
        target_type: &str,
        ontology: &Ontology,
    ) -> Result<(), ValidationError> {
        if !ontology.is_valid_relationship(&rel.predicate, source_type, target_type) {
            Err(ValidationError::InvalidDomain {
                predicate: rel.predicate.clone(),
                source_type: source_type.to_string(),
            })
        } else {
            Ok(())
        }
    }
}

pub struct OntologyValidator {
    ontology: Ontology,
    rules: Vec<Box<dyn ValidationRule>>,
}

impl OntologyValidator {
    pub fn new(ontology: Ontology, confidence_threshold: f32) -> Self {
        let rules: Vec<Box<dyn ValidationRule>> = vec![
            Box::new(NameNotEmptyRule),
            Box::new(KnownEntityTypeRule),
            Box::new(ConfidenceThresholdRule::new(confidence_threshold)),
        ];
        Self { ontology, rules }
    }

    pub fn with_rules(ontology: Ontology, rules: Vec<Box<dyn ValidationRule>>) -> Self {
        Self { ontology, rules }
    }

    pub fn validate_batch(
        &self,
        entities: Vec<ExtractedEntity>,
        relationships: Vec<ExtractedRelationship>,
    ) -> ValidationReport {
        let ontology = &self.ontology;

        let (valid_entities, dropped_entities): (Vec<_>, Vec<_>) = entities
            .into_par_iter()
            .map(|entity| {
                let mut last_error = None;
                for rule in &self.rules {
                    if let Err(e) = rule.validate(&entity, ontology) {
                        last_error = Some(e);
                        break;
                    }
                }
                match last_error {
                    Some(e) => Err((entity, e)),
                    None => Ok(entity),
                }
            })
            .partition(Result::is_ok);

        let valid_entities: Vec<ExtractedEntity> =
            valid_entities.into_iter().filter_map(Result::ok).collect();
        let dropped_entities: Vec<(ExtractedEntity, ValidationError)> =
            dropped_entities.into_iter().filter_map(Result::err).collect();

        let entity_type_map: HashMap<String, String> = valid_entities
            .iter()
            .map(|e| (e.name.clone(), e.entity_type.clone()))
            .collect();

        let mut valid_relationships = Vec::new();
        let mut dropped_relationships = Vec::new();

        for rel in relationships {
            if !self.ontology.relationship_types.contains_key(&rel.predicate) {
                let predicate = rel.predicate.clone();
                dropped_relationships.push((
                    rel,
                    ValidationError::UnknownRelationshipType { predicate },
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

            if let Err(e) = DomainRangeRule::check_rel(&rel, &source_type, &target_type, &self.ontology) {
                dropped_relationships.push((rel, e));
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

pub fn validate_extraction_result(
    entities: Vec<ExtractedEntity>,
    relationships: Vec<ExtractedRelationship>,
    ontology: &Ontology,
    confidence_threshold: f32,
) -> ValidationReport {
    let validator = OntologyValidator::new(ontology.clone(), confidence_threshold);
    validator.validate_batch(entities, relationships)
}
