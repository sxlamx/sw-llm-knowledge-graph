# 13 — Development Roadmap

## Overview

The project is divided into four phases. Each phase is independently deployable and delivers
incremental value. The phases are designed so that later phases enhance, rather than replace,
what was built earlier.

```
Phase 1 (Weeks 1-8)    → Functional end-to-end (auth, ingest, basic search)
Phase 2 (Weeks 9-16)   → Graph + ontology + hybrid search + graph viewer
Phase 3 (Weeks 17-24)  → Production hardening (concurrency, caching, monitoring)
Phase 4 (Weeks 25+)    → Advanced features (GDrive, multimodal, agents)
```

---

## Phase 1 — MVP (Core) · Target: 8 Weeks

**Goal**: A working end-to-end system where a user can log in, ingest a folder of documents,
and run basic semantic searches. No graph visualization, no ontology, no hybrid search.

### Rust Core Deliverables

- [ ] Project scaffold with PyO3/Maturin build (`cargo build` → `.so`)
- [ ] `IndexManager` struct: basic LanceDB connection, table creation, state AtomicU8
- [ ] File scanner (`scanner.rs`): recursive scan, BLAKE3 hash, supported extensions
- [ ] Text extractor (`extractor.rs`): PDF, TXT, Markdown extraction
- [ ] Chunker (`chunker.rs`): fixed-size 512-token chunks with 50-token overlap
- [ ] LanceDB ops (`storage/lancedb.rs`): table creation, Arrow RecordBatch insert, vector search
- [ ] Basic vector search: cosine similarity, limit, no filters
- [ ] PyO3 bindings: `PyIndexManager` with `scan_folder`, `insert_chunks`, `vector_search` methods

### Python API Deliverables

- [ ] FastAPI app skeleton: CORS, health check, startup/shutdown hooks
- [ ] Google OAuth: ID token validation (`google-auth` library)
- [ ] RS256 JWT: issue access + refresh tokens, rotation, middleware
- [ ] PostgreSQL: SQLAlchemy models (`users`, `collections`, `ingest_jobs`), Alembic migrations
- [ ] `POST /auth/google`, `POST /auth/refresh`, `POST /auth/logout`
- [ ] `GET/POST/DELETE /collections`
- [ ] `POST /ingest/folder`: start job, dispatch to ingest worker
- [ ] Ingest worker: scan → extract → chunk → embed (OpenAI) → insert to LanceDB
- [ ] `GET /ingest/jobs/{id}`: polling endpoint (no SSE yet)
- [ ] `POST /search`: vector-only search, return top 20 chunks
- [ ] `GET /documents`: list documents in collection
- [ ] OpenAI embedder: `text-embedding-3-large`, batched 100 chunks

### Frontend Deliverables

- [ ] React + Vite + TypeScript project setup
- [ ] Material UI v6 theme (light mode only)
- [ ] Google OAuth login page (`/`)
- [ ] JWT auth flow with RTK Query base API (auto-refresh on 401)
- [ ] Collections dashboard (`/dashboard`): MUI DataGrid, create/delete
- [ ] Collection page (`/collection/:id`): document list, ingest folder input
- [ ] Basic search page (`/search`): query input, result cards (text + doc title + page)
- [ ] Navigation: AppBar + route structure

### Docker Compose

- [ ] `python-api` service with Rust extension bundled
- [ ] `postgres` service with health check
- [ ] Volume mounts for LanceDB, Tantivy, documents
- [ ] `.env.example` with all required variables

### Phase 1 Acceptance Criteria

1. User can log in with Google → lands on dashboard
2. User can create a collection and start an ingest job on a folder of PDFs
3. After ingest, user can run a natural language query and see relevant text chunks
4. Docker Compose `up` starts the full stack from a clean state

---

## Phase 2 — Graph + Ontology · Target: Weeks 9-16

**Goal**: Build the knowledge graph layer, ontology engine, hybrid search, and graph visualization.

### Rust Core Deliverables

- [ ] `OntologyValidator` (`ontology/`): validate entity types, domain/range, confidence threshold
- [ ] `EntityResolver` (`graph/builder.rs`): exact match + Levenshtein + cosine similarity merge
- [ ] `KnowledgeGraph` struct (`models.rs`): nodes/edges/adjacency HashMaps, version AtomicU64
- [ ] Graph construction flow: LLM output → validate → resolve → upsert LanceDB → update petgraph
- [ ] BFS traversal (`graph/traversal.rs`): batched hops, min-weight pruning
- [ ] Dijkstra path finding (`graph/traversal.rs`)
- [ ] `Arc<RwLock<KnowledgeGraph>>` locking pattern (search read-lock, write lock)
- [ ] Tantivy BM25 index (`storage/tantivy.rs`): index chunks, commit batcher (500ms)
- [ ] Hybrid search: 3-channel parallel (`tokio::join!`), score fusion (0.6/0.3/0.1)
- [ ] Topic-based pre-filter: LanceDB `array_has_any` predicate
- [ ] Graph export: GraphML, JSON
- [ ] WAL writer (`wal/writer.rs`): append-only JSON log
- [ ] Startup recovery (`wal/recovery.rs`): reload from LanceDB into petgraph

### Python API Deliverables

- [ ] LLM entity/relation extractor (`llm/extractor.py`): ontology-guided, Pydantic schema
- [ ] Contextual prefix generator (`llm/chunker.py`): GPT-4o-mini, 2-sentence prefix
- [ ] Document summarizer: first 4000 tokens → 200-300 word summary
- [ ] Ontology manager: load from PostgreSQL, JSON schema, `GET/PUT /ontology`
- [ ] `POST /ontology/generate`: LLM-assisted bootstrap from sample docs
- [ ] Graph routers: `GET /graph/nodes/{id}`, `GET /graph/path`, `GET /graph/subgraph`
- [ ] `GET /graph/export?format=json|graphml`
- [ ] `PUT /graph/nodes/{id}`, `POST /graph/edges`, `DELETE /graph/edges/{id}`
- [ ] `GET /topics`, `GET /topics/{id}/nodes`
- [ ] SSE progress stream (`GET /ingest/jobs/{id}/stream`)
- [ ] WebSocket endpoint (`WS /ws`): job progress, graph update events
- [ ] `user_feedback` table: record node edits and edge deletions

### Frontend Deliverables

- [ ] Graph viewer page (`/graph/:collectionId`): `react-force-graph-2d`, node colors by type
- [ ] Graph controls: depth slider (1-4), edge type filter chips, topic filter
- [ ] `NodeDetailPanel`: MUI Drawer, entity properties, linked chunks, edit form
- [ ] `PathFinder` mode: click two nodes → call `GET /graph/path` → highlight path
- [ ] Ontology editor page (`/ontology/:collectionId`): entity type tree, relationship table
- [ ] `TopicSidebar`: topic multi-select for search filtering
- [ ] SSE progress bar in `IngestPanel` (replaces polling)
- [ ] WebSocket middleware integration for real-time graph updates
- [ ] Search mode selector: hybrid / vector / keyword / graph
- [ ] Result cards with BM25 highlights
- [ ] Web Worker for graph layout computation (`graphLayout.worker.ts`)

### Phase 2 Acceptance Criteria

1. After ingest, the graph viewer shows entity nodes and relationships with correct types
2. User can click a node to see its details, linked source chunks, and 1-hop neighbors
3. User can search with topics filter and see relevant filtered results
4. User can edit an entity label and see the change reflected in the graph
5. User can generate an ontology from sample docs and apply it
6. Real-time progress bar updates during ingest (SSE)

---

## Phase 3 — Production Hardening · Target: Weeks 17-24

**Goal**: Full concurrency safety, caching, observability, and security hardening.

### Rust Core Deliverables

- [ ] Full `IndexManager` concurrency model (as specified in `05-index-manager.md`)
  - [ ] `search_semaphore(100)` — bounded concurrent searches
  - [ ] `write_semaphore(1)` — serialized batch writes
  - [ ] Correct lock ordering (Level 2 → Level 3, never reversed)
  - [ ] 800ms search timeout with `tokio::time::timeout`
  - [ ] Graceful degradation (partial results if one channel times out)
- [ ] Atomic shadow table swap (`rebuild_ivf_pq_index`)
  - [ ] Shadow table build in background tokio task
  - [ ] Atomic `tables.write()` pointer swap (~50 microseconds)
  - [ ] Old table freed when in-flight searches complete (Arc refcount)
- [ ] LRU embedding cache: 1000 entries, 5-minute TTL
- [ ] LRU graph neighborhood cache: 500 entries, 2-minute TTL, version-based invalidation
- [ ] Tantivy batch committer: 500ms interval background task
- [ ] Graph pruning background task (hourly): remove low-weight edges, enforce max degree
- [ ] Batch RecordBatch writes (512 rows or 1 second buffer)
- [ ] Rayon parallel validation and BFS
- [ ] WAL checkpoint on startup (truncate after successful recovery)
- [ ] Criterion benchmarks: `search_bench`, `index_bench`

### Python API Deliverables

- [ ] Per-user rate limiter (60 req/min) — Redis-backed for multi-replica support
- [ ] Per-IP rate limiter (200 req/min)
- [ ] File path sanitization: `canonicalize()` + `starts_with()` check
- [ ] ALLOWED_FOLDER_ROOTS validation
- [ ] Collection ownership verification in all Rust bridge calls
- [ ] PostgreSQL row-level security policies on all tables
- [ ] LLM cost tracker per ingest job with `max_cost_usd` cap
- [ ] Exponential backoff on OpenAI 429 errors (tenacity library)
- [ ] Token revocation blocklist for refresh tokens
- [ ] `GET /metrics` endpoint (Prometheus format via `prometheus_client`)

### Frontend Deliverables

- [ ] Proper code splitting (React.lazy for all pages)
- [ ] `react-window` virtualized search results (no render lag at 1000+ results)
- [ ] Error boundary with user-friendly fallback UI
- [ ] 10-concurrent-search-per-user limit (UI shows queue position if exceeded)
- [ ] Dark mode toggle (MUI `useColorScheme`)
- [ ] Graph performance: virtual nodes for clusters > 100 connections
- [ ] Graph max 5000 nodes / 7000 edges renderer cap with warning
- [ ] Proper loading states for all async operations
- [ ] Retry logic for failed API calls (RTK Query `retry` wrapper)

### Testing Deliverables

- [ ] Rust: `index_concurrency_test.rs` — 100 concurrent searches stress test
- [ ] Rust: `search_test.rs` — hybrid search correctness, partial failure handling
- [ ] Rust: `ontology_validation_test.rs` — all edge cases
- [ ] Rust: `entity_resolution_test.rs` — merge/no-merge threshold tests
- [ ] Python: pytest coverage > 80% on routers and LLM pipeline
- [ ] Python: integration tests with mock OpenAI (respx)
- [ ] Frontend: component tests (vitest + testing-library)
- [ ] E2E: Playwright tests for login → ingest → search → graph flow

### Phase 3 Acceptance Criteria

1. 100 simulated concurrent searches complete without deadlock or error
2. Index compaction (shadow swap) completes while searches are in flight
3. All rate limits enforced; 429 responses returned correctly
4. `/metrics` returns valid Prometheus metrics including `kg_concurrent_searches`
5. 80%+ test coverage on Rust core (cargo tarpaulin) and Python API (pytest-cov)

---

## Phase 4 — Advanced Features · Target: Weeks 25+

**Goal**: Expand data sources, add multimodal support, collaborative editing, and agent querying.

### Google Drive Ingestion

- [ ] Google Drive OAuth2 (separate scope from login OAuth)
- [ ] List files from Drive folder using Google Drive API v3
- [ ] Download file content (export for Google Docs)
- [ ] Drive Push Notifications (webhooks) for change detection
- [ ] BLAKE3 hash on Drive file `md5Checksum` for incremental updates
- [ ] Drive file metadata stored in `documents.metadata` JSON

### Multimodal Embeddings

- [ ] Image extraction from PDFs (per-page screenshots via `pdfium`)
- [ ] OpenAI `text-embedding-3-large` for text chunks (existing)
- [ ] Vision model (GPT-4o) for image captions → embed as text
- [ ] Store image chunks with `has_image: true` flag in LanceDB
- [ ] Frontend: show extracted images in search results and node detail

### Real-Time Collaborative Graph Editing

- [ ] WebSocket multi-user room per collection
- [ ] Operational Transformation (OT) or CRDT for concurrent edits
- [ ] Broadcast node/edge changes to all connected clients
- [ ] Presence indicators: show which nodes other users are viewing
- [ ] Conflict resolution: last-write-wins for simple property updates

### Graph Analytics

- [ ] Betweenness centrality (identify key bridge entities) via petgraph
- [ ] PageRank (identify most important entities)
- [ ] Community detection (Louvain algorithm) → auto-assign topic clusters
- [ ] Export analytics results as overlay on graph visualization
- [ ] Timeline view: filter graph by document date range

### Agent-Based Graph RAG

- [ ] Agentic query decomposition: break complex question into sub-queries
- [ ] Graph-aware retrieval: agent traverses graph, collects evidence
- [ ] Multi-hop reasoning: "Who worked at OpenAI and later founded a competitor?"
- [ ] ReAct-style agent loop (OpenAI Assistants API or custom)
- [ ] Streaming response (SSE) for agent reasoning steps

### LLM Fine-Tuning on Domain Feedback

- [ ] Export positive `user_feedback` records as training examples
- [ ] Generate fine-tuning dataset: (document, ontology, expected_extraction)
- [ ] OpenAI fine-tuning API integration
- [ ] A/B test fine-tuned vs base model extraction quality metrics
- [ ] Automated quality metrics: precision/recall vs human-labeled gold set

---

## Key Milestones Summary

| Milestone | Week | Description |
|-----------|------|-------------|
| M1: First Ingest | 4 | End-to-end: PDF → LanceDB → basic vector search |
| M2: Auth Complete | 6 | Google OAuth → JWT → protected API |
| M3: Phase 1 Done | 8 | Full MVP: login, ingest, search, Docker Compose |
| M4: Graph Live | 12 | Entity/relation extraction, petgraph, graph viewer |
| M5: Hybrid Search | 14 | Vector + BM25 + graph-proximity fusion |
| M6: Ontology Stable | 16 | Ontology editor, LLM generation, validation |
| M7: Concurrency Hardened | 20 | 100 concurrent searches, atomic swap, caching |
| M8: Production Ready | 24 | Full security, metrics, 80%+ test coverage |
| M9: GDrive Integration | 28 | Drive OAuth, change detection, incremental sync |
| M10: Graph RAG Agent | 36 | Multi-hop agent querying with streaming |

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| LanceDB API changes between 0.9 versions | Medium | High | Pin exact version in Cargo.lock; integration tests |
| OpenAI API cost overrun during development | Medium | Medium | `max_cost_usd` cap from Phase 1; use GPT-4o-mini for dev |
| PyO3 upgrade breaking changes | Low | High | Pin PyO3 0.22; comprehensive Rust unit tests |
| petgraph memory exhaustion on large corpora | Low | High | Collection eviction policy (Phase 3); max_nodes limit |
| Concurrent graph write contention | Low | High | Write semaphore + brief write lock (specified in Phase 3) |
| LLM hallucination corrupting graph | Medium | Medium | Ontology validator drops invalid types (Phase 2) |
| GDrive OAuth complexity | Medium | Low | Defer to Phase 4; use polling as fallback |
