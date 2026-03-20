# LLM-Powered Rust Knowledge Graph Builder — Specification Index

This directory contains all design specification documents for the production-grade LLM-powered
Knowledge Graph Builder. The system uses a Rust core engine (via PyO3/Maturin), a Python FastAPI
orchestration layer, and a React 18 frontend.

---

## Document Index

| # | File | Title | Description |
|---|------|--------|-------------|
| 01 | [01-system-architecture.md](./01-system-architecture.md) | System Architecture | High-level component diagram, process topology, deployment layout, technology justification |
| 02 | [02-data-models.md](./02-data-models.md) | Data Models | LanceDB table schemas, PostgreSQL schemas, Rust in-memory graph structures |
| 03 | [03-ingestion-pipeline.md](./03-ingestion-pipeline.md) | Ingestion Pipeline | Full document processing pipeline: file discovery → extraction → chunking → embedding → LLM extraction → graph construction |
| 04 | [04-ontology-engine.md](./04-ontology-engine.md) | Ontology Engine | Entity type hierarchy, relationship type constraints, Rust validator, LLM-assisted expansion, versioning |
| 05 | [05-index-manager.md](./05-index-manager.md) | Index Manager | **CRITICAL** — Index state machine, RwLock locking strategy, atomic index swap, concurrent search design, WAL recovery |
| 06 | [06-search-engine.md](./06-search-engine.md) | Search Engine | Hybrid search (vector + BM25 + graph), parallel 3-way search, score fusion, caching, graph traversal optimization |
| 07 | [07-graph-engine.md](./07-graph-engine.md) | Graph Engine | Directed property graph model, hot/cold storage layers, entity resolution, graph construction flow, pruning, human-in-the-loop |
| 08 | [08-api-design.md](./08-api-design.md) | API Design | Full REST + WebSocket/SSE API specification, all endpoints, request/response schemas, rate limiting |
| 09 | [09-frontend-design.md](./09-frontend-design.md) | Frontend Design | React 18 + MUI v6 component specs, routing, state management (Redux Toolkit + RTK Query), graph viewer, auth flow |
| 10 | [10-auth-security.md](./10-auth-security.md) | Auth & Security | Google OAuth 2.0, RS256 JWT, multi-tenancy isolation, path sanitization, prompt injection prevention, rate limiting |
| 11 | [11-concurrency-performance.md](./11-concurrency-performance.md) | Concurrency & Performance | **CRITICAL** — Tokio runtime config, lock ordering, concurrency primitives table, performance targets, batch write optimization, LRU cache design |
| 12 | [12-project-structure.md](./12-project-structure.md) | Project Structure | Full directory layout, Cargo.toml dependencies, Python pyproject.toml, Docker Compose layout |
| 13 | [13-development-roadmap.md](./13-development-roadmap.md) | Development Roadmap | 4-phase delivery plan: MVP → Graph+Ontology → Production Hardening → Advanced Features |

---

## Key Design Principles

1. **Fast and lightweight** — Zero-copy Arrow RecordBatch, async Rust (Tokio), in-process LanceDB, minimal allocations
2. **Multiple concurrent searches** — RwLock-based index access, 100-permit search semaphore, MVCC-safe concurrent reads
3. **Managed index lifecycle** — Index state machine (UNINITIALIZED → BUILDING → ACTIVE → COMPACTING), atomic shadow-table swap, WAL recovery
4. **Ontology-first extraction** — All LLM outputs validated against a versioned ontology before entering the graph
5. **Hybrid storage** — Hot in-memory petgraph for traversal + cold LanceDB for persistence and ANN vector search

---

## Technology Stack Summary

| Layer | Technology |
|-------|------------|
| Frontend | React 18, TypeScript, Vite.js, Material UI v6, Redux Toolkit, RTK Query |
| Backend API | Python FastAPI (orchestration + LLM calls) |
| Core Engine | Rust (via PyO3/Maturin bindings) |
| Vector + Columnar Storage | LanceDB (IVF-PQ index, MVCC) |
| Relational Metadata | PostgreSQL (users, collections, jobs, ontology) |
| In-Memory Graph | petgraph (StableGraph, directed) |
| Full-Text Search | Tantivy (BM25) |
| Async Runtime | Tokio (multi-threaded) + Rayon (CPU parallelism) |
| File Watching | notify crate |
| Auth | Google OAuth 2.0 + RS256 JWT |
| Graph Visualization | Cytoscape.js / react-force-graph-2d |
| Hashing | BLAKE3 (incremental update detection) |
| Observability | tracing + OpenTelemetry + Prometheus metrics |
