//! Ontology engine — types, validator, rules.

use std::collections::HashMap;

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct EntityTypeDef {
    pub name: String,
    pub description: String,
    pub parent: Option<String>,
    pub attributes: HashMap<String, AttributeDef>,
    pub subtypes: Vec<String>,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct AttributeDef {
    pub attr_type: AttrType,
    pub required: bool,
    pub description: Option<String>,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub enum AttrType {
    String,
    Integer,
    Float,
    Boolean,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct RelationshipTypeDef {
    pub name: String,
    pub domain: Vec<String>,
    pub range: Vec<String>,
    pub inverse: Option<String>,
    pub symmetric: bool,
    pub description: String,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Ontology {
    pub version: String,
    pub entity_types: HashMap<String, EntityTypeDef>,
    pub relationship_types: HashMap<String, RelationshipTypeDef>,
}

impl Ontology {
    pub fn default_ontology() -> Self {
        let mut entity_types = HashMap::new();

        entity_types.insert(
            "DATE".to_string(),
            EntityTypeDef {
                name: "DATE".to_string(),
                description: "A date or time reference".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec![],
            },
        );

        entity_types.insert(
            "MONEY".to_string(),
            EntityTypeDef {
                name: "MONEY".to_string(),
                description: "A monetary value".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec![],
            },
        );

        entity_types.insert(
            "PERCENT".to_string(),
            EntityTypeDef {
                name: "PERCENT".to_string(),
                description: "A percentage value".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec![],
            },
        );

        entity_types.insert(
            "LAW".to_string(),
            EntityTypeDef {
                name: "LAW".to_string(),
                description: "A legal provision or law".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec![],
            },
        );

        entity_types.insert(
            "PERSON".to_string(),
            EntityTypeDef {
                name: "PERSON".to_string(),
                description: "A human individual".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec!["Researcher".to_string(), "Executive".to_string(), "Judge".to_string(), "Lawyer".to_string(), "Petitioner".to_string(), "Respondent".to_string(), "Witness".to_string()],
            },
        );

        entity_types.insert(
            "ORGANIZATION".to_string(),
            EntityTypeDef {
                name: "ORGANIZATION".to_string(),
                description: "A formal organization or institution".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec![
                    "Company".to_string(),
                    "GovernmentAgency".to_string(),
                    "NGO".to_string(),
                    "University".to_string(),
                    "Court".to_string(),
                ],
            },
        );

        entity_types.insert(
            "LOCATION".to_string(),
            EntityTypeDef {
                name: "LOCATION".to_string(),
                description: "A geographic place".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec!["Country".to_string(), "City".to_string(), "Region".to_string(), "Jurisdiction".to_string()],
            },
        );

        entity_types.insert(
            "CONCEPT".to_string(),
            EntityTypeDef {
                name: "CONCEPT".to_string(),
                description: "An abstract idea, technology, methodology, or product".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec!["Technology".to_string(), "Product".to_string(), "LegalConcept".to_string(), "DefinedTerm".to_string()],
            },
        );

        entity_types.insert(
            "EVENT".to_string(),
            EntityTypeDef {
                name: "EVENT".to_string(),
                description: "A discrete occurrence in time".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec!["CourtCase".to_string(), "LegislationTitle".to_string()],
            },
        );

        entity_types.insert(
            "DOCUMENT".to_string(),
            EntityTypeDef {
                name: "DOCUMENT".to_string(),
                description: "A source document in the collection".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec![],
            },
        );

        entity_types.insert(
            "COURT_CASE".to_string(),
            EntityTypeDef {
                name: "COURT_CASE".to_string(),
                description: "A court case or legal proceeding".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec![],
            },
        );

        entity_types.insert(
            "COURT".to_string(),
            EntityTypeDef {
                name: "COURT".to_string(),
                description: "A court of law".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec![],
            },
        );

        entity_types.insert(
            "LEGISLATION_TITLE".to_string(),
            EntityTypeDef {
                name: "LEGISLATION_TITLE".to_string(),
                description: "A named piece of legislation".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec![],
            },
        );

        entity_types.insert(
            "LEGISLATION_REFERENCE".to_string(),
            EntityTypeDef {
                name: "LEGISLATION_REFERENCE".to_string(),
                description: "A reference to a section or provision of legislation".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec![],
            },
        );

        entity_types.insert(
            "STATUTE_SECTION".to_string(),
            EntityTypeDef {
                name: "STATUTE_SECTION".to_string(),
                description: "A section number within a statute".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec![],
            },
        );

        entity_types.insert(
            "JURISDICTION".to_string(),
            EntityTypeDef {
                name: "JURISDICTION".to_string(),
                description: "A legal jurisdiction".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec![],
            },
        );

        entity_types.insert(
            "LEGAL_CONCEPT".to_string(),
            EntityTypeDef {
                name: "LEGAL_CONCEPT".to_string(),
                description: "A legal concept or doctrine".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec![],
            },
        );

        entity_types.insert(
            "DEFINED_TERM".to_string(),
            EntityTypeDef {
                name: "DEFINED_TERM".to_string(),
                description: "A term defined within a legal document".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec![],
            },
        );

        entity_types.insert(
            "CASE_CITATION".to_string(),
            EntityTypeDef {
                name: "CASE_CITATION".to_string(),
                description: "A formatted case citation".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec![],
            },
        );

        let mut relationship_types = HashMap::new();

        relationship_types.insert(
            "works_at".to_string(),
            RelationshipTypeDef {
                name: "works_at".to_string(),
                domain: vec!["PERSON".to_string()],
                range: vec!["ORGANIZATION".to_string()],
                inverse: Some("employs".to_string()),
                symmetric: false,
                description: "A person is employed by or affiliated with an organization"
                    .to_string(),
            },
        );

        relationship_types.insert(
            "located_in".to_string(),
            RelationshipTypeDef {
                name: "located_in".to_string(),
                domain: vec![
                    "ORGANIZATION".to_string(),
                    "PERSON".to_string(),
                    "LOCATION".to_string(),
                ],
                range: vec!["LOCATION".to_string()],
                inverse: None,
                symmetric: false,
                description: "An entity is located in a place".to_string(),
            },
        );

        relationship_types.insert(
            "mentions".to_string(),
            RelationshipTypeDef {
                name: "mentions".to_string(),
                domain: vec![
                    "PERSON".to_string(),
                    "ORGANIZATION".to_string(),
                    "LOCATION".to_string(),
                    "CONCEPT".to_string(),
                    "EVENT".to_string(),
                ],
                range: vec![
                    "PERSON".to_string(),
                    "ORGANIZATION".to_string(),
                    "LOCATION".to_string(),
                    "CONCEPT".to_string(),
                    "EVENT".to_string(),
                ],
                inverse: None,
                symmetric: false,
                description: "An entity mentions another entity".to_string(),
            },
        );

        relationship_types.insert(
            "related_to".to_string(),
            RelationshipTypeDef {
                name: "related_to".to_string(),
                domain: vec!["CONCEPT".to_string()],
                range: vec!["CONCEPT".to_string()],
                inverse: Some("related_to".to_string()),
                symmetric: true,
                description: "Two concepts are semantically related".to_string(),
            },
        );

        relationship_types.insert(
            "co_occurrence".to_string(),
            RelationshipTypeDef {
                name: "co_occurrence".to_string(),
                domain: vec![
                    "PERSON".to_string(),
                    "ORGANIZATION".to_string(),
                    "LOCATION".to_string(),
                    "CONCEPT".to_string(),
                    "EVENT".to_string(),
                    "COURT_CASE".to_string(),
                    "LEGISLATION_TITLE".to_string(),
                ],
                range: vec![
                    "PERSON".to_string(),
                    "ORGANIZATION".to_string(),
                    "LOCATION".to_string(),
                    "CONCEPT".to_string(),
                    "EVENT".to_string(),
                    "COURT_CASE".to_string(),
                    "LEGISLATION_TITLE".to_string(),
                ],
                inverse: Some("co_occurrence".to_string()),
                symmetric: true,
                description: "Two entities appear in the same context".to_string(),
            },
        );

        Self {
            version: "1.0".to_string(),
            entity_types,
            relationship_types,
        }
    }

    pub fn is_valid_entity_type(&self, type_name: &str) -> bool {
        if self.entity_types.contains_key(type_name) {
            return true;
        }
        for def in self.entity_types.values() {
            if def.subtypes.contains(&type_name.to_string()) {
                return true;
            }
        }
        false
    }

    pub fn is_valid_relationship(
        &self,
        predicate: &str,
        source_type: &str,
        target_type: &str,
    ) -> bool {
        if let Some(rel_def) = self.relationship_types.get(predicate) {
            let domain_ok = rel_def
                .domain
                .iter()
                .any(|d| self.is_subtype_of(source_type, d));
            let range_ok = rel_def
                .range
                .iter()
                .any(|r| self.is_subtype_of(target_type, r));
            domain_ok && range_ok
        } else {
            false
        }
    }

    pub fn is_subtype_of(&self, child_type: &str, parent_type: &str) -> bool {
        if child_type == parent_type {
            return true;
        }
        if let Some(def) = self.entity_types.get(child_type) {
            if let Some(parent) = &def.parent {
                return self.is_subtype_of(parent, parent_type);
            }
        }
        false
    }
}
