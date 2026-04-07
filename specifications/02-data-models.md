# 02 — Data Models

## 1. Overview

The system uses a hybrid storage strategy:

- **LanceDB**: all persistent storage — vector embeddings, chunk/node/edge/document records **and**
  relational metadata (users, collections, ingest jobs, token revocation). LanceDB Arrow tables
  are used in place of PostgreSQL for simplicity and zero-dependency deployment.
- **Rust in-memory**: petgraph-backed `KnowledgeGraph` struct for fast traversal

All UUIDs are v4. All timestamps are stored as Unix epoch milliseconds (int64) in LanceDB.
Embeddings are `f32` vectors; dimension is configurable (default **1024** via
`Qwen/Qwen3-Embedding-0.6B`). The spec value of 1536 (OpenAI `text-embedding-3-large`) is
no longer used.

> **Implementation deviation**: The original spec used PostgreSQL + SQLAlchemy + Alembic for
> relational metadata. The current implementation uses LanceDB system tables exclusively.
> PostgreSQL migration remains an option for Phase 3 hardening.

---

## 2. LanceDB Tables

LanceDB stores data as Arrow RecordBatches. Schemas are defined in Rust using `arrow-schema`.

### 2.1 `chunks` Table

Stores text chunks with their contextual embeddings. The `contextual_text` field contains the
LLM-generated 2-sentence prefix concatenated with the raw text — this is what is embedded.

```
Field               Arrow Type                          Notes
──────────────────────────────────────────────────────────────────────────────
id                  Utf8 (UUID v4)                      Primary key
doc_id              Utf8 (UUID v4)                      FK → documents.id
collection_id       Utf8 (UUID v4)                      Tenant partition key
text                Utf8                                Raw extracted text
contextual_text     Utf8                                LLM prefix + raw text (embedded)
embedding           FixedSizeList<Float32>[1536]        Vector for ANN search
position            Int32                               Chunk order within document
token_count         Int32                               Approximate token count
page                Int32                               Source page number (PDF)
topics              List<Utf8>                          Topic labels assigned by LLM
created_at          TimestampMicrosecond (UTC)
```

Rust schema builder:

```rust
use arrow_schema::{DataType, Field, FixedSizeList, Schema, TimeUnit};

fn chunks_schema() -> Schema {
    Schema::new(vec![
        Field::new("id", DataType::Utf8, false),
        Field::new("doc_id", DataType::Utf8, false),
        Field::new("collection_id", DataType::Utf8, false),
        Field::new("text", DataType::Utf8, false),
        Field::new("contextual_text", DataType::Utf8, false),
        Field::new("embedding", DataType::FixedSizeList(
            Arc::new(Field::new("item", DataType::Float32, true)), 1024  // configurable via settings.embedding_dimension
        ), false),
        Field::new("position", DataType::Int32, false),
        Field::new("token_count", DataType::Int32, true),
        Field::new("page", DataType::Int32, true),
        Field::new("topics", DataType::List(
            Arc::new(Field::new("item", DataType::Utf8, true))
        ), true),
        Field::new("created_at", DataType::Timestamp(TimeUnit::Microsecond, Some("UTC".into())), false),
    ])
}
```

### 2.2 `nodes` Table (Entity Nodes)

Stores extracted named entities. Each entity has an embedding for similarity-based entity
resolution and semantic search.

```
Field               Arrow Type                          Notes
──────────────────────────────────────────────────────────────────────────────
id                  Utf8 (UUID v4)                      Primary key
collection_id       Utf8 (UUID v4)                      Tenant partition key
label               Utf8                                Display name (e.g., "OpenAI")
entity_type         Utf8                                Enum: Person|Organization|Location|
                                                        Concept|Event|Document
description         Utf8                                LLM-generated description
aliases             List<Utf8>                          Known alternate names
embedding           FixedSizeList<Float32>[1536]        Name+description embedding
confidence          Float32                             LLM extraction confidence 0.0–1.0
ontology_class      Utf8                                Ontology entity type path
                                                        (e.g., "Organization/Company")
metadata            Utf8 (JSON blob)                    Flexible key-value attributes
created_at          TimestampMicrosecond (UTC)
updated_at          TimestampMicrosecond (UTC)
```

### 2.3 `edges` Table

Stores semantic relationships between entities. Edges reference their source chunk for provenance.

```
Field               Arrow Type                          Notes
──────────────────────────────────────────────────────────────────────────────
id                  Utf8 (UUID v4)                      Primary key
collection_id       Utf8 (UUID v4)                      Tenant partition key
source_id           Utf8 (UUID v4)                      FK → nodes.id
target_id           Utf8 (UUID v4)                      FK → nodes.id
predicate           Utf8                                Relationship label (e.g., "works_at")
weight              Float32                             Confidence/strength 0.0–1.0
context             Utf8                                Supporting sentence from source text
chunk_id            Utf8 (UUID v4)                      FK → chunks.id (provenance)
doc_origins         List<Utf8>                          All doc UUIDs that support this edge
created_at          TimestampMicrosecond (UTC)
```

### 2.4 `documents` Table

Stores document metadata and raw content for display and re-extraction.

```
Field               Arrow Type                          Notes
──────────────────────────────────────────────────────────────────────────────
id                  Utf8 (UUID v4)                      Primary key
collection_id       Utf8 (UUID v4)                      Tenant partition key
title               Utf8                                Filename or extracted title
source              Utf8                                Enum: local|gdrive
path                Utf8                                Absolute file path or Drive URL
file_type           Utf8                                Enum: pdf|docx|md|txt|html
file_hash           Utf8 (BLAKE3 hex)                   For incremental change detection
raw_content         LargeBinary                         Full extracted text (compressed)
doc_summary         Utf8                                LLM-generated 200-300 word summary
metadata            Utf8 (JSON blob)                    Author, title, page count, etc.
created_at          TimestampMicrosecond (UTC)
updated_at          TimestampMicrosecond (UTC)
```

### 2.5 `topics` Table

Stores topic cluster centroids extracted by LLM. Used for topic-based filtering.

```
Field               Arrow Type                          Notes
──────────────────────────────────────────────────────────────────────────────
id                  Utf8 (UUID v4)                      Primary key
collection_id       Utf8 (UUID v4)                      Tenant partition key
name                Utf8                                Topic label (e.g., "machine learning")
embedding           FixedSizeList<Float32>[1536]        Topic centroid embedding
keywords            List<Utf8>                          Representative keywords
score               Float32                             Coherence score
frequency           Int32                               Number of chunks assigned
```

---

## 3. LanceDB System Tables (Metadata)

All relational metadata is stored in LanceDB Arrow tables (no PostgreSQL dependency).
System tables are created at startup by `init_system_tables()` in `lancedb_client.py`.
Timestamps are stored as **Unix epoch milliseconds** (int64). Tenant isolation is by
`user_id` field lookups — no RLS (single-process deployment).

### 3.1 `users`

```
Field           Arrow Type    Notes
────────────────────────────────────────────────────────────
id              Utf8          UUID v4
google_sub      Utf8          Google subject identifier (unique)
email           Utf8
name            Utf8
avatar_url      Utf8
role            Utf8          "user" | "admin"
status          Utf8          "active" | "suspended"
created_at      Int64         Unix epoch ms
last_login      Int64         Unix epoch ms
```

### 3.2 `collections`

```
Field           Arrow Type    Notes
────────────────────────────────────────────────────────────
id              Utf8          UUID v4
user_id         Utf8          FK → users.id (ownership check)
name            Utf8
description     Utf8
folder_path     Utf8          Absolute path to local folder
status          Utf8          "active" | "ingesting" | "error" | "archived"
doc_count       Int32
created_at      Int64         Unix epoch ms
updated_at      Int64         Unix epoch ms
```

### 3.3 `ingest_jobs`

```
Field                Arrow Type    Notes
────────────────────────────────────────────────────────────
id                   Utf8          UUID v4
collection_id        Utf8          FK → collections.id
status               Utf8          "pending" | "running" | "completed" | "failed" | "cancelled"
progress             Float32       0.0 – 1.0
total_docs           Int32
processed_docs       Int32
error_msg            Utf8
started_at           Int64         Unix epoch ms (null = not started)
completed_at         Int64         Unix epoch ms (null = not finished)
created_at           Int64         Unix epoch ms
options              Utf8          JSON blob: {max_cost_usd, ocr_enabled, ...}
last_completed_file  Utf8          Checkpoint: last successfully flushed file path
```

### 3.4 `revoked_tokens`

Stores refresh token JTIs that have been revoked (rotation blocklist).

```
Field           Arrow Type    Notes
────────────────────────────────────────────────────────────
jti             Utf8          JWT ID (UUID v4)
revoked_at      Int64         Unix epoch ms
expires_at      Int64         Unix epoch ms (for cleanup)
```

### 3.5 `drive_watch_channels`

Stores Google Drive push notification channel registrations (Phase 4).

```
Field           Arrow Type    Notes
────────────────────────────────────────────────────────────
channel_id      Utf8          Drive push channel UUID
resource_id     Utf8          Drive resource being watched
collection_id   Utf8          FK → collections.id
folder_id       Utf8          Google Drive folder ID
access_token    Utf8          OAuth access token for Drive API
expiry_ms       Int64         Token expiry (Unix ms)
created_at      Int64         Unix epoch ms
```

---

## 4. Rust In-Memory Structures

These structs live in the Rust core and represent the hot layer of the knowledge graph. They are
populated from LanceDB on startup and kept in sync during operation.

### 4.1 Core Structs

```rust
// rust-core/src/models.rs

use std::collections::HashMap;
use uuid::Uuid;
use serde::{Deserialize, Serialize};

/// Node type enum aligned with ontology entity types
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum NodeType {
    Person,
    Organization,
    Location,
    Concept,
    Event,
    Document,
    Chunk,
    Topic,
}

/// Edge type enum aligned with ontology relationship types
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum EdgeType {
    Mentions,          // Chunk → Entity
    RelatesTo,         // Entity → Entity
    WorksAt,           // Person → Organization
    LocatedIn,         // Org/Person → Location
    BelongsToTopic,    // Entity/Chunk → Topic
    DerivedFrom,       // Chunk → Document
    SimilarTo,         // Entity ↔ Entity
    Next,              // Chunk → Chunk (sequential)
    Custom(String),    // Ontology-defined custom relationship
}

/// A node in the in-memory knowledge graph
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphNode {
    pub id: Uuid,
    pub node_type: NodeType,
    pub label: String,
    pub description: Option<String>,
    pub aliases: Vec<String>,
    pub confidence: f32,
    pub ontology_class: Option<String>,
    pub properties: HashMap<String, serde_json::Value>,
    pub collection_id: Uuid,
}

/// A directed edge in the in-memory knowledge graph
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphEdge {
    pub id: Uuid,
    pub source: Uuid,
    pub target: Uuid,
    pub edge_type: EdgeType,
    pub weight: f32,
    pub context: Option<String>,
    pub chunk_id: Option<Uuid>,
    pub properties: HashMap<String, serde_json::Value>,
}

/// The full in-memory knowledge graph for a collection
#[derive(Debug)]
pub struct KnowledgeGraph {
    /// Primary node store: UUID → GraphNode
    pub nodes: HashMap<Uuid, GraphNode>,

    /// Forward adjacency: source_id → Vec<(edge_id, target_id)>
    pub adjacency_out: HashMap<Uuid, Vec<(Uuid, Uuid)>>,

    /// Reverse adjacency: target_id → Vec<(edge_id, source_id)>
    pub adjacency_in: HashMap<Uuid, Vec<(Uuid, Uuid)>>,

    /// Edge store: edge_id → GraphEdge
    pub edges: HashMap<Uuid, GraphEdge>,

    /// Monotonically increasing version counter for cache invalidation
    pub version: std::sync::atomic::AtomicU64,

    /// Collection this graph belongs to
    pub collection_id: Uuid,
}

impl KnowledgeGraph {
    pub fn new(collection_id: Uuid) -> Self {
        Self {
            nodes: HashMap::new(),
            adjacency_out: HashMap::new(),
            adjacency_in: HashMap::new(),
            edges: HashMap::new(),
            version: std::sync::atomic::AtomicU64::new(0),
            collection_id,
        }
    }

    /// Insert a batch of nodes. Caller must hold write lock.
    pub fn insert_nodes_batch(&mut self, nodes: Vec<GraphNode>) {
        for node in nodes {
            self.nodes.insert(node.id, node);
        }
        self.version.fetch_add(1, std::sync::atomic::Ordering::Release);
    }

    /// Insert a batch of edges. Caller must hold write lock.
    pub fn insert_edges_batch(&mut self, edges: Vec<GraphEdge>) {
        for edge in edges {
            self.adjacency_out
                .entry(edge.source)
                .or_default()
                .push((edge.id, edge.target));
            self.adjacency_in
                .entry(edge.target)
                .or_default()
                .push((edge.id, edge.source));
            self.edges.insert(edge.id, edge);
        }
        self.version.fetch_add(1, std::sync::atomic::Ordering::Release);
    }

    pub fn node_count(&self) -> usize { self.nodes.len() }
    pub fn edge_count(&self) -> usize { self.edges.len() }
}
```

### 4.2 Search Result Types

```rust
/// A single hybrid search result
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchResult {
    pub chunk_id: Uuid,
    pub doc_id: Uuid,
    pub text: String,
    pub contextual_text: String,
    pub vector_score: f32,
    pub keyword_score: f32,
    pub graph_proximity_score: f32,
    pub final_score: f32,
    pub page: Option<i32>,
    pub topics: Vec<String>,
}

/// A subgraph returned from graph traversal queries
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SubGraph {
    pub nodes: Vec<GraphNode>,
    pub edges: Vec<GraphEdge>,
    pub root_id: Uuid,
    pub depth: u32,
}
```

### 4.3 Ingest Job State

```rust
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum JobStatus {
    Pending,
    Running { processed: u32, total: u32, current_file: String },
    Completed { processed: u32, duration_secs: f64 },
    Failed { error: String },
    Cancelled,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IngestJob {
    pub id: Uuid,
    pub collection_id: Uuid,
    pub folder_path: String,
    pub status: JobStatus,
    pub options: IngestOptions,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IngestOptions {
    pub max_cost_usd: Option<f64>,
    pub ocr_enabled: bool,
    pub max_files: usize,            // default 10_000
    pub max_depth: usize,            // default 5
    pub chunk_size_tokens: usize,    // default 512
    pub chunk_overlap_tokens: usize, // default 50
}
```

---

## 5. Multi-Tenancy Table Naming

For LanceDB tables, tenant isolation is enforced by prefixing table names with the collection UUID:

```
{collection_id}_chunks
{collection_id}_nodes
{collection_id}_edges
{collection_id}_documents
{collection_id}_topics
```

This provides hard isolation at the table level with no risk of cross-tenant data leakage from
missing filter clauses. The `IndexManager` maps collection IDs to table handles in its internal
`HashMap<String, Table>`.
