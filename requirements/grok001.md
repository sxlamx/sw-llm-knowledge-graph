**Software Requirements Specification (SRS)**  
**LLM-Powered Rust Knowledge Graph Builder**  
**Version 1.0**  
**Date:** March 19, 2026  

### 1. Introduction

#### 1.1 Purpose
Develop a web application that automatically ingests document collections, uses Large Language Models (LLMs) to extract entities, relations, and topics, builds and persists a hybrid knowledge graph + vector index, and provides intuitive search, navigation, and topic-based filtering. The system must be performant, secure, and extensible for future cloud storage (Google Drive).

#### 1.2 Scope
**In Scope (MVP – Phase 1):**
- Local folder document ingestion
- LLM-driven entity/relation/topic extraction
- Hybrid storage in LanceDB (vector + relational tables)
- Semantic + graph search
- Graph navigation & visualization
- Topic filtering
- Google OAuth + JWT authentication
- React Material-UI frontend

**Out of Scope (Phase 2):**
- Google Drive API integration
- Real-time collaboration
- Multi-user access control beyond ownership
- Export to external graph DBs (Neo4j, etc.)

#### 1.3 Definitions
- **Node**: Entity, Topic, or Document Chunk with embedding
- **Edge**: Directed relation (subject → predicate → object)
- **Topic**: High-level concept extracted by LLM (used for filtering)
- **LanceDB Table**: `nodes`, `edges`, `chunks` with vector columns

### 2. System Architecture & Tech Stack (Mandated)

**Backend (Core)**
- Rust (primary language) – performance-critical paths
  - LanceDB Rust crate (native vector + table operations)
  - `pyo3` + `maturin` to expose Rust libraries to Python
  - `axum` or FastAPI (Python) for REST/gRPC API
  - `tokio` for async I/O
  - `petgraph` or custom adjacency list in LanceDB for graph traversal

**Python Layer (Orchestration & LLM)**
- Python 3.11+ wrapper (PyO3 bindings)
- LLM calls (OpenAI, Anthropic, or local via Ollama/Llama.cpp)
- Document parsing (`unstructured`, `pypdf`, `python-docx`)
- Embedding model calls (OpenAI text-embedding-3-large or local `sentence-transformers`)
- LangChain/LlamaIndex-style pipelines (optional)

**Frontend**
- React 18 + Vite.js
- Material-UI (MUI) v6
- Graph visualization: React Cytoscape.js or react-force-graph
- State: Redux Toolkit + RTK Query

**Authentication**
- Google Identity (OAuth 2.0)
- JWT (RS256) issued by backend (10 min access, 7 day refresh)
- Stored in HttpOnly cookie + localStorage (refresh token)

**Deployment**
- Docker Compose (Rust + Python + LanceDB + React)
- LanceDB persistent storage on host volume

### 3. Functional Requirements

#### 3.1 Authentication & User Management
| ID | Requirement |
|----|-------------|
| FR-01 | Google OAuth 2.0 login button on landing page |
| FR-02 | Backend validates Google ID token → issues JWT |
| FR-03 | All API endpoints (except `/auth`) require valid JWT |
| FR-04 | User profile stored in LanceDB `users` table (id, email, created_at) |
| FR-05 | Logout clears tokens |

#### 3.2 Document Ingestion (Phase 1 – Local Folders)
| ID | Requirement |
|----|-------------|
| FR-06 | User selects local folder(s) via frontend (drag-drop or folder picker) |
| FR-07 | Backend recursively scans folders (max depth 5, max 10 000 files) |
| FR-08 | Supported formats: `.pdf`, `.txt`, `.md`, `.docx`, `.html` |
| FR-09 | Chunking: 512-token overlapping chunks (overlap 50 tokens) |
| FR-10 | Each chunk stored in LanceDB `chunks` table with `doc_path`, `page`, `text`, `embedding` |

#### 3.3 Knowledge Graph Construction (LLM-Powered)
| ID | Requirement |
|----|-------------|
| FR-11 | For every chunk, LLM prompt extracts: (1) entities (name, type, description), (2) relations (subject-predicate-object triples), (3) topics (max 5 per doc) |
| FR-12 | LLM temperature = 0.0, structured JSON output enforced via Pydantic/OpenAI tools |
| FR-13 | Rust service merges duplicate entities (fuzzy + embedding cosine > 0.92) |
| FR-14 | Nodes stored in LanceDB `nodes` table: `id` (UUID), `label`, `type` (Person/Org/Topic/Concept), `embedding` (1536-dim), `metadata` (JSON) |
| FR-15 | Edges stored in LanceDB `edges` table: `from_id`, `to_id`, `predicate`, `weight` (confidence score) |
| FR-16 | Topics extracted → stored as special nodes + linked to chunks via edges |

#### 3.4 Indexing
| ID | Requirement |
|----|-------------|
| FR-17 | Automatic vector indexing on `nodes.embedding` and `chunks.embedding` (LanceDB IVF_PQ index) |
| FR-18 | Graph adjacency list cached in Rust memory (re-built on every ingest batch) |
| FR-19 | Full re-index option + incremental update (detect changed files via hash) |

#### 3.5 Search
| ID | Requirement |
|----|-------------|
| FR-20 | Hybrid search: vector (cosine) + keyword + graph traversal |
| FR-21 | Search bar accepts natural language query |
| FR-22 | Results ranked by: (embedding similarity × 0.6) + (graph distance × 0.3) + (topic match × 0.1) |
| FR-23 | Return: nodes, connected edges (depth 2), source chunks |

#### 3.6 Graph Navigation & Visualization
| ID | Requirement |
|----|-------------|
| FR-24 | Interactive graph viewer (Cytoscape.js) showing nodes & edges |
| FR-25 | Click node → show details panel + linked chunks |
| FR-26 | Expand/collapse neighbors (depth control 1-4) |
| FR-27 | Force-directed layout + zoom/pan |
| FR-28 | “Path finder” between two selected nodes |

#### 3.7 Topic Filtering
| ID | Requirement |
|----|-------------|
| FR-29 | Sidebar list of all extracted topics (sorted by frequency) |
| FR-30 | Multi-select topics → filters graph view and search results |
| FR-31 | “Topics only” mode shows only topic nodes and their connections |

#### 3.8 User Interface Requirements
- Responsive Material-UI design
- Dark/light theme
- Progress bar during ingestion & graph building
- Document preview pane (PDF.js for PDFs)
- Export graph as JSON/GraphML

### 4. Non-Functional Requirements

| Category | Requirement |
|----------|-------------|
| Performance | • Ingest + graph build ≤ 3 s per 10 pages<br>• Search latency < 800 ms (P95)<br>• Graph render ≤ 60 FPS for < 5 000 nodes |
| Scalability | • 100 000 documents / 1 million nodes on single machine (16 GB RAM) |
| Security | • JWT + Google OAuth only<br>• File paths sanitized<br>• No LLM prompt injection (structured output + guardrails) |
| Reliability | • LanceDB ACID transactions per batch<br>• Idempotent ingest (duplicate detection) |
| Usability | • Intuitive Material-UI<br>• Tooltips & onboarding tour |
| Maintainability | • 80 % Rust code coverage<br>• OpenAPI spec for all endpoints |

### 5. Data Models (LanceDB Schemas)

**nodes** table
- id: UUID (primary)
- label: String
- type: String (enum)
- embedding: FixedSizeList<Float32, 1536>
- metadata: JSON
- created_at: Timestamp

**edges** table
- id: UUID
- from_id: UUID (foreign)
- to_id: UUID (foreign)
- predicate: String
- weight: Float32
- created_at: Timestamp

**chunks** table
- id: UUID
- doc_path: String
- text: String
- embedding: FixedSizeList<Float32, 1536>
- node_ids: List<UUID> (linked entities)

### 6. API Endpoints (REST)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/auth/google` | Exchange Google token → JWT |
| POST | `/ingest/folder` | Start folder ingestion job |
| GET  | `/status/ingest/{job_id}` | Progress |
| POST | `/search` | Hybrid search |
| GET  | `/graph/nodes/{id}` | Node details + neighbors |
| GET  | `/topics` | List all topics |
| GET  | `/graph/export` | JSON/GraphML |

### 7. Acceptance Criteria & Test Strategy
- 100 % of FR-01 to FR-31 covered by automated tests (Rust + Playwright)
- Manual testing: 10 000-document corpus, 5 concurrent users
- Performance benchmarks using `criterion` (Rust) and Lighthouse (frontend)

### 8. Future Extensions (Phase 2)
- Google Drive OAuth + real-time watch
- Multi-tenancy
- LLM fine-tuning on domain data
- Graph analytics (centrality, community detection)

This specification is complete, unambiguous, and directly maps to the mandated tech stack (LanceDB + Rust core + Python LLM layer + React MUI + Google JWT). It serves as the single source of truth for development, testing, and stakeholder alignment.
