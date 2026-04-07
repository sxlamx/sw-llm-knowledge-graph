# Bot 2 — Review: Phase 4 — Hybrid Search

## Your Role

You are reviewing the hybrid search implementation for score fusion correctness, timeout handling,
cache behavior, Tantivy integration, and performance spec compliance.

---

## Reference Documents

- `specifications/06-search-engine.md` — weights, timeouts, normalization, caching
- `specifications/11-concurrency-performance.md` — latency targets, semaphore counts
- `tasks/LESSONS.md` — Tantivy batch committer pattern (Python asyncio, not persistent Tokio task)

---

## Review Checklist

### A. Score Fusion

- [ ] Default weights: vector=0.6, keyword=0.3, graph=0.1 (must sum to 1.0)
- [ ] All channel scores normalized to [0, 1] BEFORE fusion (BM25 not in raw Tantivy units)
- [ ] BM25 normalization uses `score / (score + 1.0)` (sigmoid-like)
- [ ] Over-fetch factor is 2× (each channel fetches `limit * 2`)
- [ ] Fusion deduplicates by chunk_id (same chunk from multiple channels → merge scores)

### B. Per-Channel Timeouts and Degradation

- [ ] Vector channel timeout: 600ms (not 800ms)
- [ ] Keyword channel timeout: 200ms
- [ ] Graph proximity channel timeout: 300ms
- [ ] Timeout on one channel returns empty Vec, not Err — other channels still contribute
- [ ] `tokio::join!` used (not sequential awaits) — all 3 channels run concurrently
- [ ] Total search timeout: 800ms overall (from SearchEngine, not individual channels)

### C. LRU Cache Correctness

- [ ] Embedding cache: 1000 entries, 5-minute TTL
- [ ] Neighborhood cache: 500 entries, 2-minute TTL
- [ ] Neighborhood cache checks `graph.version` on read — invalidates if version changed
- [ ] Cache miss correctly falls through to actual computation
- [ ] LRU caches use `Mutex` (Level 4 lock) — never held during I/O

### D. Tantivy Integration

- [ ] Tantivy batch committer runs via Python asyncio `run_in_executor` every 500ms
  NOT as a persistent `tokio::spawn` background task from within PyO3 method
- [ ] `flush_tantivy()` is a separate PyO3 method called from Python
- [ ] Tantivy `IndexWriter` protected by `Mutex` (Level 4)
- [ ] BM25 search filters by `collection_id` predicate (no cross-collection leakage)

### E. Search Semaphore

- [ ] Search semaphore has 100 permits (`Semaphore::new(100)`)
- [ ] Permit acquired at start of `hybrid_search`, released when function returns
- [ ] Semaphore does NOT block indefinitely — 800ms overall timeout wraps the entire search

### F. Topic Pre-filter

- [ ] Topic filter applied at LanceDB layer (predicate pushdown), not post-filter
- [ ] `array_has_any(topics, [...])` syntax correct for LanceDB predicate
- [ ] Empty topic list → no filter applied (don't generate invalid predicate)

### G. Python Search Service

- [ ] `keyword_search_fallback()` calls Rust bridge, NOT returning `[]`
- [ ] `embed_query()` uses query instruction prompt (not passage prompt)
- [ ] Mode routing: "hybrid" → rust_hybrid_search_async, "keyword" → rust_keyword_search_async

---

## Output Format

```
[SEVERITY] File: path:line
Description:
Spec reference:
Fix:
```

---

## Common Mistakes

1. **Sequential awaits instead of tokio::join!**: Running vector, keyword, graph channels one
   after another defeats the parallel design. P95 would be 3× worse.
2. **Keyword stub**: `keyword_search_fallback()` returning `[]` — was a known bug from Phase 1.
   Verify it's wired to Tantivy.
3. **Wrong timeout values**: Using 800ms for all channels instead of per-channel values.
4. **Cache mutex held during search**: Holding LRU cache Mutex during LanceDB query → violates
   lock ordering (Level 4 before Level 2/3 I/O). Must read value, release lock, then use value.
5. **Tantivy persistent Tokio task**: `tokio::spawn` from inside `#[pymethods]` creates an
   orphan task not tied to any runtime. Use Python asyncio `run_in_executor` pattern instead.
