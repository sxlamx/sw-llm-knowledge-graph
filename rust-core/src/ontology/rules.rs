//! Validation rules — trait definition only.

use crate::models::ExtractedEntity;

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

pub trait ValidationRule: Send + Sync {
    fn name(&self) -> &str;
    fn validate(
        &self,
        entity: &ExtractedEntity,
        ontology: &crate::ontology::Ontology,
    ) -> Result<(), ValidationError>;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::ExtractedEntity;
    use crate::ontology::Ontology;
    use crate::ontology::validator::{
        NameNotEmptyRule, KnownEntityTypeRule, ConfidenceThresholdRule, OntologyValidator,
    };

    #[test]
    fn test_unknown_entity_type_error() {
        let err = ValidationError::UnknownEntityType {
            entity_name: "Test".into(),
            type_name: "FAKE_TYPE".into(),
        };
        assert!(matches!(err, ValidationError::UnknownEntityType { .. }));
    }

    #[test]
    fn test_confidence_below_threshold_error() {
        let err = ValidationError::ConfidenceBelowThreshold {
            entity_name: "Test".into(),
            confidence: 0.1,
            threshold: 0.3,
        };
        if let ValidationError::ConfidenceBelowThreshold { confidence, threshold, .. } = err {
            assert!(confidence < threshold);
        }
    }

    #[test]
    fn test_empty_entity_name_error() {
        let err = ValidationError::EmptyEntityName;
        assert!(matches!(err, ValidationError::EmptyEntityName));
    }

    #[test]
    fn test_unknown_relationship_type_error() {
        let err = ValidationError::UnknownRelationshipType {
            predicate: "nonexistent_rel".into(),
        };
        assert!(matches!(err, ValidationError::UnknownRelationshipType { .. }));
    }

    #[test]
    fn test_invalid_domain_error() {
        let err = ValidationError::InvalidDomain {
            predicate: "works_at".into(),
            source_type: "CONCEPT".into(),
        };
        assert!(matches!(err, ValidationError::InvalidDomain { .. }));
    }

    #[test]
    fn test_invalid_range_error() {
        let err = ValidationError::InvalidRange {
            predicate: "located_in".into(),
            target_type: "CONCEPT".into(),
        };
        assert!(matches!(err, ValidationError::InvalidRange { .. }));
    }

    #[test]
    fn test_name_not_empty_rule_rejects_empty() {
        let ontology = Ontology::default_ontology();
        let entity = ExtractedEntity {
            name: "  ".to_string(),
            entity_type: "PERSON".to_string(),
            description: String::new(),
            aliases: vec![],
            confidence: 0.9,
        };
        let result = NameNotEmptyRule.validate(&entity, &ontology);
        assert!(matches!(result, Err(ValidationError::EmptyEntityName)));
    }

    #[test]
    fn test_name_not_empty_rule_accepts_nonempty() {
        let ontology = Ontology::default_ontology();
        let entity = ExtractedEntity {
            name: "Alice".to_string(),
            entity_type: "PERSON".to_string(),
            description: String::new(),
            aliases: vec![],
            confidence: 0.9,
        };
        let result = NameNotEmptyRule.validate(&entity, &ontology);
        assert!(result.is_ok());
    }

    #[test]
    fn test_known_entity_type_rule_rejects_unknown() {
        let ontology = Ontology::default_ontology();
        let entity = ExtractedEntity {
            name: "Thing".to_string(),
            entity_type: "FAKE_TYPE".to_string(),
            description: String::new(),
            aliases: vec![],
            confidence: 0.9,
        };
        let result = KnownEntityTypeRule.validate(&entity, &ontology);
        assert!(matches!(result, Err(ValidationError::UnknownEntityType { .. })));
    }

    #[test]
    fn test_known_entity_type_rule_accepts_known() {
        let ontology = Ontology::default_ontology();
        let entity = ExtractedEntity {
            name: "Alice".to_string(),
            entity_type: "PERSON".to_string(),
            description: String::new(),
            aliases: vec![],
            confidence: 0.9,
        };
        let result = KnownEntityTypeRule.validate(&entity, &ontology);
        assert!(result.is_ok());
    }

    #[test]
    fn test_confidence_threshold_rule_rejects_below() {
        let ontology = Ontology::default_ontology();
        let rule = ConfidenceThresholdRule::new(0.3);
        let entity = ExtractedEntity {
            name: "Low".to_string(),
            entity_type: "PERSON".to_string(),
            description: String::new(),
            aliases: vec![],
            confidence: 0.2,
        };
        let result = rule.validate(&entity, &ontology);
        assert!(matches!(result, Err(ValidationError::ConfidenceBelowThreshold { .. })));
    }

    #[test]
    fn test_confidence_threshold_rule_accepts_at_threshold() {
        let ontology = Ontology::default_ontology();
        let rule = ConfidenceThresholdRule::new(0.3);
        let entity = ExtractedEntity {
            name: "At".to_string(),
            entity_type: "PERSON".to_string(),
            description: String::new(),
            aliases: vec![],
            confidence: 0.3,
        };
        let result = rule.validate(&entity, &ontology);
        assert!(result.is_ok());
    }

    #[test]
    fn test_validate_batch_drops_invalid_entities() {
        let ontology = Ontology::default_ontology();
        let validator = OntologyValidator::new(ontology, 0.3);

        let entities = vec![
            ExtractedEntity {
                name: "Alice".to_string(),
                entity_type: "PERSON".to_string(),
                description: "A person".to_string(),
                aliases: vec![],
                confidence: 0.9,
            },
            ExtractedEntity {
                name: "".to_string(),
                entity_type: "PERSON".to_string(),
                description: String::new(),
                aliases: vec![],
                confidence: 0.5,
            },
            ExtractedEntity {
                name: "Bob".to_string(),
                entity_type: "UNKNOWN_TYPE".to_string(),
                description: String::new(),
                aliases: vec![],
                confidence: 0.8,
            },
            ExtractedEntity {
                name: "Low".to_string(),
                entity_type: "PERSON".to_string(),
                description: String::new(),
                aliases: vec![],
                confidence: 0.1,
            },
        ];

        let report = validator.validate_batch(entities, vec![]);
        assert_eq!(report.valid_entities.len(), 1);
        assert_eq!(report.valid_entities[0].name, "Alice");
        assert_eq!(report.dropped_entities.len(), 3);
    }
}
