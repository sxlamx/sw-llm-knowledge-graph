# 13 — Development Roadmap

> **Status reconciled 2026-06-30** — Checkboxes were reconciled against the actual codebase
> (rust-core, python-api, frontend). The prior version under-counted: Phase 3 and Phase 4
> had substantial implementations marked incomplete. Items below reflect verified code state.
> Inline notes call out intentional deviations (e.g. in-memory vs Redis rate limiting,
> last-write-wins vs OT/CRDT, Ollama/Qwen embeddings vs OpenAI).

## Overview

The project is divided into four phases. Each phase is independently deployable and delivers
incremental value. The phases are designed so that later phases enhance, rather than replace,
what was built earlier.

```
Phase 1 (Weeks 1-8)    → Functional end-to-end (auth, ingest, basic search)        ✅ COMPLETE
Phase 2 (Weeks 9-16)   → Graph + ontology + hybrid search + graph viewer           ✅ COMPLETE
Phase 3 (Weeks 17-24)  → Production hardening (concurrency, caching, monitoring)    🟡 ~85% (shadow-swap + Redis RL + coverage remain)
Phase 4 (Weeks 25+)    → Advanced features (GDrive, multimodal, agents)             🟡 ~80% (Hyper-Extract F1–F10 is the big remaining block)
```

---

## Phase 1 — MVP (Core) · Target: 8 Weeks

**Goal**: A working end-to-end system where a user can log in, ingest a folder of documents,
and run basic semantic searches. No graph visualization, no ontology, no hybrid search.

### Rust Core Deliverables

- [x] Project scaffold with PyO3/Maturin build (`cargo build` → `.so`)
- [x] `IndexManager` struct: basic LanceDB connection, table creation, state AtomicU8
- [x] File scanner (`scanner.rs`): recursive scan, BLAKE3 hash, supported extensions
- [x] Text extractor (`extractor.rs`): PDF, TXT, Markdown extraction (Python: pymupdf)
- [x] Chunker (`chunker.rs`): fixed-size 512-token chunks with 50-token overlap
- [x] LanceDB ops (`storage/lancedb.rs`): table creation, Arrow RecordBatch insert, vector search
- [x] Basic vector search: cosine similarity, limit, no filters
- [x] PyO3 bindings: `IndexManager` with `scan_folder`, `insert_chunks`, `vector_search` methods

### Python API Deliverables

- [x] FastAPI app skeleton: CORS, health check, startup/shutdown hooks
- [x] Google OAuth: ID token validation (`google-auth` library)
- [x] RS256 JWT: issue access + refresh tokens, rotation, middleware
- [x] LanceDB system tables for users/collections/ingest_jobs (replaces PostgreSQL — see `02-data-models.md`)
- [x] `POST /auth/google`, `POST /auth/refresh`, `POST /auth/logout`
- [x] `GET/POST/DELETE /collections`
- [x] `POST /ingest/folder`: start job, dispatch to ingest worker
- [x] Ingest worker: scan → extract → chunk → embed (HuggingFace local) → insert to LanceDB
- [x] `GET /ingest/jobs/{id}`: polling endpoint
- [x] `POST /search`: vector-only search, return top 20 chunks
- [x] `GET /documents`: list documents in collection
- [x] HuggingFace embedder: `Qwen/Qwen3-Embedding-0.6B`, local GPU, 1024-dim (replaces OpenAI)

### Frontend Deliverables

- [x] React + Vite + TypeScript project setup
- [x] Material UI v6 theme (light mode only)
- [x] Google OAuth login page (`/`)
- [x] JWT auth flow with RTK Query base API (auto-refresh on 401)
- [x] Collections dashboard (`/dashboard`): MUI DataGrid, create/delete
- [x] Collection page (`/collection/:id`): document list, ingest folder input
- [x] Basic search page (`/search`): query input, result cards (text + doc title + page)
- [x] Navigation: AppBar + route structure

### Docker Compose

- [x] `python-api` service with Rust extension bundled
- [ ] ~~`postgres` service~~ — replaced by LanceDB system tables
- [x] Volume mounts for LanceDB, documents
- [x] `.env.example` with all required variables

### Phase 1 Acceptance Criteria

1. ✅ User can log in with Google → lands on dashboard
2. ✅ User can create a collection and start an ingest job on a folder of PDFs
3. ✅ After ingest, user can run a natural language query and see relevant text chunks
4. ✅ Docker Compose `up` starts the full stack from a clean state

---

## Phase 2 — Graph + Ontology · Target: Weeks 9-16

**Goal**: Build the knowledge graph layer, ontology engine, hybrid search, and graph visualization.

### Rust Core Deliverables

- [x] `OntologyValidator` (`ontology/`): validate entity types, domain/range, confidence threshold
- [x] `EntityResolver` (`graph/builder.rs`): exact match + Levenshtein + cosine similarity merge
- [x] `KnowledgeGraph` struct (`models.rs`): nodes/edges/adjacency HashMaps, version AtomicU64
- [x] Graph construction flow: NER/LLM output → validate → resolve → upsert LanceDB → update petgraph
- [x] BFS traversal (`graph/traversal.rs`): batched hops, min-weight pruning
- [x] Dijkstra path finding (`graph/traversal.rs`)
- [x] `Arc<RwLock<KnowledgeGraph>>` locking pattern (search read-lock, write lock)
- [x] Tantivy BM25 index (`storage/tantivy.rs`): index chunks, commit batcher (500ms)
- [x] Hybrid search: 3-channel parallel (`tokio::join!`), score fusion (0.6/0.3/0.1)
- [x] Topic-based pre-filter: topics filter applied to vector channel (`search_service.py`)
- [x] Graph export: JSON (GraphML deferred)
- [x] WAL writer (`wal/writer.rs`): append-only JSON log
- [x] Startup recovery (`wal/recovery.rs`): reload from LanceDB into petgraph, checkpoint-on-startup

### Python API Deliverables

- [x] LLM entity/relation extractor (`llm/extractor.py`): ontology-guided, Pydantic schema (config-gated)
- [x] Contextual prefix generator (`llm/chunker.py`): 2-sentence prefix (config-gated, `ENABLE_CONTEXTUAL_PREFIX`)
- [x] Document summarizer: first 4000 tokens → 200-300 word summary
- [x] **NER pipeline** (`llm/ner_tagger.py`): spaCy `en_core_web_trf` + legal LLM labels (always-on)
- [x] **Graph construction from NER** (`build_graph_from_ner.py`): batch graph build from ner_tags
- [x] Ontology manager: JSON schema, `GET/PUT /ontology`
- [x] `POST /ontology/generate`: LLM-assisted bootstrap from sample docs
- [x] Graph routers: `GET /graph/nodes/{id}`, `GET /graph/path`, `GET /graph/subgraph`
- [x] `GET /graph/export?format=json`
- [x] `PUT /graph/nodes/{id}`, `POST /graph/edges`, `DELETE /graph/edges/{id}`
- [x] `GET /topics`, `GET /topics/{id}/nodes`
- [x] SSE progress stream (`GET /ingest/jobs/{id}/stream`)
- [x] WebSocket endpoint (`WS /ws`): job progress, graph update events
- [x] `user_feedback` table: record node edits and edge deletions (`db/models.py`, written in `graph.py`)

### Frontend Deliverables

- [x] Graph viewer page (`/graph/:collectionId`): `react-force-graph-2d`, node colors by canonical type
- [x] Graph controls: depth slider (1-4), edge type filter chips, topic filter
- [x] `NodeDetailPanel`: MUI Drawer, entity properties, linked chunks, edit form
- [x] `PathFinder` mode: click two nodes → call `GET /graph/path` → highlight path
- [x] Ontology editor page (`/ontology/:collectionId`): entity type tree, relationship table
- [x] `TopicSidebar`: topic multi-select for search filtering
- [x] SSE progress bar in `IngestPanel` (replaces polling — uses `EventSource`)
- [x] WebSocket middleware integration for real-time graph updates
- [x] Search mode selector: hybrid / vector / keyword / graph
- [x] Result cards with BM25 highlights (`ResultCard.tsx` renders `result.highlights`)
- [x] Web Worker for graph layout computation (`graphLayout.worker.ts`)
- [x] Node label toggle (LabelIcon/LabelOffIcon in graph toolbar)

### Phase 2 Acceptance Criteria

1. ✅ After ingest, the graph viewer shows entity nodes and relationships with correct types
2. ✅ User can click a node to see its details, linked source chunks, and 1-hop neighbors
3. ✅ User can search with topics filter and see relevant filtered results
4. ✅ User can edit an entity label and see the change reflected in the graph
5. ✅ User can generate an ontology from sample docs and apply it
6. ✅ Real-time progress bar updates during ingest (SSE)

---

## Phase 3 — Production Hardening · Target: Weeks 17-24

**Goal**: Full concurrency safety, caching, observability, and security hardening.

### Rust Core Deliverables

- [x] Full `IndexManager` concurrency model (as specified in `05-index-manager.md`)
  - [x] `search_semaphore(100)` — bounded concurrent searches
  - [x] `write_semaphore(1)` — serialized batch writes
  - [x] Correct lock ordering (Level 2 → Level 3, never reversed)
  - [x] Search timeout with `tokio::time::timeout` (per-channel: vector=600ms, keyword=200ms, graph=300ms — total ~800ms budget)
  - [x] Graceful degradation (partial results if one channel times out)
- [ ] Atomic shadow table swap (`rebuild_ivf_pq_index`) — **NOT YET IMPLEMENTED** (the headline Phase 3 feature)
  - [ ] Shadow table build in background tokio task
  - [ ] Atomic `tables.write()` pointer swap (~50 microseconds)
  - [ ] Old table freed when in-flight searches complete (Arc refcount)
- [x] LRU embedding cache: 1000 entries, 5-minute TTL (`TimedLruCache` `query_embedding_cache`)
- [x] LRU graph neighborhood cache: 500 entries, 2-minute TTL, version-based invalidation (`graph_neighbor_cache`)
- [x] Tantivy batch committer: 500ms interval background task (`_tantivy_commit_loop` in `main.py`)
- [x] Graph pruning background task: `prune_graph(collection, min_weight, max_degree=100)` scheduled via `rust_bridge.py`
- [x] Batch RecordBatch writes (`insert_nodes_batch` / `insert_edges_batch`)
- [x] Rayon parallel validation (`ontology/validator.rs`) and BFS
- [x] WAL checkpoint on startup (`checkpoint_on_startup` truncates after successful recovery)
- [x] Criterion benchmarks: `search_bench`, `index_bench`, `concurrent_search_bench` (`rust-core/benches/`)

### Python API Deliverables

- [x] Per-user rate limiter (`RateLimiter` in `auth/middleware.py`) — **in-memory sliding window; Redis backing for multi-replica deferred**
- [x] Per-IP rate limiter (`per_ip_limit = per_user_limit * 3`)
- [x] File path sanitization: `canonicalize()` + `starts_with()` check (`core/path_sanitizer.py`)
- [x] ALLOWED_FOLDER_ROOTS validation
- [x] Collection ownership verification (`collections.py` checks `user_id`; enforced at router layer)
- [ ] PostgreSQL row-level security policies — **deferred (PostgreSQL not deployed; LanceDB holds metadata)**
- [x] LLM cost tracker per ingest job with `max_cost_usd` cap (`cost_tracker.py` `BudgetExceededError`)
- [x] Exponential backoff on 429 errors (`tenacity` `wait_exponential` in `llm/extractor.py`)
- [x] Token revocation blocklist for refresh tokens (`auth/jwt.py` `_revoked_tokens` + LanceDB persistence)
- [x] `GET /metrics` endpoint (Prometheus format via `prometheus_client`, incl. `kg_concurrent_searches`)

### Frontend Deliverables

- [x] Proper code splitting (React.lazy for all pages — `App.tsx`)
- [x] `react-window` virtualized search results (`SearchResults.tsx` `FixedSizeList`)
- [x] Error boundary with user-friendly fallback UI (`components/common/ErrorBoundary.tsx`)
- [ ] 10-concurrent-search-per-user limit (UI queue position) — **NOT IMPLEMENTED** (global semaphore only; no per-user queue UI)
- [x] Dark mode toggle (MUI `useColorScheme` via `ThemeProvider`, toggle in `NavBar`/`Settings`)
- [ ] Graph performance: virtual nodes for clusters > 100 connections — **NOT IMPLEMENTED** (cluster labels exist, but no node collapse/virtualization)
- [x] Graph max 5000 nodes / 7000 edges renderer cap with warning (`ForceGraph.tsx` `MAX_NODES`/`MAX_EDGES`)
- [x] Proper loading states for all async operations (`LoadingOverlay`, per-component spinners)
- [ ] Retry logic for failed API calls (RTK Query `retry` wrapper) — **NOT IMPLEMENTED** (only 401 reauth in `baseQueryWithReauth`; no retry-with-backoff)

### Testing Deliverables

- [x] Rust: `index_concurrency_test.rs` — 100 concurrent searches stress test
- [x] Rust: `search_test.rs` — hybrid search correctness, partial failure handling
- [x] Rust: `ontology_validation_test.rs` — all edge cases
- [x] Rust: `entity_resolution_test.rs` — merge/no-merge threshold tests
- [ ] Python: pytest coverage > 80% on routers and LLM pipeline — **unverified** (test suite exists; coverage % not measured here)
- [x] Python: integration tests with mocked LLM (`unittest.mock`/`patch` in `conftest.py`; note: `respx` not used)
- [x] Frontend: component tests (vitest + testing-library — `__tests__/` covers API slices, pages, components)
- [x] E2E: Playwright tests for login → ingest → search → graph flow (`e2e/tests/`)

### Phase 3 Acceptance Criteria

1. ✅ 100 simulated concurrent searches complete without deadlock or error (`index_concurrency_test.rs`)
2. ⬜ Index compaction (shadow swap) completes while searches are in flight — **blocked on shadow-swap impl**
3. ✅ All rate limits enforced; 429 responses returned correctly
4. ✅ `/metrics` returns valid Prometheus metrics including `kg_concurrent_searches`
5. ⬜ 80%+ test coverage on Rust core (cargo tarpaulin) and Python API (pytest-cov) — **unverified**

---

## Phase 4 — Advanced Features · Target: Weeks 25+

**Goal**: Expand data sources, add multimodal support, collaborative editing, and agent querying.

> **Note**: The Hyper-Extract feature set (spec `15-hyper-extract-integration.md`, F1–F10) is a
> Phase 4 scope addition that is **entirely unstarted** — see the dedicated section at the end
> of this phase. It is the single largest remaining workstream.

### Google Drive Ingestion

- [x] Google Drive OAuth2 (separate scope from login OAuth)
- [x] List files from Drive folder using Google Drive API v3
- [x] Download file content (export for Google Docs)
- [x] Drive Push Notifications (webhooks) for change detection (`register_watch_channel` / `deregister_watch_channel`)
- [x] Stable hash on Drive file `md5Checksum` for incremental updates (`drive_hash` — uses md5Checksum, falls back to md5)
- [x] Drive file metadata stored in `documents.metadata` JSON (`drive_hash` etc.)

### Multimodal Embeddings

- [x] Image extraction from PDFs (per-page thumbnails via `pdfium2`, `poppler` fallback) — `multimodal_service.py`
- [ ] ~~OpenAI `text-embedding-3-large` for text chunks~~ — **obsolete**: project uses local `Qwen/Qwen3-Embedding-0.6B`
- [x] Vision model for image captions → embed as text
- [x] Store image chunks with `has_image: true` flag in LanceDB
- [x] Frontend: show extracted images in search results (`ResultCard`) and node detail (`NodeDetailPanel`)

### Real-Time Collaborative Graph Editing

- [x] WebSocket multi-user room per collection (`routers/ws.py` room model, `useCollabRoom.ts`)
- [ ] Operational Transformation (OT) or CRDT for concurrent edits — **NOT IMPLEMENTED** (last-write-wins used instead, see below)
- [x] Broadcast node/edge changes to all connected clients (`room.broadcast`)
- [x] Presence indicators: show which nodes other users are viewing (`setPresence`/`removePresence`, presence ring in `ForceGraph`)
- [x] Conflict resolution: last-write-wins for simple property updates (`ws.py` per-field `ts` comparison)

### Graph Analytics

- [x] Betweenness centrality (identify key bridge entities) — `analytics_service.py` (BFS-based Brandes)
- [x] PageRank (identify most important entities)
- [x] Community detection (Louvain algorithm) → auto-assign topic clusters
- [x] Export analytics results as overlay on graph visualization (`AnalyticsPanel` in `GraphViewer`, node size = PageRank)
- [x] Timeline view: filter graph by document date range (`date_from`/`date_to` in `GraphViewer`)

### Agent-Based Graph RAG

- [x] Agentic query decomposition: break complex question into sub-queries
- [x] Graph-aware retrieval: agent traverses graph, collects evidence
- [x] Multi-hop reasoning: "Who worked at OpenAI and later founded a competitor?"
- [x] ReAct-style agent loop (`services/agent_service.py`)
- [x] Streaming response (SSE) for agent reasoning steps

### LLM Fine-Tuning on Domain Feedback

- [x] Export positive `user_feedback` records as training examples (`build_training_dataset`)
- [x] Generate fine-tuning dataset: (document, ontology, expected_extraction) — JSONL in OpenAI format
- [x] OpenAI fine-tuning API integration (`fine_tuning.jobs.create` / `retrieve`)
- [x] A/B test fine-tuned vs base model extraction quality metrics (`finetune_service.py` A/B evaluation, `routers/finetune.py`)
- [x] Automated quality metrics: precision/recall/F1 vs labeled set

### Hyper-Extract Feature Integration (spec `15`) — 🟡 UNSTARTED

None of the F1–F10 features from `15-hyper-extract-integration.md` are implemented. This is the
largest remaining workstream. No `templates/` directory exists; no `TemplateConfig`,
`EntityMerger`, `MergeStrategy`, `search_nodes`/`search_edges`, or `merge_into_collection`.

- [ ] **F1** Declarative YAML extraction templates (+ `TemplateGallery`/`TemplateFactory`, API, `TemplatePicker` UI)
- [ ] **F2** Two-stage extraction (nodes first, then edges with known-node context)
- [ ] **F3** LLM-powered entity/edge field-level merging (`MergeStrategy` enum, `EntityMerger`)
- [ ] **F4** Incremental document feeding (`POST /collections/{id}/feed`, `merge_into_collection`)
- [ ] **F5** Knowledge chat (vector search + LLM Q&A over extracted entities/relations, `/chat`, `ChatPanel`)
- [ ] **F6** Temporal and spatial graph dimensions (`time`/`location` edge fields, composite dedup keys)
- [ ] **F7** Hyperedge support (n-ary relations via `participants` field, full-participant dangling pruning)
- [ ] **F8** Domain template library (finance, legal, medical, industry, general presets)
- [ ] **F9** Extraction method registry (multiple algorithms)
- [ ] **F10** Structured identifiers & display label templates (`KeyCompiler`, `display_label`/`dedup_key` on nodes/edges)

---

## Key Milestones Summary

| Milestone | Week | Description | Status |
|-----------|------|-------------|--------|
| M1: First Ingest | 4 | End-to-end: PDF → LanceDB → basic vector search | ✅ |
| M2: Auth Complete | 6 | Google OAuth → JWT → protected API | ✅ |
| M3: Phase 1 Done | 8 | Full MVP: login, ingest, search, Docker Compose | ✅ |
| M4: Graph Live | 12 | Entity/relation extraction, petgraph, graph viewer | ✅ |
| M5: Hybrid Search | 14 | Vector + BM25 + graph-proximity fusion | ✅ |
| M6: Ontology Stable | 16 | Ontology editor, LLM generation, validation | ✅ |
| M7: Concurrency Hardened | 20 | 100 concurrent searches, atomic swap, caching | 🟡 (swap pending) |
| M8: Production Ready | 24 | Full security, metrics, 80%+ test coverage | 🟡 (RL/coverage pending) |
| M9: GDrive Integration | 28 | Drive OAuth, change detection, incremental sync | ✅ |
| M10: Graph RAG Agent | 36 | Multi-hop agent querying with streaming | ✅ |
| M11: Hyper-Extract | TBD | Templates, two-stage extraction, knowledge chat, hyperedges | ⬜ Unstarted |

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| LanceDB API changes between 0.9 versions | Medium | High | Pin exact version in Cargo.lock; integration tests |
| OpenAI API cost overrun during development | Medium | Medium | `max_cost_usd` cap (implemented); use GPT-4o-mini for dev |
| PyO3 upgrade breaking changes | Low | High | Pin PyO3 0.22; comprehensive Rust unit tests |
| petgraph memory exhaustion on large corpora | Low | High | Collection eviction policy (Phase 3); max_nodes limit (5000-node cap implemented) |
| Concurrent graph write contention | Low | High | Write semaphore(1) + brief write lock (implemented) |
| LLM hallucination corrupting graph | Medium | Medium | Ontology validator drops invalid types (Phase 2, implemented) |
| GDrive OAuth complexity | Medium | Low | Implemented in Phase 4; polling fallback available |
| Shadow-table swap not yet implemented | Medium | High | Highest-priority Phase 3 remainder; blocks acceptance criterion #2 |