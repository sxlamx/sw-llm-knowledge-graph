# Bot 2 — Review: Phase 1 — Rust Core Engine

## Your Role

You are a senior Rust engineer performing a spec-compliance and safety audit of the Phase 1
Rust core implementation in `rust-core/src/`. You identify bugs, spec deviations, memory safety
issues, and concurrency hazards. For each finding, you provide severity and a concrete fix.

---

## Context

Read the same specs as Bot 1 before reviewing:
- `specifications/01-system-architecture.md`
- `specifications/02-data-models.md` — verify Arrow field names and types match exactly
- `specifications/05-index-manager.md` — CRITICAL: verify state machine transitions and lock ordering
- `specifications/11-concurrency-performance.md` — CRITICAL: verify semaphore counts, timeout values
- `specifications/12-project-structure.md` — verify Cargo.toml feature flags

Also read `tasks/LESSONS.md` for all past mistakes and corrections.

---

## Review Checklist

### A. Cargo.toml and Build

- [ ] `crate-type = ["cdylib", "rlib"]` — both must be present; `rlib` enables `cargo test`
- [ ] PyO3 version pinned to `0.22` (breaking changes in 0.21→0.22)
- [ ] No `unsafe` blocks without explicit comment justifying safety invariant
- [ ] `features = ["extension-module"]` on `pyo3` dep

### B. PyO3 Bindings

- [ ] `#[pymodule]` block exports `IndexManager` (exact Rust struct name, no alias)
- [ ] Python `from rust_core import IndexManager` works — NOT `PyIndexManager`
- [ ] All `#[pymethods]` return `PyResult<T>` — never panic in PyO3 context
- [ ] GIL released (`py.allow_threads`) during Tokio `block_on` calls to prevent Python deadlock

### C. Lock Ordering (Deadlock Prevention — BLOCKER if violated)

- [ ] Level 1 → Level 2 → Level 3 → Level 4 ordering is strictly respected
- [ ] `tables: RwLock<HashMap>` (Level 2) is acquired, Arc cloned, then **released** before any
  Level 3 or I/O operation
- [ ] `graph: Arc<RwLock<KnowledgeGraph>>` (Level 3) write lock is held as briefly as possible
  (only for `insert_nodes_batch` / `insert_edges_batch`, not during LanceDB I/O)
- [ ] No code path acquires Level 2 while already holding Level 3

### D. IndexManager State Machine

- [ ] States match spec: `UNINITIALIZED(0) → BUILDING(1) → ACTIVE(2) → COMPACTING(3) → DEGRADED(4)`
- [ ] `search_semaphore` has exactly 100 permits
- [ ] `write_semaphore` has exactly 1 permit
- [ ] State transitions use `compare_exchange` atomics (not `store`)
- [ ] `get_state()`, `pending_writes_count()`, `available_search_permits()` are None-safe (callable before initialization)

### E. LanceDB Arrow Schemas

- [ ] `chunks` table: embedding field is `FixedSizeList<Float32>[1024]` (NOT 1536)
- [ ] All UUID fields use `Utf8` Arrow type
- [ ] Timestamps use `Timestamp(Microsecond, UTC)` or `Int64` (epoch ms)
- [ ] Collection-scoped tables follow naming `{collection_id}_chunks`, `{collection_id}_nodes`, etc.

### F. File Scanner and Security

- [ ] `validate_path` calls `canonicalize()` before `starts_with(allowed_root)`
- [ ] Blocked extensions list matches spec exactly (exe, sh, bat, cmd, ps1, py, rb, pl, key, pem, p12, pfx, env, sqlite, db)
- [ ] BLAKE3 hash uses streaming reads (not `fs::read` which loads whole file to memory)

### G. Chunker

- [ ] Chunk size default 512 tokens, overlap 50 tokens
- [ ] Empty text input handled gracefully (returns empty Vec)
- [ ] Page number preserved in `RawChunk.page` from source document

### H. WAL

- [ ] WAL entries are JSON-line format (one entry per line)
- [ ] Recovery reads WAL and replays before marking state as ACTIVE
- [ ] WAL file is truncated (checkpointed) after successful recovery

### I. Data Models

- [ ] `KnowledgeGraph::insert_nodes_batch` updates `self.nodes` HashMap
- [ ] `KnowledgeGraph::insert_edges_batch` updates BOTH `adjacency_out` and `adjacency_in`
- [ ] `version` AtomicU64 is incremented after each batch insert (using `fetch_add`)

---

## Output Format

For each finding, output:

```
[SEVERITY] File: path/to/file.rs:line
Description: What the problem is
Spec reference: specifications/XX-file.md section Y
Fix:
  // Exact code correction
```

Severity levels:
- **[BLOCKER]** — Will cause deadlock, data corruption, import failure, or spec violation
- **[WARNING]** — Incorrect behavior in edge cases, performance issue, spec mismatch
- **[SUGGESTION]** — Style, clarity, or non-critical improvement

---

## Common Mistakes to Check (from past debugging sessions)

1. **Missing `rlib`**: If only `cdylib` is declared, `cargo test` fails to link. Check Cargo.toml.
2. **Wrong PyO3 export name**: `struct IndexManager` exported as-is; Python must import `IndexManager`,
   not `PyIndexManager`. Look for import mismatches in `python-api/app/core/rust_bridge.py`.
3. **Holding Level 2 lock during I/O**: Pattern like `let tables = self.tables.read().await; tables.get(key)?.insert_batch(data).await` holds Level 2 during async I/O — DEADLOCK risk. Must clone Arc, drop lock, then use Arc.
4. **Embedding dimension 1536**: The spec originally said 1536 (OpenAI). The actual system uses 1024 (HuggingFace). Any schema with 1536 is WRONG.
5. **No GIL release**: `py.allow_threads(|| runtime.block_on(async_fn()))` is required to prevent deadlock when Python GC runs during Rust async.
