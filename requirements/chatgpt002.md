Below is a **production-grade graph schema + LanceDB table design** tailored for your **LLM-powered Rust Knowledge Graph system**, optimized for:

* Hybrid search (vector + keyword + graph)
* Efficient traversal
* Topic filtering
* Incremental updates

---

# 1. Knowledge Graph Schema (Logical Model)

We use a **Directed Property Graph**.

## 1.1 Node Types

### 1. Entity Node

```json
{
  "id": "ent_123",
  "type": "ENTITY",
  "entity_type": "Person | Organization | Concept | Location | Event",
  "name": "OpenAI",
  "aliases": ["Open AI", "OpenAI Inc"],
  "embedding": [float],
  "description": "...",
  "confidence": 0.95,
  "created_at": "...",
  "updated_at": "..."
}
```

---

### 2. Document Node

```json
{
  "id": "doc_456",
  "type": "DOCUMENT",
  "title": "Research Paper",
  "source": "local | gdrive",
  "path": "/folder/file.pdf",
  "metadata": {
    "author": "...",
    "created_at": "...",
    "file_type": "pdf"
  }
}
```

---

### 3. Chunk Node

```json
{
  "id": "chunk_789",
  "type": "CHUNK",
  "document_id": "doc_456",
  "text": "...",
  "embedding": [float],
  "position": 12,
  "token_count": 512
}
```

---

### 4. Topic Node

```json
{
  "id": "topic_001",
  "type": "TOPIC",
  "name": "Machine Learning",
  "embedding": [float],
  "keywords": ["AI", "models"],
  "score": 0.87
}
```

---

## 1.2 Edge Types

### Relationship Schema

```json
{
  "id": "edge_001",
  "source": "ent_123",
  "target": "ent_456",
  "type": "RELATES_TO",
  "weight": 0.82,
  "context": "Extracted sentence...",
  "chunk_id": "chunk_789",
  "created_at": "..."
}
```

---

### Core Edge Types

| Edge Type          | From → To            | Description                |
| ------------------ | -------------------- | -------------------------- |
| `MENTIONS`         | CHUNK → ENTITY       | Entity appears in chunk    |
| `RELATES_TO`       | ENTITY → ENTITY      | LLM-extracted relationship |
| `BELONGS_TO_TOPIC` | ENTITY/CHUNK → TOPIC | Topic classification       |
| `DERIVED_FROM`     | CHUNK → DOCUMENT     | Chunk origin               |
| `SIMILAR_TO`       | ENTITY ↔ ENTITY      | Embedding similarity       |
| `NEXT`             | CHUNK → CHUNK        | Sequential chunk           |

---

# 2. Physical Storage Strategy

We **separate storage into:**

| Layer               | Technology               |
| ------------------- | ------------------------ |
| Graph relationships | Rust (petgraph / custom) |
| Vector storage      | LanceDB                  |
| Metadata            | PostgreSQL / SQLite      |
| Full-text           | Tantivy                  |

---

# 3. LanceDB Table Design

LanceDB is used for:

* Vector search
* Hybrid retrieval
* Fast filtering

---

## 3.1 Table: `entities`

### Schema

```python
{
  "id": "string",
  "name": "string",
  "entity_type": "string",
  "description": "string",
  "aliases": "list<string>",
  "embedding": "vector<float32>[768]",
  "confidence": "float32",
  "created_at": "timestamp"
}
```

### Notes

* Indexed on `embedding`
* Secondary filter on `entity_type`

---

## 3.2 Table: `chunks`

### Schema

```python
{
  "id": "string",
  "document_id": "string",
  "text": "string",
  "embedding": "vector<float32>[768]",
  "position": "int32",
  "token_count": "int32",
  "topics": "list<string>",
  "created_at": "timestamp"
}
```

### Notes

* Primary search table
* Supports hybrid retrieval

---

## 3.3 Table: `documents`

### Schema

```python
{
  "id": "string",
  "title": "string",
  "source": "string",
  "path": "string",
  "file_type": "string",
  "metadata": "json",
  "created_at": "timestamp"
}
```

---

## 3.4 Table: `topics`

### Schema

```python
{
  "id": "string",
  "name": "string",
  "embedding": "vector<float32>[768]",
  "keywords": "list<string>",
  "score": "float32"
}
```

---

## 3.5 Table: `edges` (Optional in LanceDB)

Used for **semantic edge search**.

```python
{
  "id": "string",
  "source_id": "string",
  "target_id": "string",
  "type": "string",
  "context": "string",
  "embedding": "vector<float32>[768]",
  "weight": "float32"
}
```

---

# 4. Rust Graph Storage (Core)

## 4.1 Node Struct

```rust
pub struct Node {
    pub id: String,
    pub node_type: NodeType,
    pub properties: HashMap<String, Value>,
}
```

---

## 4.2 Edge Struct

```rust
pub struct Edge {
    pub id: String,
    pub source: String,
    pub target: String,
    pub edge_type: EdgeType,
    pub weight: f32,
    pub properties: HashMap<String, Value>,
}
```

---

## 4.3 Graph Indexes

* `HashMap<String, Node>` → node lookup
* `HashMap<String, Vec<Edge>>` → adjacency list
* Reverse index for traversal

---

# 5. Indexing Strategy

## 5.1 Vector Index

* Entities
* Chunks
* Topics

## 5.2 Keyword Index (Tantivy)

* Chunk text
* Entity names

## 5.3 Graph Index

* Adjacency lists
* Path caching (optional)

---

# 6. Query Patterns

---

## 6.1 Semantic Search

```json
{
  "query": "LLM optimization techniques",
  "top_k": 10
}
```

Flow:

1. Embed query
2. Search `chunks` in LanceDB
3. Expand via graph neighbors

---

## 6.2 Entity Lookup

```json
{
  "entity": "OpenAI"
}
```

Flow:

1. Keyword + vector match
2. Retrieve connected nodes

---

## 6.3 Topic Filtering

```json
{
  "topics": ["AI", "Graph Systems"]
}
```

Flow:

* Filter chunks/entities by topic field
* Combine with vector score

---

## 6.4 Graph Traversal

```json
GET /graph/path?from=ent_1&to=ent_9
```

* BFS / Dijkstra
* Weight-based ranking

---

# 7. Optimization Strategies

## 7.1 Entity Resolution

* String similarity (Levenshtein)
* Embedding similarity threshold

---

## 7.2 Graph Pruning

* Remove low-confidence edges
* Limit node degree

---

## 7.3 Caching

* Frequently accessed nodes
* Popular query embeddings

---

# 8. Example LanceDB Setup (Python)

```python
import lancedb

db = lancedb.connect("./lancedb")

table = db.create_table(
    "chunks",
    data=[
        {
            "id": "chunk_1",
            "text": "LLMs are powerful...",
            "embedding": [0.1] * 768,
            "document_id": "doc_1",
            "position": 0,
            "token_count": 100,
            "topics": ["AI"]
        }
    ]
)
```

---

# 9. Future Extensions

* Graph embeddings stored in LanceDB
* Multi-hop retrieval scoring
* Temporal graphs (time-aware edges)
* Multi-modal embeddings

---

# 10. Key Design Principles

* **Separation of concerns**

  * Graph ≠ Vector store
* **Hybrid retrieval first**
* **Schema flexibility (JSON properties)**
* **Rust for performance-critical graph ops**

---


