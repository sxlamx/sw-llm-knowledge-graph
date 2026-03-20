//! Validation rules.

use crate::models::ExtractedEntity;
use crate::ontology::{Ontology, ValidationError};

pub trait ValidationRule: Send + Sync {
    fn name(&self) -> &str;
    fn validate(
        &self,
        entity: &ExtractedEntity,
        ontology: &Ontology,
    ) -> Result<(), ValidationError>;
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

pub struct ValidEntityTypeRule;
impl ValidationRule for ValidEntityTypeRule {
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
