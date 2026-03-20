# 01 — System Architecture

## 1. System Overview and Objectives

The LLM-Powered Knowledge Graph Builder ingests unstructured documents (PDFs, DOCX, Markdown, HTML,
plain text), extracts named entities and semantic relationships using large language models, and
assembles them into a queryable, navigable knowledge graph. The system is designed for:

- **Knowledge discovery**: surface latent connections across large document corpora
- **Semantic search**: hybrid vector + keyword + graph-proximity retrieval
- **Ontology-guided accuracy**: all extractions validated against a versioned ontology
- **Human-in-the-loop refinement**: users approve, reject, or edit extracted entities and edges
- **Production-grade concurrency**: many simultaneous searches, non-blocking index updates

---

## 2. High-Level Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Browser (React 18 + TypeScript)                    │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  ┌───────────────┐  │
│  │ Auth / Login │  │  Dashboard   │  │ Search UI     │  │ Graph Viewer  │  │
│  │ (Google PKCE)│  │ Collections  │  │ Hybrid search │  │ Cytoscape.js  │  │
│  └──────┬───────┘  └──────┬───────┘  └───────┬───────┘  └───────┬───────┘  │
└─────────┼─────────────────┼──────────────────┼──────────────────┼──────────┘
          │  HTTPS + JWT    │                  │                  │
          ▼                 ▼                  ▼                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Python FastAPI  (Orchestration + LLM)                    │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  Routers: /auth  /collections  /ingest  /search  /graph  /ontology  │   │
│  │  Auth middleware (RS256 JWT validation)                               │   │
│  │  Rate limiter  │  CORS  │  SSE/WebSocket gateway                     │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│  ┌───────────────────┐  ┌──────────────────────────────────────────────┐    │
│  │  LLM Pipeline     │  │  Job Manager (AsyncIO queue)                 │    │
│  │  - embedder.py    │  │  - Ingest worker (per-job async task)        │    │
│  │  - extractor.py   │  │  - SSE progress broadcaster                  │    │
│  │  - chunker.py     │  │  - Job state → PostgreSQL                    │    │
│  │  - ontogpt.py     │  └──────────────────────────────────────────────┘    │
│  └─────────┬─────────┘                                                      │
│            │  PyO3 FFI (in-process)                                         │
└────────────┼───────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Rust Core Engine  (PyO3/Maturin)                     │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐ │
│  │  IndexManager   │  │  SearchEngine   │  │  IngestionEngine            │ │
│  │  State machine  │  │  Hybrid search  │  │  Scanner / Extractor        │ │
│  │  RwLock tables  │  │  Score fusion   │  │  Chunker (text-splitter)    │ │
│  │  Atomic swap    │  │  LRU caches     │  │  Entity resolution          │ │
│  └────────┬────────┘  └────────┬────────┘  └─────────────┬───────────────┘ │
│           │                   │                           │                 │
│  ┌────────▼───────────────────▼───────────────────────────▼──────────────┐  │
│  │                     Storage Abstraction Layer                          │  │
│  │  lancedb.rs (Arrow RecordBatch)  │  tantivy.rs (BM25)  │  graph ops   │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
             │                      │                       │
             ▼                      ▼                       ▼
    ┌─────────────────┐   ┌──────────────────┐   ┌──────────────────────┐
    │    LanceDB      │   │   PostgreSQL      │   │  Tantivy Index       │
    │  (vector store) │   │  (relational      │   │  (on-disk BM25)      │
    │  IVF-PQ index   │   │   metadata)       │   │                      │
    │  MVCC reads     │   │  users,           │   │                      │
    │  Arrow columnar │   │  collections,     │   │                      │
    │  chunks / nodes │   │  jobs, ontology   │   │                      │
    │  edges / docs   │   │  feedback         │   │                      │
    └─────────────────┘   └──────────────────┘   └──────────────────────┘
```

---

## 3. Component Responsibilities

| Component | Language | Responsibility |
|-----------|----------|----------------|
| React Frontend | TypeScript | User interface, auth flow, search, graph visualization, ontology editor |
| FastAPI Python API | Python | HTTP routing, auth middleware, LLM orchestration, job management, SSE streaming |
| LLM Pipeline | Python | Text extraction prompts, contextual prefix generation, embedding batching, entity/relation extraction, ontology generation |
| Rust Core Engine | Rust | High-performance index management, entity resolution, graph construction, hybrid search execution, chunking, file scanning |
| IndexManager | Rust | State machine, RwLock table access, atomic index swap, concurrent search semaphore |
| SearchEngine | Rust | 3-way parallel search (vector + BM25 + graph), score fusion, LRU caching |
| OntologyValidator | Rust | Type-checking entities, domain/range-checking edges, rule-based validation |
| LanceDB | Rust (native) | Persistent vector + columnar storage, IVF-PQ ANN index, MVCC concurrent reads |
| PostgreSQL | SQL | Relational metadata: users, collections, ingest jobs, ontology schema, user feedback |
| Tantivy | Rust (native) | On-disk BM25 full-text index, keyword search |
| petgraph | Rust (native) | In-memory directed property graph for fast traversal |

---

## 4. Process Topology

### 4.1 Single Binary vs Services

The Rust core is compiled as a **Python extension module** (`.so` / `.pyd`) via **Maturin**. It runs
**in-process** within the Python FastAPI server — no separate Rust process, no IPC overhead.

```
python-api process
├── FastAPI ASGI (uvicorn)
├── PyO3 bridge (librust_core.so loaded at startup)
│   └── Rust IndexManager, SearchEngine, IngestionEngine (same heap)
└── AsyncIO event loop (LLM calls, DB queries, SSE)
```

PyO3 allows Rust functions to be called directly from Python as if they were Python objects. The
GIL is released during long-running Rust operations (annotated with `py.allow_threads()`), allowing
true parallelism between Rust work and Python I/O.

### 4.2 PyO3 Binding Pattern

```rust
// rust-core/src/lib.rs
use pyo3::prelude::*;

#[pyclass]
pub struct PyIndexManager {
    inner: Arc<IndexManager>,
}

#[pymethods]
impl PyIndexManager {
    #[new]
    pub fn new(db_path: &str) -> PyResult<Self> { ... }

    pub fn search(&self, py: Python, query_embedding: Vec<f32>, limit: usize)
        -> PyResult<Vec<PySearchResult>>
    {
        // Release GIL during Rust search operation
        py.allow_threads(|| {
            self.inner.search_blocking(query_embedding, limit)
        }).map_err(|e| PyRuntimeError::new_err(e.to_string()))
    }
}

#[pymodule]
fn rust_core(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_class::<PyIndexManager>()?;
    m.add_class::<PySearchEngine>()?;
    m.add_class::<PyIngestionEngine>()?;
    Ok(())
}
```

### 4.3 Async Bridge

Rust async (Tokio) is bridged to Python async (AsyncIO) via a dedicated Tokio runtime spawned at
module load time. Python calls Rust functions synchronously in `run_in_executor` to avoid blocking
the FastAPI event loop:

```python
# python-api/app/core/rust_bridge.py
import asyncio
from concurrent.futures import ThreadPoolExecutor
from rust_core import PyIndexManager

_executor = ThreadPoolExecutor(max_workers=16)
_index_manager = PyIndexManager(db_path="/data/lancedb")

async def search(query_embedding: list[float], limit: int):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _executor,
        _index_manager.search,
        query_embedding,
        limit
    )
```

---

## 5. Key Design Principles

### 5.1 Separation of Concerns

- **Python** handles all LLM API calls, authentication logic, HTTP routing, and job orchestration.
  Python's ecosystem (OpenAI SDK, google-auth, FastAPI, SQLAlchemy) is unmatched for these tasks.
- **Rust** handles all performance-critical operations: index management, concurrent search,
  entity resolution, graph traversal, file scanning, and chunking. Rust's ownership model
  guarantees memory safety without garbage collection pauses.
- **Frontend** is a pure presentation layer — it never talks to the Rust core directly.

### 5.2 Hybrid Storage Strategy

| Data Type | Storage | Reason |
|-----------|---------|--------|
| Vector embeddings (1536-dim) | LanceDB | ANN search with IVF-PQ, columnar Arrow layout |
| Graph topology | petgraph (hot) + LanceDB edges (cold) | Fast BFS in memory, persistent recovery |
| Full-text content | Tantivy | BM25 ranking, inverted index |
| Relational metadata | PostgreSQL | ACID transactions, joins, row-level security |

### 5.3 Ontology-First Extraction

All LLM-extracted entities and relationships are validated against a loaded ontology before entering
the graph. Invalid types are dropped, logged, and surfaced as warnings. This prevents graph
pollution from hallucinated entity types.

### 5.4 Zero-Copy Arrow Data Path

LanceDB uses Apache Arrow internally. The Rust engine writes `RecordBatch` objects directly without
serialization overhead. Reads return Arrow arrays that are zero-copy sliced for embedding lookup.

---

## 6. Deployment: Docker Compose Layout

```yaml
# docker/docker-compose.yml (abbreviated)
version: "3.9"

services:
  rust-core:
    build:
      context: ../rust-core
      dockerfile: ../docker/Dockerfile.rust
    # NOTE: rust-core is a Python extension, NOT a standalone service.
    # This service entry is for build/test purposes only.
    # In production it is loaded in-process by python-api.

  python-api:
    build:
      context: ../python-api
      dockerfile: ../docker/Dockerfile.api
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://kg:secret@postgres:5432/kg
      - LANCEDB_PATH=/data/lancedb
      - TANTIVY_PATH=/data/tantivy
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - JWT_PRIVATE_KEY_PATH=/run/secrets/jwt_private_key
      - GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}
    volumes:
      - lancedb-data:/data/lancedb
      - tantivy-data:/data/tantivy
      - doc-storage:/data/documents
    depends_on:
      - postgres
    secrets:
      - jwt_private_key

  frontend:
    build:
      context: ../frontend
      dockerfile: ../docker/Dockerfile.frontend
    ports:
      - "3000:80"
    environment:
      - VITE_API_BASE_URL=http://python-api:8000/api/v1
      - VITE_GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: kg
      POSTGRES_USER: kg
      POSTGRES_PASSWORD: secret
    volumes:
      - pg-data:/var/lib/postgresql/data

volumes:
  lancedb-data:
  tantivy-data:
  doc-storage:
  pg-data:

secrets:
  jwt_private_key:
    file: ./secrets/jwt_private_key.pem
```

---

## 7. Why Rust Core

| Concern | Rust Advantage |
|---------|---------------|
| Memory safety | No garbage collector, no dangling pointers, ownership model enforced at compile time |
| Concurrency | `Send`/`Sync` traits enforce thread safety at compile time; no data races |
| Performance | Zero-cost abstractions, no GIL, predictable latency (no GC pauses) |
| Arrow integration | Native `arrow-array` crate; zero-copy RecordBatch operations |
| LanceDB | Rust-native client; same process, no IPC |
| petgraph | Mature graph library in Rust; efficient adjacency lists |
| Tantivy | Rust-native BM25; same binary, no subprocess |

---

## 8. Technology Justification Table

| Technology | Chosen For | Alternatives Considered |
|------------|-----------|------------------------|
| LanceDB | Serverless vector DB with Arrow columnar layout, MVCC, IVF-PQ ANN | Qdrant (separate process), Weaviate (heavy), pgvector (slower ANN) |
| FastAPI | Async Python HTTP, Pydantic validation, OpenAPI docs, SSE support | Flask (no async), Django (too heavy) |
| petgraph | Fast in-memory graph traversal, Dijkstra, BFS, DFS included | NetworkX (Python, slow), custom adjacency list |
| Tantivy | Rust-native BM25, same binary, no subprocess | Elasticsearch (heavy, separate service), Meilisearch |
| PyO3/Maturin | In-process Rust from Python, GIL release support, mature ecosystem | CFFI (no async), gRPC (IPC overhead) |
| React 18 + Vite | Fast dev build, concurrent rendering, large ecosystem | Next.js (SSR overkill), SvelteKit |
| Material UI v6 | Comprehensive component library, TypeScript-first | Chakra UI, Ant Design |
| Redux Toolkit + RTK Query | Structured state, auto-caching, cache invalidation | Zustand (less structured), React Query alone |
| Cytoscape.js / react-force-graph-2d | Canvas-based graph rendering, handles 5000+ nodes | D3.js (SVG, slow at scale), vis.js |
| BLAKE3 | Fast cryptographic hashing for change detection | SHA-256 (slower), MD5 (insecure) |
| Google OAuth 2.0 | Widely trusted, PKCE-safe, no password management | Auth0 (cost), custom auth (maintenance burden) |
