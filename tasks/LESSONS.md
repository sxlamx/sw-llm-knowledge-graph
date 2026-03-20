# Lessons Learned

This file tracks mistakes made and corrections applied during development.
Claude should read this file before starting any coding task to avoid repeating past mistakes.

---

## Format

Each entry follows this structure:

**Date:** YYYY-MM-DD
**Context:** What was being worked on
**Mistake:** What went wrong
**Correction:** What the correct approach is
**Rule:** The general principle to apply going forward

---

<!-- New lessons are added below this line -->

---

**Date:** 2026-03-20
**Context:** Setting up Claude's session behavior
**Mistake:** LESSONS.md was only instructed to be read before coding tasks, not at the start of every session. Corrections and improvements captured here could be missed if a session started without a coding task.
**Correction:** Updated CLAUDE.md to require reading LESSONS.md at the beginning of every session, not just before coding.
**Rule:** Always read LESSONS.md at the start of every session so all past corrections are applied regardless of task type.

---

**Date:** 2026-03-20
**Context:** Phase 1 backend completion — Rust core PyO3 bridge
**Mistake:** `python-api/app/core/rust_bridge.py` imported `PyIndexManager` from `rust_core`, but PyO3 exports the struct under its Rust name `IndexManager`. This caused `ImportError` and set `RUST_AVAILABLE = False`, silently disabling the entire Rust integration.
**Correction:** Fixed import to `from rust_core import IndexManager as PyIndexManager`.
**Rule:** Always verify PyO3 class export names match Python import names. PyO3 uses the Rust struct name unless `#[pyclass(name = "...")]` is set explicitly. Check `lib.rs` `#[pymodule]` block to confirm exported names before importing in Python.

---

**Date:** 2026-03-20
**Context:** Phase 1 backend completion — documents endpoint
**Mistake:** `python-api/app/routers/documents.py` used `range(offset, min(offset + limit, 0))` which always produces an empty range (upper bound is always 0). The endpoint always returned 0 documents.
**Correction:** Replaced stub with real LanceDB query against `{collection_id}_chunks` table, aggregating by `doc_id`.
**Rule:** Never use `min(x, 0)` as an upper bound in a range — this always produces an empty range. Stubs that return hardcoded empty results must be marked with `# TODO` and a failing test, not silently deployed.

---

**Date:** 2026-03-20
**Context:** Phase 1 backend completion — spec deviations discovered
**Mistake:** The implementation deviated from spec in two ways without being documented: (1) Embedder uses Ollama locally instead of OpenAI `text-embedding-3-large`; (2) PostgreSQL + SQLAlchemy + Alembic is absent — LanceDB tables are used for user/collection/job metadata instead.
**Correction:** These are intentional deferrals (Ollama for local dev, LanceDB for simplicity), not errors. Documented here to avoid confusion.
**Rule:** When intentionally deviating from a specification, document the deviation and rationale immediately — in LESSONS.md and/or in a comment near the code. Do not leave undocumented divergences that will confuse future readers.

---

**Date:** 2026-03-20
**Context:** Phase 1 backend completion — Phase 2 feature creep in ingest pipeline
**Mistake:** `ingest_worker.py` called `generate_doc_summary()` and `generate_contextual_prefix()` (LLM calls) for every chunk during Phase 1 ingest. These are Phase 2 features that add significant LLM cost and latency per document.
**Correction:** Gated behind `settings.enable_contextual_prefix` (default `False`). Set `ENABLE_CONTEXTUAL_PREFIX=true` to enable.
**Rule:** Phase 2+ features that add external API calls or significant latency must be gated behind a config flag and disabled by default during Phase 1. Review the roadmap phase boundaries before adding LLM calls to core pipelines.

---

**Date:** 2026-03-21
**Context:** Writing Rust integration tests for `find_shortest_path` and `bfs_reachable`.
**Mistake:** Assumed `find_shortest_path` returns `Vec<Uuid>` and that `bfs_reachable` excludes the seed node from its result set.
**Correction:** `find_shortest_path` returns `Vec<PathStep>` (alternating Node/Edge items, NOT strictly ordered). `bfs_reachable` inserts the seed node into `visited` when it is first popped from the frontier, so it IS included in the returned `HashSet`. Test with `filter_map` on `PathStep::Node` variants rather than indexing directly.
**Rule:** When testing graph traversal APIs, read the return type carefully and trace the reconstruction loop before writing assertions. Never assume Node-only return types or that seed nodes are excluded.

---

**Date:** 2026-03-21
**Context:** Implementing the Tantivy batch committer for Phase 3.
**Mistake:** Initially considered spawning a persistent Tokio background task from `#[pymethods]`, which requires a persistent runtime and conflicts with the "each method creates its own `Runtime::new()`" PyO3 pattern.
**Correction:** Decouple write and commit: `insert_chunks` stages docs without committing; a separate `flush_tantivy()` pymethods fn does the commit. Python startup wires an asyncio task that calls `flush_tantivy()` every 500 ms via `run_in_executor`. This gives the same effect without needing a persistent Rust-side Tokio runtime.
**Rule:** For PyO3 background tasks, prefer Python asyncio tasks calling a blocking pymethods fn via `run_in_executor` over spawning a persistent Tokio runtime in Rust.

---

**Date:** 2026-03-21
**Context:** Adding Rust integration tests to a pyo3 `cdylib` crate.
**Mistake:** `crate-type = ["cdylib"]` alone prevents `cargo test` from building integration tests in `tests/`, because there is no `rlib` to link against.
**Correction:** Use `crate-type = ["cdylib", "rlib"]` so both the Python extension and the native Rust test harness are produced.
**Rule:** Any pyo3 crate that needs `cargo test` (unit or integration) must declare `rlib` alongside `cdylib`.
