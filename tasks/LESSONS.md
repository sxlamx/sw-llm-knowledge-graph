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
