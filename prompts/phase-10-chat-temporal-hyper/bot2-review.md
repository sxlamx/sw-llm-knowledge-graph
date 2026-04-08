# Bot 2 — Review: Phase 10 (Knowledge Chat + Temporal/Spatial + Hyperedges)

> **Features**: F5 + F6 + F7

---

## Review Checklist

### A. Knowledge Chat Service

- [ ] `search_knowledge()` embeds query using `embed_query()` (not `embed_texts()`)
- [ ] Query embedding passed to Rust `search_nodes()` and `search_edges()` as JSON array
- [ ] Empty search results produce "No relevant information found." context (not error)
- [ ] Chat prompt includes structured context sections: "=== Relevant Entities ===" and "=== Relevant Relations ==="
- [ ] Chat answer cites specific entities/relations from context when possible
- [ ] Response includes `answer`, `nodes`, and `edges` keys
- [ ] Node/edge data in context is JSON-serialized with `default=str` for non-serializable types
- [ ] `call_ollama_cloud()` used for chat LLM call (not direct httpx)
- **Severity**: HIGH — chat is user-facing and must work reliably

### B. Chat API Endpoint

- [ ] `POST /collections/{id}/chat` requires authentication
- [ ] Validates collection ownership before processing
- [ ] Rate-limited (counts against LLM-heavy request limit: 5/min)
- [ ] `ChatRequest` schema has `query` (required), `top_k_nodes` (default 5), `top_k_edges` (default 5)
- [ ] Returns 200 with `{answer, nodes, edges}` on success
- [ ] Returns 404 for non-existent collection
- [ ] Returns 401 for unauthenticated requests
- [ ] Cost tracked per chat call
- **Severity**: MEDIUM

### C. Rust search_nodes / search_edges

- [ ] `search_nodes()` opens `{collection_id}_nodes` LanceDB table, queries embedding column
- [ ] `search_edges()` opens `{collection_id}_edges` LanceDB table, queries embedding column
- [ ] Both return JSON array of `{item, score}` with similarity scores
- [ ] Both handle empty tables gracefully (return empty JSON array, not error)
- [ ] Both release L2 lock after cloning table Arc (brief lock holding)
- [ ] `search_edges()` applies `time_from`/`time_to` filter when provided (string comparison)
- [ ] `search_edges()` applies `location` filter when provided (exact match or substring)
- [ ] Filter parameters are Optional<&str> (can be None)
- [ ] If Rust LanceDB search not yet implemented, Python fallback via `rust_bridge.py` is used
- **Severity**: HIGH — search is critical for chat quality

### D. Temporal Extraction Prompts

- [ ] Temporal templates inject "Current Observation Date: {date}" into edge extraction prompt
- [ ] Prompt instructs LLM to resolve relative time ("last year", "yesterday") based on observation date
- [ ] Prompt instructs to keep explicit dates as written
- [ ] Prompt instructs to leave time field empty when no time info, NOT hallucinate
- [ ] `observation_time` defaults to today's date in ISO format
- [ ] `observation_time` can be overridden by template or API parameter
- **Severity**: MEDIUM — poor temporal prompts produce bad time extraction

### E. Spatial Extraction Prompts

- [ ] Spatial templates inject "Current Observation Location: {location}" into edge extraction prompt
- [ ] Prompt instructs to resolve relative locations ("here", "nearby") based on observation location
- [ ] Prompt instructs to keep explicit locations as written
- [ ] Prompt instructs to leave location field empty when no location info
- [ ] `observation_location` defaults to "Unknown"
- **Severity**: MEDIUM

### F. Temporal/Spatial Dedup Key Composition

- [ ] Temporal edge key: `{source}|{predicate}|{target}@{time}` — time appended with `@`
- [ ] Spatial edge key: `{source}|{predicate}|{target}@{location}` — location appended with `@`
- [ ] Spatio-temporal: `{source}|{predicate}|{target}@{time}|{location}`
- [ ] Empty time or location does NOT produce `@` or `at` — handled in key compiler
- [ ] Same edge with different times produces different dedup keys (correctly treated as different edges)
- [ ] Same edge with same time and same entities produces same dedup key (correctly deduplicated)
- **Severity**: HIGH — wrong dedup keys create duplicate or missing edges

### G. Hyperedge Storage and Adjacency

- [ ] `participants: Option<Vec<Uuid>>` stored correctly in LanceDB edges table
- [ ] `participants` serialized as JSON array of UUID strings in LanceDB
- [ ] Binary edges use `source`+`target` (participants is None)
- [ ] Hyperedges use `participants` (source/target may also be set for backward compat, or left None)
- [ ] Adjacency maps updated for hyperedges: each participant has edges to all other participants
- [ ] `prune_dangling_edges()` checks ALL participants exist for hyperedges
- [ ] Frontend graph viewer can render hyperedges (at minimum: highlight all connected nodes)
- **Severity**: HIGH — wrong adjacency means broken graph traversal

### H. Frontend Chat Panel

- [ ] `ChatPanel` component renders message history (user + assistant)
- [ ] Chat input with send button
- [ ] Toggle between "Search Chunks" (existing search) and "Ask Knowledge Graph" (new chat)
- [ ] Retrieved nodes/edges displayed alongside answer
- [ ] Error state handled when chat API fails
- [ ] Rate limit exceeded shows user-friendly message (not raw 429)
- **Severity**: LOW — UI-only

### I. Frontend Temporal/Spatial Display

- [ ] Edges with `time` show `@2024` badge on edge label
- [ ] Edges with `location` show `@New York` badge on edge label
- [ ] Filter controls for time range and location (if implemented)
- [ ] Filter params passed to backend API

---

## Common Mistakes

1. **Using `embed_texts()` for query** — must use `embed_query()` which adds the query instruction prefix for asymmetric retrieval
2. **Time filter comparing strings** — "2024-01" < "2024-02" works for ISO format but not for "January 2024"
3. **Empty time producing `@` in dedup key** — must handle `time=None` and `location=None` by omitting the suffix
4. **Hyperedge adjacency only connecting to source** — must connect ALL participants to each other
5. **Chat endpoint not rate-limited** — each chat call is an LLM call; must count against the LLM-heavy rate limit
6. **search_nodes/search_edges holding L2 lock during vector search** — clone Arc, release, then search
7. **Temporal prompt not injecting observation_time** — the template's `time_field` must trigger prompt injection
8. **Missing `default=str` in JSON serialization** — datetime, UUID, and other non-serializable types will crash `json.dumps()`

---

## Output Format

Standard review format with file, section, severity, description, and fix for each issue.