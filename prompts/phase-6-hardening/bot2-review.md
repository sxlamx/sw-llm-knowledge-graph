# Bot 2 — Review: Phase 6 — Production Hardening

## Your Role

You are performing a thorough concurrency safety, security, and performance review of the
Phase 6 hardening implementation. This is the highest-stakes review — deadlocks and data
corruption are possible if the concurrency model is implemented incorrectly.

---

## Reference Documents

- `specifications/05-index-manager.md` — CRITICAL: full concurrency model and lock ordering
- `specifications/11-concurrency-performance.md` — CRITICAL: performance targets, lock ordering table
- `specifications/10-auth-security.md` — rate limiting, token revocation

---

## Review Checklist

### A. Lock Ordering (CRITICAL — DEADLOCK if violated)

- [ ] All code paths acquire locks in strict order: Level 1 (atomics) → Level 2 (tables RwLock) →
  Level 3 (graph RwLock) → Level 4 (Mutex leaves)
- [ ] Level 2 `tables.write()` is held ONLY for `HashMap::insert` — never held during I/O
- [ ] Level 3 graph write lock held ONLY during `insert_nodes_batch` / `insert_edges_batch`
  — never held during LanceDB network calls
- [ ] Level 4 Mutex (LRU cache, Tantivy writer) acquired only briefly; not while holding Level 2 or 3
- [ ] No `std::sync::MutexGuard` or `tokio::sync::RwLockWriteGuard` held across `.await` points

### B. Shadow Table Swap (BLOCKER if wrong)

- [ ] Shadow table built WITHOUT holding Level 2 lock (searches continue on live table during build)
- [ ] Level 2 write lock acquired ONLY for `HashMap::insert` (pointer swap, ~50μs)
- [ ] State transitions use `compare_exchange` (not `store`) to prevent race with concurrent state change
- [ ] On rebuild failure: state reverted to ACTIVE (not left as COMPACTING)
- [ ] Old Arc is NOT manually freed — it's dropped when refcount reaches 0 (Arc semantics)

### C. 800ms Search Timeout

- [ ] `tokio::time::timeout(Duration::from_millis(800), ...)` wraps entire search operation
- [ ] Timeout returns empty results (not Err) — caller handles gracefully
- [ ] Per-channel sub-timeouts still apply within the 800ms window

### D. LRU Cache Safety

- [ ] Embedding cache Mutex held ONLY to read/write cache — never during model inference
- [ ] Neighborhood cache: version check `entry.1 == kg.version.load()` on read
  — invalidate (remove) if version mismatch
- [ ] Cache TTL enforced on read (not just on eviction)
- [ ] LRU cache size limits: embedding=1000, neighborhood=500 — verify correct values

### E. Batch Write Buffer

- [ ] Flush triggered on `>= 512 rows` OR `>= 1 second since last flush` (whichever comes first)
- [ ] Pending buffer cleared after successful flush (not left with stale data)
- [ ] Write semaphore (1 permit) held during flush (prevents concurrent flushes)

### F. Rate Limiting

- [ ] Sliding window (not token bucket): timestamps list per user, expire entries outside window
- [ ] Returns 429 with `Retry-After: 60` and `X-RateLimit-Limit: 60` headers
- [ ] Middleware applied to ALL routes except `/health` and `/metrics` (Prometheus must be unauthenticated)
- [ ] `asyncio.Lock()` protects the in-memory rate limiter state

### G. Prometheus Metrics

- [ ] `kg_concurrent_searches` gauge present and updated on search start/end
- [ ] `kg_index_state` gauge reflects actual `IndexManager.state` value
- [ ] `GET /metrics` endpoint has NO authentication requirement
- [ ] No sensitive data (user IDs, document content) in metric labels

### H. WAL Checkpoint

- [ ] WAL truncated (checkpointed) AFTER successful recovery replay — not before
- [ ] Recovery failure: WAL NOT truncated; server starts in DEGRADED state
- [ ] Checkpoint is an atomic operation (write new file, rename over old)

### I. Graph Pruning

- [ ] Pruning triggered hourly by Python asyncio loop (not persistent Rust Tokio task)
- [ ] LanceDB updated BEFORE petgraph update (two-phase write rule maintained)
- [ ] Orphan tombstoning does NOT hard-delete nodes (only marks `tombstoned=true`)

---

## Output Format

```
[SEVERITY] File: path:line
Description:
Spec reference:
Fix:
  // code correction
```

Severity: **[BLOCKER]** | **[WARNING]** | **[SUGGESTION]**

---

## Common Mistakes

1. **MutexGuard across await**: `let _guard = cache.lock().await; cache_value.clone(); some_await().await;`
   This holds the Mutex across `.await` — blocks all other tasks. Must release guard before await.
2. **Shadow build while holding Level 2**: Building shadow table inside `tables.write()` scope
   blocks ALL concurrent searches for the full rebuild duration (minutes). BLOCKER.
3. **compare_exchange vs store**: Using `state.store(COMPACTING)` instead of `compare_exchange`
   allows two concurrent threads to both think they won the race to start compaction.
4. **WAL checkpoint before recovery**: Truncating WAL before verifying all entries replayed
   successfully means data loss if replay fails partway. Checkpoint only after full success.
5. **rate_limit middleware excludes /metrics**: If /metrics requires auth, Prometheus cannot scrape it.
   Verify `/health` and `/metrics` are exempt from rate limiting AND auth middleware.
