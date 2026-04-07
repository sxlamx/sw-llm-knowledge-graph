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
