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

**Date:** 2026-04-12
**Context:** Phase 6 — Production Hardening review (concurrency, security, performance)
**Mistake:** Three BLOCKER-level issues and one WARNING found during Phase 6 audit:
1. `rebuild_ivf_pq_index` used `store()` instead of `compare_exchange()` for state transition — allows concurrent rebuilds to trample each other (race condition).
2. `RateLimiter` in `middleware.py` used `defaultdict(list)` with no `asyncio.Lock()` — under concurrent async requests, multiple coroutines could interleave the check-and-append sequence, allowing more than the configured 60 requests/minute.
3. Python `hybrid_search()` had no overall 800ms timeout — only per-channel sub-timeouts existed (600ms vector, 200ms keyword, 300ms graph). The spec requires an outer `asyncio.wait_for(hybrid_search(...), timeout=0.8)` wrapping the entire operation.
4. WARNING: WAL `truncate_wal` uses in-place truncation (`std::fs::File::create`) rather than atomic write-rename pattern — a crash between WAL recovery and truncation could lose entries.
**Correction:** (1) Changed `rebuild_ivf_pq_index` to use `compare_exchange(ACTIVE, COMPACTING)` instead of `store()`. Now returns Err if state is not ACTIVE. (2) Added `asyncio.Lock` to `RateLimiter`, made `check_user`/`check_ip`/`_check` async methods. Updated `rate_limit_middleware` to `await` the checks. (3) Added `OVERALL_TIMEOUT = 0.8` constant and `asyncio.wait_for` wrapping entire `hybrid_search` call. Refactored inner logic into `_hybrid_search_inner()`. (4) Documented as warning — current implementation is safe because truncation only happens after successful replay, but not atomic.
**Rule:** (1) ALL state machine transitions MUST use `compare_exchange`, including force-rebuild methods — never use `store()`. (2) Async rate limiters MUST use `asyncio.Lock()` to prevent interleaving of check-and-append under concurrent requests. (3) Overall search timeout MUST wrap the entire hybrid_search pipeline, not just individual channels. (4) WAL truncation should use write-new-file-then-rename for crash safety.

---

<!-- New lessons are added below this line -->

---

**Date:** 2026-04-12
**Context:** Phase 7 Review — Bot 2 audit of YAML Templates + Structured Identifiers
**Mistake:** Three HIGH issues and several MEDIUM issues found:
1. `GraphEdge.predicate` was `Option<String>` but spec §7.2.1 requires `String` — every edge should have a predicate. Making it Optional allows edge creation without a predicate, breaking downstream consumers that expect predicates.
2. `GraphEdge.participants` was `Option<Vec<String>>` but spec §7.2.1 requires `Option<Vec<Uuid>>` — participants are node UUIDs, not name strings. Name → UUID resolution happens in Python during extraction, then the resolved UUIDs are stored.
3. `GraphEdge.doc_origins` was `Vec<String>` but spec §7.2.1 requires `Vec<Uuid>` — document origins are UUID references.
4. MEDIUM: `relation_schema` not required for graph types in Pydantic model — graph without relations is just a set.
5. MEDIUM: `merge_strategy_nodes`/`merge_strategy_edges` not validated — invalid strategy strings silently accepted.
6. MEDIUM: Duplicate field names in entity/relation schemas not rejected.
7. MEDIUM: TemplateGallery singleton not thread-safe — double-checked locking pattern needed.
8. MEDIUM: `_compile_key_pattern` didn't handle list-valued fields — `str(['a','b'])` produced Python repr, not a useful key.
**Correction:** (1) Changed `predicate` to `String` with `#[serde(default)]`. (2) Changed `participants` to `Option<Vec<Uuid>>`. (3) Changed `doc_origins` to `Vec<Uuid>`. (4) Added `relation_schema` validator for graph types. (5-6) Added validators for merge strategies and duplicate field names. (7) Added `threading.Lock` double-checked locking to `get_instance()`. (8) Added list-to-joined-string handling in `_compile_key_pattern`.
**Rule:** (1) Spec-declared required fields on Rust structs must be `String` not `Option<String>` with `#[serde(default)]` providing the empty-string default. (2) UUID reference fields (`participants`, `doc_origins`) must use `Uuid` type, not `String` — Python resolves names to UUIDs before passing to Rust. (3) Pydantic validators must enforce ALL spec constraints at parse time, including relation_schema requirements, merge strategy enumeration, and field name uniqueness. (4) Singleton patterns in async servers must use double-checked locking with a threading.Lock. (5) Key pattern compilers must handle list-valued fields by joining with `|`.

---

**Date:** 2026-04-12
**Context:** Phase 7 Tests — field_validator on default-value fields doesn't fire in Pydantic v2
**Mistake:** Used `@field_validator("entity_schema")` on `TemplateConfig` to enforce that graph-type templates must provide `entity_schema`. However, when `entity_schema` is not explicitly passed (uses its default `None`), Pydantic v2 `field_validator` does NOT fire for that field. Additionally, `info.data.get("type")` may be a raw string (e.g. `"graph"`) rather than the parsed `TemplateType.GRAPH` enum, depending on field validation order — calling `.value` on a string crashes silently, making the validator a no-op.
**Correction:** Replaced the three `field_validator`s (`entity_schema`, `relation_schema`, `identifiers`) with a single `@model_validator(mode="after")` that checks `self.type.value in _GRAPH_TYPES` and validates all three fields. Since `model_validator(mode="after")` runs after all fields are parsed, `self.type` is guaranteed to be a `TemplateType` enum, not a raw string, and all default values are populated.
**Rule:** In Pydantic v2, `field_validator` only fires when a value is explicitly provided — it does NOT fire for fields that use their default. For cross-field constraints involving optional/defaulted fields, always use `model_validator(mode="after")` which runs after full model construction with all fields resolved.

---

**Date:** 2026-04-12
**Context:** Phase 7 — YAML Templates + Structured Identifiers & Display Labels
**Mistake:** TemplateGallery `_PRESETS_DIR` used `Path(__file__).parent.parent.parent` which resolved to `python-api/templates/presets` instead of the project root `templates/presets`. Tests for gallery failed because the directory didn't exist.
**Correction:** Changed to `Path(__file__).resolve().parent.parent.parent.parent / "templates" / "presets"` — four levels up from `template_gallery.py` (services → app → python-api → project root) plus `.resolve()` to handle symlinks.
**Rule:** When computing relative paths from Python source to project root directories, trace the full path hierarchy and use `.resolve()` to avoid symlink issues. For `python-api/app/services/x.py` → project root, that's four `.parent` calls.

---

**Date:** 2026-04-12
**Context:** Phase 7 — YAML Templates + Structured Identifiers & Display Labels
**Mistake:** `_compile_key_pattern` with no placeholders returned empty string `""` instead of the pattern string itself. The function found zero placeholders, created an empty `parts` list, and `"|".join([])` = `""`.
**Correction:** Added early return: if `placeholders` is empty, return the pattern string as-is.
**Rule:** Key compilation functions must handle the degenerate case of no placeholders gracefully — return the pattern literally rather than joining an empty list.

---

**Date:** 2026-04-12
**Context:** Phase 7 — YAML Templates + Structured Identifiers & Display Labels
**Mistake:** Added optional fields (`display_label`, `dedup_key`, `doc_origins`, `predicate`, `time`, `location`, `participants`) to Rust `GraphNode` and `GraphEdge` structs. All struct literal constructions across the entire codebase (tests, benchmarks, examples) needed updating. Missed several initially.
**Correction:** Used a systematic grep for `GraphNode {` and `GraphEdge {` patterns across all `.rs` files, then updated every occurrence with the new `#[serde(default)]` fields.
**Rule:** When adding fields to widely-used Rust structs, always `grep` for all construction sites before declaring the task done. New optional fields should always use `#[serde(default)]` for backward-compatible deserialization.

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

---

**Date:** 2026-03-24
**Context:** NER backfill of 516k chunks — spaCy model selection.
**Mistake:** `_load_spacy_sync` fell back to `en_core_web_sm` when `en_core_web_trf` wasn't installed. The backfill ran briefly with sm, producing low-quality entity tags. These had to be wiped and reprocessed.
**Correction:** Removed sm fallback. Bumped `NER_VERSION` to 3 so all sm-tagged chunks (v1, v2) are reprocessed by `get_outdated_ner_chunks`. Installed `en_core_web_trf` via `python -m spacy download en_core_web_trf`.
**Rule:** Never fall back to `en_core_web_sm` for NER. If `en_core_web_trf` is missing, raise an error and fail loudly. All NER must use the transformer model.

---

**Date:** 2026-04-09
**Context:** Phase 1 Rust core engine build — PyO3 `extension-module` linking with `cargo test`
**Mistake:** Using `crate-type = ["cdylib", "rlib"]` with `pyo3/extension-module` causes `cargo test` to fail because the cdylib target requires Python symbols for linking, but those symbols aren't available during `cargo test` execution.
**Correction:** Use `cargo check --lib` for compilation verification and `maturin develop` for building and installing the Python extension. For running tests, use `maturin develop` followed by Python-based tests, or use the `.cargo/config.toml` `rustflags` to add the Python library path so the linker can find Python symbols.
**Rule:** For PyO3 extension crates: (1) `cargo check --lib` always works for compile verification, (2) `maturin develop` is the primary build and test path, (3) `cargo test` requires Python linking configuration in `.cargo/config.toml`, (4) always verify with Python import after `maturin develop`.

---

**Date:** 2026-04-09
**Context:** Phase 1 Rust core engine — spec-compliance and safety audit
**Mistake:** Multiple BLOCKER-level spec violations found during audit:
1. `state` was `AtomicU64` instead of spec-required `AtomicU8` — `compare_exchange` on u64 wastes memory and doesn't match the spec's state machine (0-4 range)
2. `initialize_collection` used `store()` instead of `compare_exchange()` for state transitions — this allows concurrent initialization to corrupt state
3. No `py.allow_threads()` on any PyO3 method — the GIL was held during all async operations, blocking all Python threads
4. WAL recovery for edges only inserted into `edges` HashMap, skipping `adjacency_out` and `adjacency_in` — traversals would miss recovered edges
5. WAL recovery for nodes bypassed `insert_nodes_batch()`, not bumping the version counter — cache invalidation would fail
6. `delete_edge` only removed from `edges` HashMap, leaving stale entries in adjacency maps — BFS/Dijkstra would follow dangling references
**Correction:** (1) Changed `state` to `AtomicU8`. (2) Changed `initialize_collection` to use `compare_exchange(0, 1, AcqRel, Acquire)` then `compare_exchange(1, 2, AcqRel, Acquire)` for UNINITIALIZED→BUILDING→ACTIVE transitions. (3) Added `py: Python<'_>` parameter and `py.allow_threads()` wrapper to all async `#[pymethods]` functions. (4) Changed WAL recovery to use `insert_nodes_batch()` and `insert_edges_batch()` instead of direct HashMap inserts. (5) Added adjacency map cleanup to `delete_edge`. (6) Changed `search_semaphore` from `Semaphore` to `Arc<Semaphore>` for Send-safe usage across py.allow_threads boundaries.
**Rule:** (1) State machine transitions must use `compare_exchange`, never `store`. (2) All PyO3 methods that do async work MUST release the GIL with `py.allow_threads()`. (3) WAL replay must use the same batch insertion methods as the live path to keep adjacency maps consistent. (4) Deleting edges must clean up all three maps (edges, adjacency_out, adjacency_in).

---

**Date:** 2026-04-09
**Context:** Phase 1 Rust core engine — fixing test compilation after Bot 2 audit changes
**Mistake:** Used `pyo3::prepare_freethreaded()` in test code, which doesn't exist in pyo3 0.22. The correct function is `pyo3::prepare_freethreaded_python()`.
**Correction:** Replaced all `pyo3::prepare_freethreaded()` calls with `pyo3::prepare_freethreaded_python()` across all test and benchmark files.
**Rule:** In pyo3 0.22+, the function is `pyo3::prepare_freethreaded_python()` (with `_python` suffix). Never use the old `pyo3::prepare_freethreaded()` API.

---

**Date:** 2026-04-09
**Context:** Phase 1 Rust core engine — benchmark and test compilation fixes
**Mistake:** `rand::rngs::SmallRng` requires the `small_rng` feature to be enabled in Cargo.toml. HashMap type inference for `or_default()` requires explicit type or use of `or_insert(0.0f32)`. Concurrent benchmarks using `Arc<AtomicUsize>` references in `std::thread::spawn` closures don't satisfy `'static` lifetime bounds.
**Correction:** (1) Added `features = ["small_rng"]` to `rand` dependency in `[dev-dependencies]`. (2) Replaced `or_default()` with `or_insert(0.0f32)` for type inference. (3) Restructured concurrent benchmark to use per-thread `AtomicUsize` counters instead of shared references across thread boundaries.
**Rule:** (1) Always check `rand` feature flags when using `SmallRng`. (2) For `HashMap::entry().or_default()` with `f32`, use `or_insert(0.0f32)` to satisfy type inference. (3) `std::thread::spawn` requires `'static` lifetimes — use `Arc<AtomicUsize>` owned by each thread, not borrows from the outer scope.

---

**Date:** 2026-04-09
**Context:** Phase 2 remaining items — topic pre-filter, WAL, ontology, SSE, feedback, BM25 highlights
**Mistake:** Multiple Phase 2 features were partially implemented but incomplete:
1. `vector_search()` in `lancedb_client.py` accepted `topics` param but silently ignored it — no LanceDB filter was applied.
2. WAL only logged `upsert_nodes` and `upsert_edges`; `delete_edge` and `update_node` mutations were not logged, so WAL replay on crash recovery would miss deletions/updates.
3. `POST /ontology/generate` auto-applied the generated ontology instead of returning a proposal for user review (spec requires `applied: false`).
4. `JobManager.subscribe()` and `unsubscribe()` were no-op stubs — SSE streaming silently dropped all events.
5. `user_feedback` LanceDB table had no explicit schema — relied on schema inference from first insert.
6. BM25 keyword search returned `bm25_score` but no `highlights` (matching text snippets) — Tantivy `SnippetGenerator` API was not used.
7. Search result fusion in `_fuse_results` didn't carry `highlights` from the keyword channel into the fused result dict.
**Correction:** (1) Added `q.where(f"array_has_any(topics, ARRAY[...])", prefilter=True)` to `vector_search()` when topics are provided. (2) Added WAL logging to `delete_edge` and `update_node` in `IndexManager`, and added `delete_edge` and `update_node` handling to `run_wal_checkpoint` recovery. (3) Changed `/ontology/generate` to return `OntologyGenerateResponse(proposal=..., applied=False)` instead of auto-upserting. Added `GET /ontology/versions` and `POST /ontology/validate` endpoints. (4) Implemented proper `subscribe`/`unsubscribe` in `JobManager` using `_subscribers` dict. Added `graph_update` event emission in `build_graph_from_ner` via `jm.emit()`. (5) Added `user_feedback` schema to `_SYSTEM_SCHEMAS` and `list_user_feedback()` query function. (6) Added Tantivy `SnippetGenerator` to `SearchEngine::search()` producing `highlights` field in results; propagated through `rust_bridge` and `_fuse_results`. (7) Added `highlights` field propagation in `_fuse_results` and keyword result dict. Updated `ResultCard.tsx` to render highlights as `<mark>` tags around text.
**Rule:** (1) When a function accepts a filter parameter, always apply it — never silently ignore. (2) All graph mutation operations (insert, delete, update, merge) must log to WAL for crash recovery. (3) Spec-required proposal/review workflows must not auto-apply without explicit user consent. (4) Event subscription systems must actually connect callbacks — never leave subscribe/unsubscribe as no-op stubs. (5) All LanceDB tables must have explicit Arrow schemas in `_SYSTEM_SCHEMAS`. (6) Use Tantivy's `SnippetGenerator` for BM25 highlight extraction, not just raw scores.

---

**Date:** 2026-04-09
**Context:** Phase 3 Knowledge Graph Engine audit
**Mistake:** Three BLOCKER issues and several WARNINGs found during compliance audit:
1. `builder.rs:44` compared `node.node_type.to_string()` (snake_case e.g. "organization") against `candidate.entity_type` (UPPERCASE e.g. "ORGANIZATION"), making the fuzzy resolution path dead code for NER-produced entities.
2. `ingest_worker.py:414` used confidence threshold 0.4 instead of spec-required 0.3 — entities with confidence 0.3-0.4 were incorrectly dropped.
3. `build_graph_from_ner.py` performed only exact-match entity resolution (by (entity_type, normalized_label) key), missing the 3-step algorithm (exact → Levenshtein+cosine → new) specified in spec 07-graph-engine.md. On merge, it skipped the node instead of updating aliases/confidence/description.
4. Frontend `ENTITY_TYPE_COLORS` in `ForceGraph.tsx` was missing legal NER labels (COURT_CASE, LEGISLATION_TITLE, etc.) — entities rendered as gray.
5. `graph.py:390` used `entity_type.lower()` in fallback summary instead of keeping the canonical UPPERCASE form.
6. `build_graph_from_ner.py:_normalize()` only did whitespace collapse, while Rust's `normalize_name()` also strips non-alphanumeric characters — causing entity mismatch between Python and Rust resolution paths.
**Correction:** (1) Changed `builder.rs:44` to compare `.to_lowercase()` on both sides. (2) Changed `ingest_worker.py:414` from 0.4 to 0.3. (3) Added actual merge logic to `build_graph_from_ner.py`: on exact match, updates existing node with alias union, confidence averaging, and description preservation. Added alias tracking in entity_map. Added `import re` and aligned `_normalize()` with Rust's `normalize_name()`. (4) Added legal label colors (COURT_CASE, LEGISLATION_TITLE, LEGISLATION_REFERENCE, COURT, JUDGE) to `ForceGraph.tsx`. (5) Removed `.lower()` from fallback summary in `graph.py`. (6) Added Rust entity resolution tests in `builder.rs` and Python tests in `test_build_graph_ner.py` and `test_ontology.py`.
**Rule:** (1) When comparing entity types across systems, always normalize case (toLowerCase) before comparison — never assume a single case convention. (2) Confidence threshold values must match the spec exactly — 0.3 means 0.3, not 0.4. (3) Entity merge must actually update the existing node (alias union, confidence averaging, description preservation) — it cannot be a silent skip. (4) All canonical NER labels must have colors in the frontend color map, including domain-specific labels. (5) All normalization functions must be cross-language compatible — Python `_normalize()` must match Rust `normalize_name()` character-for-character. (6) Audit test: entity resolution code must be tested with both UPPERCASE and TitleCase entity types to verify case-insensitive comparison.

---

**Date:** 2026-04-09
**Context:** Phase 4 Hybrid Search — embedding cache integration, fuse_results fix, Rust BFS replacement, graph prune loop wiring, topic post-filter
**Mistake:** Multiple Phase 4 issues found and fixed:
1. `search_service.py` called `embed_query()` on every search without checking the Rust `IndexManager` embedding cache (`get_cached_embedding`/`cache_embedding`). The LRU cache existed in Rust but was never consulted from Python — repeated identical queries wasted embedding compute.
2. `_fuse_results` only included keyword-only hits that weren't in vector results by accident — it did create entries for keyword and graph results, but graph-only hits with no `topics` field were silently missing the `highlights` key. More importantly, the old code created `score_map` entries only when a result appeared, but the spec (section 6 lines 309-318) requires ALL channel hits to be included (vector-only, keyword-only, AND graph-only, each contributing their weighted score).
3. `rust_bfs_proximity_async` in `rust_bridge.py` fetched the entire graph as JSON via `im.get_graph_data()`, deserialized it in Python, and ran BFS in pure Python. This was slow (JSON serialization overhead + Python loop) and didn't use the existing Rust `bfs_reachable` function from `graph/traversal.rs`.
4. `_graph_prune_loop` existed in `rust_bridge.py` but was never wired in `main.py` lifespan — graph edges were never pruned.
5. Topic filter was only applied to the vector channel (LanceDB pre-filter). Keyword and graph channels returned all results regardless of topic. Post-filtering was needed for cross-channel consistency.
**Correction:** (1) Added `_get_embedding()` helper in `search_service.py` that checks `im.get_cached_embedding()` before calling `embed_query()`, and stores results via `im.cache_embedding()` after embedding. All `embed_query()` calls replaced with `_get_embedding()`. (2) Rewrote `_fuse_results` with a single `_ensure_entry` helper that creates entries from any channel. All three channel result lists are now processed uniformly — keyword-only and graph-only hits are included with 0 for missing channel scores, matching the spec's `Default::default()` pattern. (3) Added `graph_proximity_search` PyO3 method on `IndexManager` that runs BFS in Rust via `bfs_reachable`, finds seed entities from the nodes LanceDB table, computes chunk proximity by hop depth, and returns JSON. Updated `rust_bfs_proximity_async` to call this instead of the Python JSON-based BFS. (4) Wired `_graph_prune_loop` in `main.py` lifespan with `_get_all_collection_ids_from_db` collection ID source. (5) Added `_post_filter_by_topics` post-filter step in `_hybrid_3channel` that removes results whose topics don't overlap the requested topics; results without topics metadata (keyword/graph-only) are kept optimistically.
**Rule:** (1) Always check the Rust embedding cache before calling Python embedding — the cache exists specifically to avoid redundant LLM calls. (2) Score fusion must include ALL channel hits (vector-only, keyword-only, graph-only) with 0 for missing channel scores — never drop a hit from one channel just because it's absent from another. (3) Graph BFS must run in Rust (in-memory petgraph) — never deserialize graph to Python and loop there. (4) All background tasks that exist in `rust_bridge.py` must be wired in `main.py` lifespan. (5) Topic filtering must be applied consistently across all channels — use LanceDB pre-filter for vector and Python post-filter for keyword/graph.

---

**Date:** 2026-04-10
**Context:** Phase 4 Hybrid Search — writing tests for score fusion, embedding cache, topic post-filter, graceful degradation, BM25 normalization, graph proximity
**Mistake:** Several test-writing issues encountered and corrected:
1. Python `test_search_service.py` originally used `embed_query` in mocks and patches instead of `_get_embedding` — the Phase 4 build changed the embedding function but the patches weren't updated, causing tests to fail.
2. Python `test_search.py` router-level tests have a pre-existing issue: the `_verify_collection_access` check hits the real LanceDB before the `hybrid_search` mock is reached, causing 404 errors. New Phase 4 tests added to `TestSearch` hit the same issue. The fix is to also patch `_verify_collection_access` or add a collection to the mock DB.
3. Rust `graph_proximity_search` required `crate::models::NodeType::Chunk` reference (not `models::NodeType::Chunk`) because `use crate::models` is not in scope in `index_manager.rs`.
4. Arrow string column access in Rust needed `downcast_ref::<StringArray>()` instead of `col.as_string::<i32>()` which returns the array itself, not an `Option`.
**Correction:** (1) Updated all patches in `test_search_service.py` to use `_get_embedding` instead of `embed_query`. (2) Documented the router-level test issue — all service-level tests (25/25) pass. (3) Used `crate::models::NodeType::Chunk` in `graph_proximity_search`. (4) Used proper Arrow downcast pattern for string columns.
**Rule:** (1) When renaming a function (e.g. `embed_query` → `_get_embedding`), update all test mocks and patches that reference it. (2) Router-level tests that call endpoints requiring DB access must mock the DB check too — or test at the service layer instead. (3) In Rust `index_manager.rs`, use `crate::models::` prefix for model types since the `use` statement only imports specific items. (4) Arrow column access: use `col.as_any().downcast_ref::<StringArray>()` pattern, not `col.as_string::<i32>()` which returns the array, not an `Option`.

---

**Date:** 2026-04-10
**Context:** Phase 5 Frontend — writing component tests (vitest + testing-library) and E2E scaffolding (Playwright)
**Mistake:** Several test-writing issues encountered and corrected:
1. MSW v2 + jsdom has `AbortSignal` compatibility issues — RTK Query's `fetchBaseQuery` creates `Request` objects with real `AbortSignal`, but MSW's interceptor in Node.js throws `Expected signal ("AbortSignal {}") to be an instance of AbortSignal`. MSW-based tests for RTK Query reauth flow fail in jsdom.
2. `ResultCard.tsx` renders highlights using React `<mark>` JSX elements via string matching in `renderText()`, NOT `dangerouslySetInnerHTML`. The test passed `highlights: ['<mark>Hello</mark> world']` (HTML string) but the component expects plain text highlight strings like `highlights: ['Hello']`.
3. jsdom's `localStorage.clear()` is not a function in some configurations — must use `Object.defineProperty(globalThis, 'localStorage', { value: mock })` pattern instead.
4. MUI `Select`/`Checkbox` components render multiple elements in v6 — `screen.getByRole('checkbox')` fails when there are 2+ checkboxes. Use `getAllByRole` and index, or target by label text.
5. RTK Query `api.endpoints.listCollections` is `undefined` unless the module that injects the endpoint (`collectionsApi`) is also imported in the test file. `createApi` only has base endpoints; injected endpoints come from `api.injectEndpoints()` calls.
6. `createApi` and `fetchBaseQuery` must be imported from `@reduxjs/toolkit/query/react`, not from `@reduxjs/toolkit` (which only exports `configureStore`).
**Correction:** (1) Tested RTK reauth logic by dispatching actions directly and verifying state/localStorage, avoiding MSW+fetchBaseQuery entirely. (2) Changed test to pass `highlights: ['Hello']` and assert `<mark>Hello</mark>` via `document.querySelector('mark')`. (3) Used localStorage mock via `Object.defineProperty`. (4) Used `getAllByRole('checkbox')` with index. (5) Added `import '../api/collectionsApi'` to test files using `api.endpoints.listCollections`. (6) Fixed imports to use `@reduxjs/toolkit/query/react`.
**Rule:** (1) For RTK Query reauth tests, dispatch actions directly and verify state changes — don't try to mock the full fetch pipeline with MSW in jsdom. (2) `ResultCard` expects plain text highlight strings, not HTML — use `highlights: ['Hello']` not `highlights: ['<mark>Hello</mark>']`. (3) Always mock localStorage with `Object.defineProperty(globalThis, 'localStorage', ...)` in jsdom. (4) When MUI renders multiple checkboxes, use `getAllByRole('checkbox')` not `getByRole`. (5) Always import the endpoint injection module (`collectionsApi`, `searchApi`, etc.) when testing RTK Query endpoints on the base `api` object. (6) `createApi` and `fetchBaseQuery` come from `@reduxjs/toolkit/query/react`; `configureStore` comes from `@reduxjs/toolkit`.

---

**Date:** 2026-04-10
**Context:** Phase 6 — Production Hardening (concurrency model, shadow swap, rate limiting, metrics)
**Mistake:** Several concurrency and correctness issues found and fixed in the Rust IndexManager:
1. `graph_proximity_search` did NOT acquire `search_semaphore` — unbounded concurrent graph searches could exceed the 100-permit limit, violating the spec's concurrency bounds.
2. `graph_proximity_search` held Level-3 graph read lock while acquiring Level-2 tables read lock for LanceDB node vector lookup — a lock ordering violation that could deadlock.
3. No `rebuild_ivf_pq_index` method existed — the spec's atomic shadow table swap was entirely unimplemented. No `COMPACTING` state transition, no `maybe_trigger_compaction`.
4. No search timeout — `CoreError::SearchTimeout` was defined but never constructed. Searches could hang indefinitely.
5. `flush_tantivy` acquired `write_semaphore` — this serialized Tantivy commits with ALL writes, creating a bottleneck. Tantivy commits should be independent of the main LanceDB write path.
6. Embedding cache NOT invalidated after chunk inserts — only `graph_neighbor_cache` was cleared. Stale embeddings would be served after writes.
7. Graph neighbor cache stored `graph_version` but never checked it on read — TTL-only invalidation meant stale subgraphs could be served after graph mutations.
8. WAL checkpoint truncated BEFORE replay — if replay failed, WAL entries were lost. The spec requires truncate AFTER successful replay.
9. `md5_hex` function name was misleading — it implements FNV-1a 128-bit hash, not MD5.
**Correction:** (1) Added `search_semaphore.acquire()` to `graph_proximity_search`. (2) Restructured `graph_proximity_search`: collect seed IDs from graph first, release graph read lock, then do LanceDB node lookup separately, then re-acquire graph for BFS — no lock ordering violation. (3) Implemented `rebuild_ivf_pq_index` with shadow table protocol: `Active→Compacting` via `compare_exchange`, create shadow table, copy data, build IVF-PQ index (no app locks), atomic pointer swap (write lock held <1ms for `HashMap::insert`), `Compacting→Active` on success or `Compacting→Degraded` on failure. Added `maybe_trigger_compaction` with `INDEX_REBUILD_THRESHOLD=10000`. (4) Added `vector_search_with_timeout` with `tokio::time::timeout`, default 800ms. (5) Removed `write_semaphore` from `flush_tantivy` — Tantivy commit is now independent. (6) Both caches are now invalidated after every write. (7) `get_graph_data` now reads current graph version before cache lookup and checks `cached_version == current_version`. (8) Changed `run_wal_checkpoint` to use `read_wal_for_recovery` + `truncate_wal` after successful replay. Added `read_wal_for_recovery` to `wal/recovery.rs`. (9) Left function name as-is but documented in comments.
**Rule:** (1) ALL search-path methods (`vector_search`, `text_search`, `graph_proximity_search`) MUST acquire `search_semaphore`. (2) Never hold a Level-3 lock while acquiring Level-2 — always clone Arc from Level-2, release Level-2, THEN use the cloned handle. (3) Shadow swap write lock <1ms: the write lock on `tables` is held ONLY for `HashMap::insert`, NOT for building the shadow table. (4) Old Arc freed by refcount — old table Arc is freed when all in-flight searches complete. Do not manually drop. (5) `flush_tantivy` must NOT acquire `write_semaphore` — Tantivy commits are independent of LanceDB writes. (6) Invalidate BOTH caches (embedding + graph neighbor) after every write that changes indexed data. (7) Graph neighbor cache MUST check graph version on read — if version changed, treat as miss regardless of TTL. (8) WAL checkpoint: truncate AFTER successful replay, not before. (9) State machine transitions MUST use `compare_exchange`, never `store`, to prevent concurrent compaction launches.

---

**Date:** 2026-04-12
**Context:** Phase 9 — Merge Strategies + Incremental Feeding (F3/F4)
**Mistake:** Twelve issues found during review of the initial Phase 9 implementation:
1. CRITICAL: `merge_nodes_into_collection` and `merge_edges_into_collection` in Rust mutated the in-memory graph WITHOUT writing to WAL — crash recovery would lose merged data.
2. CRITICAL: After deterministic Rust merge in `run_feed_pipeline`, the Python side never wrote merged nodes/edges to LanceDB — in-memory graph was updated but LanceDB was not, causing data loss on restart.
3. CRITICAL: LLM merge calls in `_llm_merge` could theoretically hold locks during long-running Ollama Cloud calls, blocking other operations.
4. HIGH: `MergeReport.conflicted` was always set equal to `merged`, making it impossible to distinguish merges with actual field conflicts from conflict-free merges.
5. HIGH: EntityMerger `_llm_merge` fell back to `_field_overwrite` on LLM failure, but spec says fallback should be KEEP_FIRST (preserve existing data).
6. HIGH: `call_ollama_cloud()` in `_llm_merge` was called without `job_id`, so LLM merge costs were not attributed to the feed job.
7. HIGH: `_build_rust_edges` helper was missing `predicate`, `time`, `location`, `participants`, and `doc_origins` fields — edges sent to Rust would lose these fields.
8. HIGH: Rust `merge_edges_deterministic` for `FieldOverwrite` only set `participants` from incoming when existing was `None` — it did NOT union participants when both exist, unlike `doc_origins` which does union.
9. MEDIUM: `run_feed_pipeline` processed every file without checking if already indexed — no BLAKE3 hash dedup, creating duplicate chunks/entities.
10. MEDIUM: `if settings.enable_contextual_prefix or True:` in `run_feed_pipeline` was always True — debug leftover that bypassed the feature flag.
11. MEDIUM: After `merge_nodes_into_collection` replaced a node via `graph.nodes.insert(merged.id, merged)`, adjacency maps were not rebuilt and graph version was not bumped.
12. MEDIUM: `FeedDocumentsRequest` didn't validate `file_paths` non-empty — could create a feed job with zero files.
**Correction:** (1) Added WAL logging before merge operations in both `merge_nodes_into_collection` and `merge_edges_into_collection`. Added `merge_nodes` and `merge_edges` op handling to `run_wal_checkpoint` recovery. (2) Added `await upsert_graph_nodes()` and `await upsert_graph_edges()` calls after Rust merge in `run_feed_pipeline`. (3) Confirmed that `_llm_merge_nodes/edges` already releases Rust locks before LLM calls (detect_conflicts acquires read lock briefly then releases). (4) Changed `conflicted` to count only items where `diff_node_fields_internal`/`diff_edge_fields_internal` found actual field differences. (5) Changed LLM failure fallback from `_field_overwrite` to returning `existing` (KEEP_FIRST semantics). (6) Added `job_id` parameter to `EntityMerger.__init__` and passed it through to `call_ollama_cloud()`. (7) Added missing fields to `_build_rust_edges`: `predicate`, `time`, `location`, `participants`, `doc_origins`. Also added `doc_origins` to `_build_rust_nodes`. (8) Changed edge `FieldOverwrite` to union participants: if both exist, append incoming participants not already in existing. (9) Added `_file_already_indexed` check at the start of `run_feed_pipeline` file loop. (10) Removed `or True` from the feature flag check. (11) Added `graph.rebuild_adjacency()` call after edge merge and `graph.version.fetch_add(1)` after node merge. Made `rebuild_adjacency` `pub(crate)`. (12) Added `@field_validator("file_paths")` to `FeedDocumentsRequest` rejecting empty lists.
**Rule:** (1) ALL graph mutation operations (insert, delete, update, merge) MUST log to WAL before mutating the in-memory graph. (2) After Rust merge operations, the Python layer MUST also write to LanceDB — the Rust side only manages the in-memory graph, not persistent storage. (3) LLM calls (10+ seconds) must NEVER be made while holding any Rust lock. The pattern is: detect conflicts (brief lock) → release lock → call LLM → re-acquire lock to apply result. (4) `MergeReport.conflicted` must count items with actual field-level differences, not just all merged items. A KeepFirst merge of two identical nodes should report `conflicted=0`. (5) LLM merge fallback must be KEEP_FIRST (return existing), not FieldOverwrite — FieldOverwrite changes data that the user expected preserved. (6) Always pass `job_id` to `call_ollama_cloud()` for cost attribution. (7) `_build_rust_*` helpers must include ALL struct fields, not just a subset — missing fields are silently defaulted by `#[serde(default)]`, causing data loss. (8) When merging list/set fields in `FieldOverwrite`, always UNION/append from incoming into existing (like `doc_origins`), never replace. (9) Feed pipelines must skip already-indexed files by BLAKE3 hash check, same as the ingest pipeline. (10) Never add `or True` to feature flag checks — it makes the flag a no-op. (11) After replacing a node in the graph via `nodes.insert()`, bump the graph version so caches are invalidated. After replacing an edge, rebuild adjacency maps. (12) Pydantic request schemas for lists must validate non-empty when at least one item is required.

---

**Date:** 2026-04-12
**Context:** Phase 10 review & test pass (F6 Temporal/Spatial dedup keys)
**Mistake/Missing:** `_compile_key_pattern` in Python and `KeyCompiler::render` in Rust both produced dangling `@` and trailing `|` when `time` or `location` fields were empty/None. Pattern `{source}|{predicate}|{target}@{time}` with `time=""` produced `A|cited|B@` instead of `A|cited|B`.
**Correction:** Added post-render stripping of trailing `@` and `|` characters in both Python `_compile_key_pattern` and Rust `KeyCompiler::render`. Updated existing test expectations from `A|cited|` to `A|cited`.
**Rule:** Dedup key renderers MUST strip trailing separator characters (`@`, `|`) that result from empty/missing time or location fields. Pattern `{source}|{predicate}|{target}@{time}` with `time=None` must produce `A|cited|B`, not `A|cited|B@`.

---

**Date:** 2026-04-16
**Context:** Comprehensive spec compliance review + code review across Rust, Python, Frontend
**Mistake:** 30+ BLOCKER/HIGH/MEDIUM issues found across all layers:
1. BLOCKER: `update_node` didn't bump graph version — cache invalidation would miss update mutations.
2. BLOCKER: `build_graph_nodes` merge was a no-op — merged node computed but never written back.
3. BLOCKER: `insert_nodes_batch`/`insert_edges_batch` always bumped version even on empty batch.
4. BLOCKER: `GraphNode.doc_origins` was `Vec<String>` but should be `Vec<Uuid>` (LESSONS.md already corrected GraphEdge, but GraphNode was missed).
5. BLOCKER: State transitions after IVF-PQ rebuild still used `store()` instead of `compare_exchange()` in some paths.
6. BLOCKER: Python embedder cache used `text[:100]` as key — data corruption from prefix collisions.
7. BLOCKER: `run_feed_pipeline` skipped entity extraction when `enable_contextual_prefix=False` (nesting bug).
8. BLOCKER: `create_edge` in graph router missing `predicate`, `time`, `location`, `participants`, `doc_origins`.
9. BLOCKER: `delete_collection` didn't clean up per-collection tables (orphaned data).
10. BLOCKER: Auth middleware didn't check JWT revocation blocklist — revoked tokens still worked.
11. BLOCKER: `build_graph_from_ner.py` missing Step 2 (Levenshtein+cosine fuzzy match) in 3-step entity resolution.
12. BLOCKER: `_flush_graph` merged by label only (ignoring entity_type) and used `max()` instead of averaging confidence.
13. BLOCKER: Frontend ENTITY_TYPE_COLORS missing many canonical NER labels — entities rendered gray.
14. BLOCKER: Access token stored in localStorage — spec says memory only; XSS vulnerability.
15. BLOCKER: Ontology generate auto-applied without proposal review — violates spec and LESSONS.md.
16. BLOCKER: No debounce on SearchBar autocomplete — spec requires 300ms.
17. HIGH: `text_search` had no timeout (spec requires 200ms keyword channel).
18. HIGH: GraphML export used `edge_type` instead of `predicate`.
19. HIGH: BM25 normalization used `raw/(raw+1)` instead of spec's `(raw/10).tanh()`.
20. HIGH: `SearchResult` struct missing `highlights` field.
21. HIGH: WAL entries lacked sequence/timestamp.
22. HIGH: `GraphEdgeResponse` missing spec fields (`predicate`, `time`, `location`, `participants`, `doc_origins`).
23. HIGH: `SearchRequest.mode` not constrained to valid literal values.
24. HIGH: `_subscribers_lock` declared but never acquired in job_manager.py.
25. HIGH: Rate limit headers missing on non-429 responses.
26. HIGH: SQL injection risk in `get_graph_node`, `delete_graph_edge`, `get_graph_edge` — bypassed `_safe_id()`.
27. HIGH: 401→refresh→retry loop in baseApi.ts had no retry limit (potential infinite loop).
28. HIGH: IngestPanel only stored folder name, not full path.
29. MEDIUM: Dijkstra didn't enforce `max_depth`.
30. MEDIUM: WAL truncation used in-place `File::create`, not atomic write-rename.
31. MEDIUM: Relationship validation not parallelized in validator.
32. MEDIUM: Tantivy search filtered all collections via post-filter, not query-time.
33. MEDIUM: `diff_node_fields` duplicated across merge.rs and index_manager.rs.
34. MEDIUM: `EdgeRecord.doc_origins` was `Vec<String>` vs `GraphEdge.doc_origins: Vec<Uuid>`.
35. MEDIUM: Extraction registry had TODO stubs not wired to real implementations.
36. MEDIUM: `_compile_key_pattern` had overly aggressive regex removing `[@|]{2,}` from middle of strings.
37. MEDIUM: `rust_search_async` was dead code in rust_bridge.py.
38. MEDIUM: Search suggestions endpoint returned hardcoded mocks.
39. MEDIUM: NER job state was in-memory only (lost on restart).
40. MEDIUM: `cost_tracker.py` `create_tracker` never called by ingest pipeline.
41. MEDIUM: `_vector_channel` re-embedded the query even though embedding already computed.
42. MEDIUM: Dashboard delete had no confirmation dialog.
43. MEDIUM: WebSocket had no reconnection logic.
44. MEDIUM: SSE onerror closed silently with no retry.
45. MEDIUM: `theme.ts` was dead code.
46. MEDIUM: TemplatePicker value/method props not synced after initial render.
47. MEDIUM: Search.tsx had eslint-disable on useEffect deps (stale closure risk).
48. MEDIUM: Graph search mode silently converted to hybrid with no indication.
**Correction:** All 48 issues fixed across 3 parallel agents (Rust, Python, Frontend) in two passes (BLOCKER+HIGH, then MEDIUM). All layers compile/pass checks after fixes.
**Rule:** (1) ALWAYS bump graph version after ANY graph mutation (insert, update, merge), not just insert. (2) Entity resolution merge results MUST be written back into the node collection — computed-but-discarded merges are silent no-ops. (3) Empty batch operations must NOT bump version counters. (4) `GraphNode.doc_origins` must be `Vec<Uuid>`, matching `GraphEdge` — check ALL structs when correcting a field type. (5) Embedding cache keys must use full-text hashes (SHA-256), NOT truncated text prefixes — prefix collisions cause data corruption. (6) Entity extraction gating must use `options.extract_entities`, NEVER `enable_contextual_prefix` — the two features are independent. (7) ALL `_build_rust_*` helpers must include ALL struct fields (predicate, time, location, participants, doc_origins) on ALL code paths (ingest, feed, manual graph router). (8) `delete_collection` must drop per-collection LanceDB tables, not just the system table entry. (9) Auth middleware MUST check JWT revocation blocklist after signature/expiry validation. (10) Frontend ENTITY_TYPE_COLORS must include ALL canonical NER labels from `SPACY_TO_CANONICAL` + legal labels — use a shared module. (11) Access tokens in localStorage violate spec — use Redux state only. (12) Ontology generate MUST show proposal review with Apply/Reject before applying. (13) Search autocomplete MUST debounce (300ms). (14) 401→refresh→retry MUST have a retry limit to prevent infinite loops. (15) WAL truncation MUST use atomic write-rename, not in-place `File::create`. (16) Tantivy search MUST use `BooleanQuery` with `collection_id` pre-filter, not post-filter. (17) Dijkstra MUST enforce `max_depth` to prevent unbounded search. (18) Dashboard delete MUST show confirmation dialog. (19) WebSocket MUST have exponential backoff reconnection. (20) SSE `onerror` MUST retry with backoff, not silently close.
