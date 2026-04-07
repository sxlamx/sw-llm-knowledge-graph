//! Ontology module.

pub mod types;
pub mod validator;
pub mod rules;

pub use types::Ontology;
pub use validator::{OntologyValidator, ValidationReport, DomainRangeRule, NameNotEmptyRule, KnownEntityTypeRule, ConfidenceThresholdRule, validate_extraction_result};
pub use rules::{ValidationRule, ValidationError};
