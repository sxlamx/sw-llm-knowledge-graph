# 04 — Ontology Engine

## 1. Purpose

The ontology engine provides the formal semantic structure that governs what kinds of entities,
relationships, and attributes are valid in the knowledge graph. It acts as a type system for
graph content, preventing graph pollution from LLM hallucinations and ensuring consistent
semantic structure across collections.

The ontology engine:
1. Defines allowed entity types and their hierarchy
2. Defines allowed relationship types with domain/range constraints
3. Validates all LLM-extracted content before graph insertion
4. Supports versioning and evolution as understanding of the domain deepens
5. Can be bootstrapped automatically from sample documents using LLM assistance

---

## 2. Ontology Layers

```
Upper Ontology (generic — fixed in code)
    │
    ├── Person
    ├── Organization
    ├── Location
    ├── Concept
    ├── Event
    └── Document
         │
Domain Ontology (collection-specific — user-defined or LLM-generated)
    │
    ├── Organization → Company, GovernmentAgency, NGO, University
    ├── Person → Researcher, Politician, Executive
    ├── Location → Country, City, Region
    ├── Concept → Technology, Methodology, Product, Theory
    └── Event → Conference, Acquisition, Election, Publication
         │
Instance Level (graph nodes — populated by ingestion)
    │
    ├── "OpenAI" (Organization/Company)
    ├── "Sam Altman" (Person/Executive)
    └── "GPT-4" (Concept/Product)
```

---

## 3. Entity Type Hierarchy (JSON Schema)

```json
{
  "version": "1.0",
  "entity_types": {
    "Person": {
      "description": "A human individual",
      "attributes": {
        "name": {"type": "string", "required": true},
        "role": {"type": "string", "required": false},
        "affiliation": {"type": "string", "required": false}
      },
      "subtypes": {
        "Researcher": {"description": "An academic or industry researcher"},
        "Executive": {"description": "A corporate executive or leader"},
        "Politician": {"description": "An elected or appointed government official"}
      }
    },
    "Organization": {
      "description": "A formal organization or institution",
      "attributes": {
        "name": {"type": "string", "required": true},
        "founded": {"type": "integer", "required": false},
        "headquarters": {"type": "string", "required": false}
      },
      "subtypes": {
        "Company": {"description": "A for-profit corporation"},
        "GovernmentAgency": {"description": "A government department or agency"},
        "NGO": {"description": "A non-governmental organization"},
        "University": {"description": "A higher education institution"}
      }
    },
    "Location": {
      "description": "A geographic place",
      "subtypes": {
        "Country": {},
        "City": {},
        "Region": {}
      }
    },
    "Concept": {
      "description": "An abstract idea, technology, methodology, or product",
      "subtypes": {
        "Technology": {"description": "A technical system or tool"},
        "Methodology": {"description": "A systematic approach or framework"},
        "Product": {"description": "A commercial product or service"},
        "Theory": {"description": "A scientific or academic theory"}
      }
    },
    "Event": {
      "description": "A discrete occurrence in time",
      "subtypes": {
        "Conference": {},
        "Acquisition": {},
        "Publication": {},
        "Election": {}
      }
    },
    "Document": {
      "description": "A source document in the collection",
      "attributes": {
        "title": {"type": "string", "required": true},
        "file_type": {"type": "string", "required": true}
      }
    }
  }
}
```

---

## 4. Relationship Types with Domain/Range Constraints

```json
{
  "relationship_types": {
    "works_at": {
      "domain": ["Person"],
      "range": ["Organization"],
      "inverse": "employs",
      "description": "A person is employed by or affiliated with an organization"
    },
    "founded": {
      "domain": ["Person"],
      "range": ["Organization"],
      "description": "A person founded an organization"
    },
    "located_in": {
      "domain": ["Organization", "Person", "Event"],
      "range": ["Location"],
      "description": "An entity is located in or associated with a place"
    },
    "related_to": {
      "domain": ["Concept"],
      "range": ["Concept"],
      "description": "Two concepts are semantically related"
    },
    "part_of": {
      "domain": ["Organization", "Concept"],
      "range": ["Organization", "Concept"],
      "description": "An entity is a part of or subset of another"
    },
    "participated_in": {
      "domain": ["Person", "Organization"],
      "range": ["Event"],
      "description": "An entity participated in an event"
    },
    "mentions": {
      "domain": ["Chunk"],
      "range": ["Person", "Organization", "Location", "Concept", "Event"],
      "description": "A chunk of text mentions an entity"
    },
    "NEXT": {
      "domain": ["Chunk"],
      "range": ["Chunk"],
      "description": "Sequential ordering of chunks within a document"
    },
    "DERIVED_FROM": {
      "domain": ["Chunk"],
      "range": ["Document"],
      "description": "A chunk is derived from a document"
    },
    "BELONGS_TO_TOPIC": {
      "domain": ["Person", "Organization", "Location", "Concept", "Event", "Chunk"],
      "range": ["Topic"],
      "description": "An entity or chunk belongs to a topic cluster"
    },
    "SIMILAR_TO": {
      "domain": ["Person", "Organization", "Location", "Concept", "Event"],
      "range": ["Person", "Organization", "Location", "Concept", "Event"],
      "symmetric": true,
      "description": "Two entities are semantically similar (embedding-based)"
    }
  }
}
```

---

## 5. Rust Ontology Structs

```rust
// rust-core/src/ontology/types.rs

use std::collections::HashMap;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EntityTypeDef {
    pub name: String,
    pub description: String,
    pub parent: Option<String>,       // None = top-level type
    pub attributes: HashMap<String, AttributeDef>,
    pub subtypes: Vec<String>,        // child type names
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AttributeDef {
    pub attr_type: AttrType,
    pub required: bool,
    pub description: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum AttrType { String, Integer, Float, Boolean, List(Box<AttrType>) }

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RelationshipTypeDef {
    pub name: String,
    pub domain: Vec<String>,          // allowed source entity types
    pub range: Vec<String>,           // allowed target entity types
    pub inverse: Option<String>,
    pub symmetric: bool,
    pub description: String,
}

/// The loaded ontology for a collection (or global)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Ontology {
    pub version: String,
    pub collection_id: Option<uuid::Uuid>,
    pub entity_types: HashMap<String, EntityTypeDef>,
    pub relationship_types: HashMap<String, RelationshipTypeDef>,
}

impl Ontology {
    /// Check if a given entity type name is valid (including subtypes)
    pub fn is_valid_entity_type(&self, type_name: &str) -> bool {
        self.entity_types.contains_key(type_name)
    }

    /// Get all valid entity type names (flat list including subtypes)
    pub fn all_entity_type_names(&self) -> Vec<&str> {
        self.entity_types.keys().map(|s| s.as_str()).collect()
    }

    /// Check if source_type → predicate → target_type is valid
    pub fn is_valid_relationship(
        &self,
        predicate: &str,
        source_type: &str,
        target_type: &str,
    ) -> bool {
        if let Some(rel_def) = self.relationship_types.get(predicate) {
            // Check domain: source entity type must be in rel_def.domain (or subtype of)
            let domain_ok = rel_def.domain.iter().any(|d| self.is_subtype_of(source_type, d));
            let range_ok = rel_def.range.iter().any(|r| self.is_subtype_of(target_type, r));
            domain_ok && range_ok
        } else {
            false
        }
    }

    /// Returns true if child_type is child_type itself or a subtype of parent_type
    pub fn is_subtype_of(&self, child_type: &str, parent_type: &str) -> bool {
        if child_type == parent_type { return true; }
        if let Some(child_def) = self.entity_types.get(child_type) {
            if let Some(parent) = &child_def.parent {
                return self.is_subtype_of(parent, parent_type);
            }
        }
        false
    }
}
```

---

## 6. Validator Engine

```rust
// rust-core/src/ontology/validator.rs

use crate::ontology::types::Ontology;
use crate::models::{ExtractedEntity, ExtractedRelationship};

#[derive(Debug, Clone)]
pub enum ValidationError {
    UnknownEntityType { entity_name: String, type_name: String },
    UnknownRelationshipType { predicate: String },
    InvalidDomain { predicate: String, source_type: String },
    InvalidRange { predicate: String, target_type: String },
    ConfidenceBelowThreshold { entity_name: String, confidence: f32, threshold: f32 },
    EmptyEntityName,
    AttributeTypeMismatch { attribute: String, expected: String, found: String },
}

pub struct ValidationReport {
    pub valid_entities: Vec<ExtractedEntity>,
    pub valid_relationships: Vec<ExtractedRelationship>,
    pub dropped_entities: Vec<(ExtractedEntity, ValidationError)>,
    pub dropped_relationships: Vec<(ExtractedRelationship, ValidationError)>,
}

pub struct OntologyValidator {
    ontology: std::sync::Arc<tokio::sync::RwLock<Ontology>>,
    rules: Vec<Box<dyn ValidationRule>>,
}

impl OntologyValidator {
    pub async fn validate_entity(&self, entity: &ExtractedEntity) -> Result<(), ValidationError> {
        let ontology = self.ontology.read().await;

        // Run all rules
        for rule in &self.rules {
            rule.validate_entity(entity, &ontology)?;
        }
        Ok(())
    }

    pub async fn validate_relationship(
        &self,
        rel: &ExtractedRelationship,
        entity_type_map: &HashMap<String, String>,  // name → type
    ) -> Result<(), ValidationError> {
        let ontology = self.ontology.read().await;

        if !ontology.relationship_types.contains_key(&rel.predicate) {
            return Err(ValidationError::UnknownRelationshipType {
                predicate: rel.predicate.clone(),
            });
        }

        let source_type = entity_type_map.get(&rel.source)
            .ok_or(ValidationError::InvalidDomain {
                predicate: rel.predicate.clone(),
                source_type: "unknown".to_string(),
            })?;
        let target_type = entity_type_map.get(&rel.target)
            .ok_or(ValidationError::InvalidRange {
                predicate: rel.predicate.clone(),
                target_type: "unknown".to_string(),
            })?;

        if !ontology.is_valid_relationship(&rel.predicate, source_type, target_type) {
            return Err(ValidationError::InvalidDomain {
                predicate: rel.predicate.clone(),
                source_type: source_type.clone(),
            });
        }

        Ok(())
    }

    /// Validate a batch in parallel using Rayon
    pub async fn validate_batch(
        &self,
        entities: Vec<ExtractedEntity>,
        relationships: Vec<ExtractedRelationship>,
    ) -> ValidationReport {
        use rayon::prelude::*;
        let ontology = self.ontology.read().await.clone(); // clone for parallel access

        let (valid_entities, dropped_entities): (Vec<_>, Vec<_>) = entities
            .into_par_iter()
            .partition_map(|entity| {
                // Run all rules synchronously (Rayon thread pool)
                match validate_entity_sync(&entity, &ontology, &self.rules) {
                    Ok(()) => rayon::iter::Either::Left(entity),
                    Err(e) => rayon::iter::Either::Right((entity, e)),
                }
            });

        // Build entity type map for relationship validation
        let entity_type_map: HashMap<String, String> = valid_entities.iter()
            .map(|e| (e.name.clone(), e.entity_type.clone()))
            .collect();

        let (valid_relationships, dropped_relationships): (Vec<_>, Vec<_>) = relationships
            .into_par_iter()
            .partition_map(|rel| {
                match validate_relationship_sync(&rel, &entity_type_map, &ontology) {
                    Ok(()) => rayon::iter::Either::Left(rel),
                    Err(e) => rayon::iter::Either::Right((rel, e)),
                }
            });

        ValidationReport { valid_entities, valid_relationships, dropped_entities, dropped_relationships }
    }
}
```

---

## 7. Rule Trait System

Custom validation rules can be registered with the validator. Rules are evaluated in order.

```rust
// rust-core/src/ontology/rules.rs

pub trait ValidationRule: Send + Sync {
    fn name(&self) -> &str;
    fn validate_entity(
        &self,
        entity: &ExtractedEntity,
        ontology: &Ontology,
    ) -> Result<(), ValidationError>;
}

/// Rule: entity name must not be empty
pub struct NameNotEmptyRule;
impl ValidationRule for NameNotEmptyRule {
    fn name(&self) -> &str { "name_not_empty" }
    fn validate_entity(&self, entity: &ExtractedEntity, _: &Ontology) -> Result<(), ValidationError> {
        if entity.name.trim().is_empty() {
            Err(ValidationError::EmptyEntityName)
        } else {
            Ok(())
        }
    }
}

/// Rule: confidence must be above a threshold
pub struct ConfidenceThresholdRule { pub threshold: f32 }
impl ValidationRule for ConfidenceThresholdRule {
    fn name(&self) -> &str { "confidence_threshold" }
    fn validate_entity(&self, entity: &ExtractedEntity, _: &Ontology) -> Result<(), ValidationError> {
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

/// Rule: entity type must be in the ontology
pub struct ValidEntityTypeRule;
impl ValidationRule for ValidEntityTypeRule {
    fn name(&self) -> &str { "valid_entity_type" }
    fn validate_entity(&self, entity: &ExtractedEntity, ontology: &Ontology) -> Result<(), ValidationError> {
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

/// Default rule set
pub fn default_rules(confidence_threshold: f32) -> Vec<Box<dyn ValidationRule>> {
    vec![
        Box::new(NameNotEmptyRule),
        Box::new(ValidEntityTypeRule),
        Box::new(ConfidenceThresholdRule { threshold: confidence_threshold }),
    ]
}
```

---

## 8. Ontology Evolution and Versioning

### Version Format

Ontology versions follow semantic versioning: `{major}.{minor}.{patch}`

- **Major**: Breaking change (entity type removed or renamed)
- **Minor**: Additive change (new entity type, new relationship type)
- **Patch**: Non-structural change (description update, attribute addition)

### Schema Migration

When the ontology version increments, a migration function maps old entity types to new ones:

```rust
pub struct OntologyMigration {
    pub from_version: String,
    pub to_version: String,
    pub entity_type_renames: HashMap<String, String>,    // old → new
    pub relationship_renames: HashMap<String, String>,   // old → new
    pub dropped_types: Vec<String>,
}

pub fn migrate_graph(
    graph: &mut KnowledgeGraph,
    migration: &OntologyMigration,
) -> MigrationReport {
    let mut renamed_nodes = 0;
    for node in graph.nodes.values_mut() {
        if let Some(new_type) = migration.entity_type_renames.get(&node.node_type.to_string()) {
            node.node_type = new_type.parse().unwrap();
            renamed_nodes += 1;
        }
    }
    MigrationReport { renamed_nodes, .. }
}
```

### Drift Detection

The system tracks unknown entity types extracted by the LLM (types not in the current ontology)
and surfaces them as suggestions for ontology expansion:

```rust
pub struct DriftDetector {
    unknown_types: Arc<Mutex<HashMap<String, u32>>>,  // type_name → count
}

impl DriftDetector {
    pub fn record_unknown_type(&self, type_name: &str) {
        let mut map = self.unknown_types.lock().unwrap();
        *map.entry(type_name.to_string()).or_default() += 1;
    }

    pub fn get_suggestions(&self, min_count: u32) -> Vec<(String, u32)> {
        let map = self.unknown_types.lock().unwrap();
        let mut suggestions: Vec<_> = map.iter()
            .filter(|(_, &count)| count >= min_count)
            .map(|(k, &v)| (k.clone(), v))
            .collect();
        suggestions.sort_by_key(|(_, count)| std::cmp::Reverse(*count));
        suggestions
    }
}
```

---

## 9. LLM-Assisted Ontology Expansion

```python
# python-api/app/llm/ontogpt.py

ONTOLOGY_BOOTSTRAP_PROMPT = """
Analyze the following sample documents and propose an ontology for a knowledge graph.

The ontology should include:
1. Entity types (with hierarchy where appropriate)
2. Relationship types (with domain/range constraints)
3. Key attributes for each entity type

Output ONLY valid JSON matching this schema:
{json_schema}

SAMPLE DOCUMENTS:
{sample_texts}
"""

async def bootstrap_ontology(
    sample_texts: list[str],
    model: str = "gpt-4o",
) -> OntologyProposal:
    """Generate an ontology proposal from sample document texts."""
    combined = "\n\n---\n\n".join(sample_texts[:5])  # max 5 samples
    prompt = ONTOLOGY_BOOTSTRAP_PROMPT.format(
        json_schema=OntologyProposal.model_json_schema(),
        sample_texts=combined[:8000],
    )
    response = await openai_client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        response_format={"type": "json_object"},
        max_tokens=4000,
    )
    return OntologyProposal.model_validate_json(response.choices[0].message.content)
```

---

## 10. Storage

### PostgreSQL (Persistent Schema)

Ontology entity types and relationship types are stored in `ontology_entities` and
`ontology_relationships` tables (see `02-data-models.md`). The full versioned JSON blob is also
stored for history:

```sql
CREATE TABLE ontology_versions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_id UUID REFERENCES collections(id) ON DELETE CASCADE,
    version       TEXT NOT NULL,
    schema_json   JSONB NOT NULL,
    created_by    UUID REFERENCES users(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active     BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_ontology_versions_collection ON ontology_versions(collection_id, is_active);
```

---

## 11. Ontology Manager (Rust)

The `OntologyManager` loads the ontology at startup from PostgreSQL and caches it in an
`Arc<RwLock<Ontology>>`. Hot-reload is triggered by a file watch event or API call.

```rust
pub struct OntologyManager {
    ontology: Arc<RwLock<Ontology>>,
    collection_id: Uuid,
    version: Arc<AtomicU64>,
}

impl OntologyManager {
    pub async fn load_from_db(pool: &PgPool, collection_id: Uuid) -> Result<Self> {
        let row = sqlx::query!(
            "SELECT schema_json FROM ontology_versions WHERE collection_id = $1 AND is_active = true",
            collection_id
        )
        .fetch_one(pool)
        .await?;

        let ontology: Ontology = serde_json::from_value(row.schema_json)?;
        Ok(Self {
            ontology: Arc::new(RwLock::new(ontology)),
            collection_id,
            version: Arc::new(AtomicU64::new(0)),
        })
    }

    pub async fn hot_reload(&self, new_ontology: Ontology) {
        let mut lock = self.ontology.write().await;
        *lock = new_ontology;
        self.version.fetch_add(1, Ordering::Release);
        tracing::info!("Ontology hot-reloaded, version incremented");
    }

    pub async fn get(&self) -> tokio::sync::RwLockReadGuard<Ontology> {
        self.ontology.read().await
    }
}
```

---

## 12. API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/ontology` | Return active ontology for collection |
| `POST` | `/api/v1/ontology/generate` | Trigger LLM-assisted ontology generation from sample docs |
| `PUT` | `/api/v1/ontology` | Replace active ontology (creates new version) |
| `GET` | `/api/v1/ontology/versions` | List version history for collection |
| `POST` | `/api/v1/ontology/validate` | Validate a set of entities/relationships against active ontology |

### Generate Endpoint Request

```json
POST /api/v1/ontology/generate
{
  "collection_id": "uuid",
  "sample_doc_ids": ["uuid1", "uuid2", "uuid3"],
  "model": "gpt-4o"
}
```

### Response

```json
{
  "proposal": {
    "version": "1.0",
    "entity_types": { ... },
    "relationship_types": { ... }
  },
  "applied": false,
  "message": "Review the proposal and call PUT /ontology to apply"
}
```
