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
            "Person".to_string(),
            EntityTypeDef {
                name: "Person".to_string(),
                description: "A human individual".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec!["Researcher".to_string(), "Executive".to_string()],
            },
        );

        entity_types.insert(
            "Organization".to_string(),
            EntityTypeDef {
                name: "Organization".to_string(),
                description: "A formal organization or institution".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec![
                    "Company".to_string(),
                    "GovernmentAgency".to_string(),
                    "NGO".to_string(),
                    "University".to_string(),
                ],
            },
        );

        entity_types.insert(
            "Location".to_string(),
            EntityTypeDef {
                name: "Location".to_string(),
                description: "A geographic place".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec!["Country".to_string(), "City".to_string()],
            },
        );

        entity_types.insert(
            "Concept".to_string(),
            EntityTypeDef {
                name: "Concept".to_string(),
                description: "An abstract idea, technology, methodology, or product".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec!["Technology".to_string(), "Product".to_string()],
            },
        );

        entity_types.insert(
            "Event".to_string(),
            EntityTypeDef {
                name: "Event".to_string(),
                description: "A discrete occurrence in time".to_string(),
                parent: None,
                attributes: HashMap::new(),
                subtypes: vec![],
            },
        );

        entity_types.insert(
            "Document".to_string(),
            EntityTypeDef {
                name: "Document".to_string(),
                description: "A source document in the collection".to_string(),
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
                domain: vec!["Person".to_string()],
                range: vec!["Organization".to_string()],
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
                    "Organization".to_string(),
                    "Person".to_string(),
                    "Location".to_string(),
                ],
                range: vec!["Location".to_string()],
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
                    "Person".to_string(),
                    "Organization".to_string(),
                    "Location".to_string(),
                    "Concept".to_string(),
                    "Event".to_string(),
                ],
                range: vec![
                    "Person".to_string(),
                    "Organization".to_string(),
                    "Location".to_string(),
                    "Concept".to_string(),
                    "Event".to_string(),
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
                domain: vec!["Concept".to_string()],
                range: vec!["Concept".to_string()],
                inverse: Some("related_to".to_string()),
                symmetric: true,
                description: "Two concepts are semantically related".to_string(),
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
