Got it — here’s a **production-grade specification + ready-to-use prompt templates** for **LLM extraction tightly coupled with your ontology**.

This is one of the most critical parts of your system because it directly controls **data quality, consistency, and downstream graph usability**.

---

# 1. Design Principles for Ontology-Coupled Prompting

## 1.1 Goals

* Force LLM to **adhere strictly to ontology**
* Minimize hallucination
* Produce **machine-ingestable structured output**
* Enable **deterministic post-processing**

---

## 1.2 Core Strategy

```text
STRICT SCHEMA + ENUM CONSTRAINTS + JSON OUTPUT + VALIDATION LOOP
```

---

# 2. Prompt Architecture

Each extraction prompt should contain:

### 2.1 Components

1. **System Instruction**
2. **Ontology Definition (compact)**
3. **Extraction Rules**
4. **Output Schema (STRICT JSON)**
5. **Few-shot Examples (optional but recommended)**
6. **Input Text**

---

# 3. Ontology Injection Format

Use a **compressed ontology representation**:

```json
{
  "entity_types": ["Person", "Organization", "Location", "Concept", "Event"],
  "relationship_types": {
    "works_at": ["Person", "Organization"],
    "located_in": ["Organization", "Location"],
    "related_to": ["Concept", "Concept"],
    "mentions": ["Chunk", "Entity"]
  }
}
```

---

# 4. Core Extraction Prompt Template

---

## 4.1 Base Prompt (Production Version)

```text
You are an information extraction engine.

Your task is to extract structured knowledge from text STRICTLY following the ontology.

### ONTOLOGY

Allowed Entity Types:
- Person
- Organization
- Location
- Concept
- Event

Allowed Relationships:
- works_at(Person → Organization)
- located_in(Organization → Location)
- related_to(Concept → Concept)
- mentions(Chunk → Entity)

### RULES

1. ONLY use the entity types listed above.
2. ONLY use the relationship types listed above.
3. DO NOT invent new types.
4. If uncertain, omit the entity or relationship.
5. Normalize entity names (e.g., "Open AI" → "OpenAI").
6. Avoid duplicates within the same output.
7. Use concise names for entities.

### OUTPUT FORMAT (STRICT JSON)

Return ONLY valid JSON. No explanation.

{
  "entities": [
    {
      "id": "E1",
      "name": "string",
      "type": "one of allowed types"
    }
  ],
  "relationships": [
    {
      "source": "E1",
      "target": "E2",
      "type": "allowed relationship",
      "confidence": 0.0-1.0
    }
  ],
  "topics": ["string"],
  "summary": "string"
}

### INPUT TEXT
{input_text}
```

---

# 5. Advanced Prompt Variants

---

## 5.1 High-Precision Mode (Strict Filtering)

Use when quality > recall.

```text
ADDITIONAL RULES:

- Only extract entities with high confidence (>0.8)
- Ignore vague or generic concepts
- Do not infer relationships unless explicitly stated
```

---

## 5.2 High-Recall Mode (Exploratory KG)

```text
ADDITIONAL RULES:

- Extract all possible entities even if uncertain
- Infer relationships when strongly implied
- Include broader concepts
```

---

## 5.3 Incremental Extraction (Chunk-Based)

```text
You are processing a chunk of a larger document.

- Maintain consistency with previously seen entities
- Reuse entity names when possible
- Do not create duplicates for the same entity
```

---

# 6. Entity Normalization Prompt

Used after extraction (optional second pass).

```text
Normalize the following entities:

Rules:
- Merge duplicates
- Standardize names
- Prefer canonical forms

Input:
{entities_json}

Output:
{
  "canonical_entities": [...]
}
```

---

# 7. Relationship Validation Prompt

Used as a **guardrail step**.

```text
Validate relationships based on ontology:

- Remove invalid relationships
- Fix incorrect types if obvious

Ontology:
{ontology}

Input:
{relationships}

Output:
{
  "valid_relationships": [...]
}
```

---

# 8. Topic Extraction Prompt

```text
Extract high-level topics from the text.

Rules:
- Use short phrases (1–3 words)
- Avoid duplicates
- Prefer generalizable concepts

Output:
{
  "topics": ["AI", "Graph Systems"]
}
```

---

# 9. Few-Shot Example (Highly Recommended)

```json
INPUT:
"John works at OpenAI in San Francisco."

OUTPUT:
{
  "entities": [
    {"id": "E1", "name": "John", "type": "Person"},
    {"id": "E2", "name": "OpenAI", "type": "Organization"},
    {"id": "E3", "name": "San Francisco", "type": "Location"}
  ],
  "relationships": [
    {"source": "E1", "target": "E2", "type": "works_at", "confidence": 0.95},
    {"source": "E2", "target": "E3", "type": "located_in", "confidence": 0.9}
  ],
  "topics": ["Employment"],
  "summary": "John works at OpenAI in San Francisco."
}
```

---

# 10. Multi-Stage Extraction Pipeline

---

## Recommended Flow

```text
Step 1: Raw Extraction (LLM)
Step 2: Validation (rules / LLM)
Step 3: Entity Resolution
Step 4: Graph Construction
```

---

# 11. Error Handling Strategy

---

## 11.1 JSON Validation

* Reject invalid JSON
* Retry with:

```text
Return ONLY valid JSON. Fix formatting errors.
```

---

## 11.2 Ontology Violations

* Drop invalid entities/relations
* Log for analysis

---

## 11.3 Confidence Thresholding

```text
confidence < 0.7 → discard
```

---

# 12. Rust + Python Integration Strategy

---

## Python (LLM Layer)

* Prompt construction
* Response parsing
* Retry logic

---

## Rust (Core Engine)

* Ontology validation
* Schema enforcement
* Graph insertion

---

# 13. Performance Optimizations

---

## 13.1 Prompt Compression

* Use short ontology definitions
* Avoid verbose descriptions

---

## 13.2 Batch Processing

* Process multiple chunks per request (if model supports)

---

## 13.3 Caching

* Cache:

  * embeddings
  * normalized entities

---

# 14. Advanced Enhancements

---

## 14.1 Function Calling (Preferred if available)

Define schema as function:

```json
{
  "name": "extract_knowledge",
  "parameters": {
    "type": "object",
    "properties": {
      "entities": {...},
      "relationships": {...}
    }
  }
}
```

---

## 14.2 Self-Validation Prompt

```text
Review your output:
- Ensure all entity types are valid
- Ensure relationships match ontology
- Fix errors
```

---

## 14.3 Chain-of-Thought (Hidden)

* Use internally
* Do NOT expose in output

---

# 15. Key Design Decisions

---

## Strict vs Flexible

| Mode     | Use Case      |
| -------- | ------------- |
| Strict   | Production KG |
| Flexible | Exploration   |

---

## Single vs Multi-Pass

| Approach    | Pros     | Cons          |
| ----------- | -------- | ------------- |
| Single-pass | Fast     | Less accurate |
| Multi-pass  | Accurate | Slower        |

---

# 16. Production Defaults (Recommended)

```text
Temperature: 0.0–0.2
Top-K: 10–20 entities per chunk
Chunk size: 300–800 tokens
Retry: 2–3 times
```

---

# 17. End-to-End Example Flow

```text
Document →
Chunk →
LLM Extraction (ontology prompt) →
JSON →
Validation →
Entity Resolution →
Graph Insert
```

---

# 18. What This Solves

* Eliminates schema drift
* Reduces hallucinated relations
* Improves entity consistency
* Enables deterministic pipelines

---

If you want next, I can generate:

* **Full Python extraction service (FastAPI + prompt orchestration)**
* **Rust validator that enforces ontology at runtime**
* **Function-calling schema version for OpenAI/Ollama**
* **End-to-end pipeline (chunk → extract → resolve → store)**

Just tell me 👍
