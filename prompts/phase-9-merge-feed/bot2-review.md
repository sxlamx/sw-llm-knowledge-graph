# Bot 2 — Review: Phase 9 (Merge Strategies + Incremental Feeding)

> **Features**: F3 + F4

---

## Review Checklist

### A. Deterministic Merge Strategies (Rust)

- [ ] `DeterministicMergeStrategy` enum matches 3 variants: `KeepFirst`, `KeepLast`, `FieldOverwrite`
- [ ] `KeepFirst` returns existing node unchanged
- [ ] `KeepLast` returns incoming node with existing UUID preserved
- [ ] `FieldOverwrite` merges: null fields overwritten by incoming non-null, aliases appended (no duplicates), confidence averaged, descriptions concatenated with ` | `
- [ ] `merge_edges_deterministic` handles `doc_origins` append, `context` concatenation, `weight` averaging
- [ ] Edge `participants` list append (no duplicates) for hyperedges
- [ ] All merge functions return a new `GraphNode`/`GraphEdge` — do NOT mutate the inputs
- **Severity**: HIGH — merge bugs corrupt data silently

### B. Conflict Detection (Rust)

- [ ] `detect_node_conflicts()` builds HashMap from existing nodes' `dedup_key`
- [ ] Returns empty vec when no conflicts found
- [ ] For each conflict, records field-level differences (existing vs incoming value)
- [ ] Handles None/null fields correctly — None vs Some("value") is a conflict
- [ ] Does NOT hold L2 lock during detection (clone Arc, release, then compare)
- **Severity**: MEDIUM

### C. PyO3 Merge Methods

- [ ] `merge_nodes_into_collection()` accepts strategy string ("keep_first", "keep_last", "field_overwrite")
- [ ] Invalid strategy string returns clear PyError, not panic
- [ ] `merge_edges_into_collection()` works with new fields (`predicate`, `time`, `location`, `participants`)
- [ ] `prune_dangling_edges()` locks L3 graph, prunes, rebuilds adjacency, returns count
- [ ] All merge methods log the number of merged/inserted/conflicted items
- [ ] WAL is written BEFORE in-memory graph update (not after)
- **Severity**: HIGH — PyO3 API bugs are runtime crashes

### D. Merge Strategy Enum (Python)

- [ ] All 7 strategies present: exact, keep_first, keep_last, field_overwrite, llm_balanced, llm_prefer_first, llm_prefer_last
- [ ] `is_deterministic` property returns True for exact, keep_first, keep_last, field_overwrite
- [ ] `rust_strategy_name` returns None for exact (not "exact"), correct strings for others
- [ ] `EXACT` strategy in Python maps to existing Rust dedup behavior (no merge, drop duplicate)
- **Severity**: MEDIUM

### E. LLM Entity Merger (Python)

- [ ] `_llm_merge()` constructs prompt specifying the schema fields
- [ ] Bias instruction matches strategy: "prefer existing" / "prefer incoming" / "balance equally"
- [ ] Canonical ID preserved after LLM merge (existing UUID kept)
- [ ] LLM response parsed with `json.loads()` with fallback for malformed JSON
- [ ] `call_ollama_cloud()` used for LLM call (not direct httpx)
- [ ] Cost tracked per merge call
- [ ] Network errors handled gracefully (fallback to KEEP_FIRST on LLM failure)
- **Severity**: HIGH — LLM merge is the core feature; broken LLM call produces garbage data

### F. Incremental Feed API

- [ ] `POST /collections/{id}/feed` validates collection exists and user owns it
- [ ] `feed_mode=True` ingest job created and tracked in `ingest_jobs` LanceDB table
- [ ] Feed request accepts `file_paths` and optional `template` key
- [ ] Returns 202 with job_id (same pattern as `POST /ingest/folder`)
- [ ] SSE progress stream at `GET /ingest/jobs/{id}/stream` works for feed jobs
- **Severity**: MEDIUM

### G. Feed Pipeline

- [ ] Feed pipeline processes new files only (skips files already indexed by BLAKE3 hash)
- [ ] New entities merged with existing using template's merge_strategy_nodes
- [ ] New edges merged with existing using template's merge_strategy_edges
- [ ] Dangling edges pruned after merge
- [ ] LanceDB AND in-memory graph both updated
- [ ] No full graph rebuild — only incremental upsert
- [ ] WAL logs feed operations for crash recovery
- [ ] Template is optional — if not provided, use EXACT strategy (current dedup behavior)
- **Severity**: HIGH — feed bugs corrupt the existing graph

### H. Lock Ordering

- [ ] Merge operations do NOT hold L2 lock during I/O (clone Arc, release L2, then work)
- [ ] L3 per-collection lock held only for brief graph mutation (not during LLM calls)
- [ ] LLM merge calls happen WITHOUT any locks held
- [ ] WAL write before graph mutation (write-ahead, not write-after)
- **Severity**: CRITICAL — lock violations cause deadlocks in production

### I. Backward Compatibility

- [ ] Existing `run_ingest_pipeline()` (non-feed) works unchanged
- [ ] Existing merge behavior (exact dedup) preserved when no template specified
- [ ] LanceDB tables with missing new columns (`dedup_key`, `display_label`) still readable

---

## Common Mistakes

1. **Mutating existing node during merge** — must return a new GraphNode, not mutate in place
2. **Holding L3 lock during LLM call** — LLM calls can take 10+ seconds; holding the lock blocks all reads
3. **Losing canonical UUID** — merged entity must keep the existing node's UUID; replacing it breaks all edge references
4. **Not updating LanceDB** — in-memory graph updated but LanceDB not written = data loss on restart
5. **WAL after mutation** — must write WAL BEFORE mutation, not after
6. **Strategy string mismatch** — Python "field_overwrite" must map to Rust `FieldOverwrite` exactly
7. **Feed re-processing all documents** — must skip already-indexed files by BLAKE3 hash check
8. **LLM merge failure with no fallback** — must fall back to KEEP_FIRST, not lose data

---

## Output Format

Standard review format with file, section, severity, description, and fix for each issue.