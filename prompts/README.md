# LLM Agent Prompts — sw-llm-knowledge-graph

This directory contains structured prompts for a **3-bot agent pipeline** that implements,
reviews, and tests the sw-llm-knowledge-graph system.

---

## The 3-Bot System

| Bot | Role | When to Run |
|-----|------|-------------|
| **Bot 1 — Build** | Senior engineer: implements features from spec | First pass |
| **Bot 2 — Review** | Senior reviewer: audits output, finds bugs, fixes deviations | After Bot 1 |
| **Bot 3 — Test** | QA engineer: writes test scripts covering all acceptance criteria | After Bot 2 |

**Execution order**: Bot 1 → Bot 2 → Bot 3. Never skip Bot 2 before Bot 3.

---

## Phases

The system is built in 6 logical phases. Run all 3 bots per phase before starting the next.

| Phase | Scope | Key Specs |
|-------|-------|-----------|
| [Phase 1 — Rust Core](./phase-1-rust-core/) | PyO3 engine, IndexManager, LanceDB/Tantivy, WAL | 01, 02, 05, 11, 12 |
| [Phase 2 — Python API](./phase-2-python-api/) | FastAPI, Google OAuth, JWT, ingest worker, NER, embedder | 03, 08, 10, 14 |
| [Phase 3 — Graph Engine](./phase-3-graph-engine/) | Ontology, EntityResolver, petgraph, NER→graph, BFS/Dijkstra | 04, 07, 14 |
| [Phase 4 — Hybrid Search](./phase-4-hybrid-search/) | 3-channel search, score fusion, LRU cache, topic filter | 06, 03 |
| [Phase 5 — Frontend](./phase-5-frontend/) | React 18, RTK Query, graph viewer, auth flow | 09, 08, 10 |
| [Phase 6 — Hardening](./phase-6-hardening/) | Concurrency, atomic swap, rate limiting, metrics | 05, 11, 10 |

---

## Prompt Files

```
phase-1-rust-core/
  bot1-build.md      ← implement Rust core engine
  bot2-review.md     ← review Rust core for spec compliance + safety
  bot3-test.md       ← write cargo test + criterion benchmarks

phase-2-python-api/
  bot1-build.md      ← implement FastAPI + ingest + NER + embedder
  bot2-review.md     ← review Python API for security + correctness
  bot3-test.md       ← write pytest + integration tests

phase-3-graph-engine/
  bot1-build.md      ← implement ontology, entity resolution, graph ops
  bot2-review.md     ← review graph engine for correctness + data integrity
  bot3-test.md       ← write graph traversal + entity resolution tests

phase-4-hybrid-search/
  bot1-build.md      ← implement hybrid search pipeline
  bot2-review.md     ← review score fusion + timeout + cache logic
  bot3-test.md       ← write search correctness + performance tests

phase-5-frontend/
  bot1-build.md      ← implement React app (auth, graph viewer, search)
  bot2-review.md     ← review frontend for auth security + UX correctness
  bot3-test.md       ← write vitest component + Playwright E2E tests

phase-6-hardening/
  bot1-build.md      ← implement concurrency model, rate limits, metrics
  bot2-review.md     ← review for deadlock risk + security correctness
  bot3-test.md       ← write concurrency stress tests + security tests
```

---

## Cross-Cutting Rules

These rules apply to **all bots, all phases** (sourced from `tasks/LESSONS.md`):

1. **NER model**: Always `en_core_web_trf`. Never `en_core_web_sm` (lower quality, must reprocess).
2. **PyO3 names**: Python imports must match Rust struct names in `#[pymodule]` block.
3. **Storage**: LanceDB system tables for all metadata (users, collections, jobs) — no PostgreSQL.
4. **Embeddings**: HuggingFace `Qwen/Qwen3-Embedding-0.6B`, 1024-dim — not OpenAI.
5. **NER labels**: Use SPACY_TO_CANONICAL: `ORG→ORGANIZATION`, `GPE/LOC/FAC→LOCATION`, `NORP→ORGANIZATION`.
6. **Lock ordering**: Level 1 (atomic) → Level 2 (outer HashMap) → Level 3 (per-collection) → Level 4 (leaf). Never reverse.
7. **Cargo.toml**: `crate-type = ["cdylib", "rlib"]` for any PyO3 crate that needs `cargo test`.
8. **Contextual prefix**: Gated behind `settings.enable_contextual_prefix` (default `False`).
9. **Access token**: Persisted to `localStorage` key `kg_access_token` (not memory-only).
10. **Dev token**: `dev_token_{user_id}` accepted ONLY when JWT PEM key files do not exist on disk.

---

## Specification Files

All specs in `specifications/` directory:

| File | Title |
|------|-------|
| 01-system-architecture.md | System Architecture |
| 02-data-models.md | Data Models (LanceDB schemas) |
| 03-ingestion-pipeline.md | Ingestion Pipeline |
| 04-ontology-engine.md | Ontology Engine |
| 05-index-manager.md | Index Manager (CRITICAL — concurrency) |
| 06-search-engine.md | Hybrid Search Engine |
| 07-graph-engine.md | Graph Engine |
| 08-api-design.md | REST + WebSocket API |
| 09-frontend-design.md | Frontend Design |
| 10-auth-security.md | Auth & Security |
| 11-concurrency-performance.md | Concurrency & Performance (CRITICAL) |
| 12-project-structure.md | Project Structure |
| 13-development-roadmap.md | Development Roadmap |
| 14-ner-pipeline.md | NER Pipeline |
