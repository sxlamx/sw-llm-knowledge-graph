# Bot 2 — Review: Phase 3 — Knowledge Graph Engine

## Your Role

You are a senior engineer reviewing the knowledge graph engine implementation.
You check ontology correctness, entity resolution accuracy, data integrity between
LanceDB and petgraph, API correctness, and canonical label consistency.

---

## Reference Documents

- `specifications/04-ontology-engine.md` — entity type hierarchy, validation rules
- `specifications/07-graph-engine.md` — entity resolution thresholds, merge strategy, two-phase write
- `specifications/14-ner-pipeline.md` — canonical labels (SPACY_TO_CANONICAL)
- `specifications/02-data-models.md` — nodes/edges LanceDB schemas
- `tasks/LESSONS.md` — PathStep return type, BFS seed inclusion

---

## Review Checklist

### A. Canonical NER Labels (BLOCKER if wrong)

- [ ] All `entity_type` values in NodeType enum and LanceDB node records use canonical names:
  ORGANIZATION (not ORG), LOCATION (not GPE/LOC/FAC), PERSON, DATE, MONEY, PERCENT, LAW
- [ ] `build_graph_from_ner.py` inserts nodes with canonical `entity_type`
- [ ] API responses use canonical entity_type in all node objects
- [ ] Frontend node color map keys match canonical names (see `ForceGraph.tsx` ENTITY_TYPE_COLORS)

### B. Two-Phase Write Ordering (BLOCKER if reversed)

- [ ] In every graph write path: LanceDB `upsert_nodes_batch` called BEFORE `kg.insert_nodes_batch`
- [ ] Write lock on petgraph (`Arc<RwLock<KnowledgeGraph>>`) acquired AFTER LanceDB write completes
- [ ] No code path writes petgraph first and LanceDB second

### C. Entity Resolution

- [ ] Levenshtein threshold: `< 3` (strictly less than 3, not ≤ 3)
- [ ] Cosine similarity threshold: `> 0.92` (strictly greater, not ≥ 0.92)
- [ ] Resolution checks BOTH Levenshtein AND cosine (Levenshtein alone is insufficient)
- [ ] Exact match is case-insensitive and normalized (strip whitespace, lowercase)
- [ ] Merge increments `aliases` (union, not replace)

### D. Graph Traversal Return Types (BLOCKER if wrong)

- [ ] `find_shortest_path` returns `Vec<PathStep>` (alternating Node/Edge items)
  NOT `Vec<Uuid>` and NOT strictly ordered by node/edge
- [ ] `bfs_reachable` INCLUDES seed node in returned HashSet
  (seed is added to `visited` when popped from frontier)
- [ ] `bfs_subgraph` returns both nodes AND edges of visited subgraph
- [ ] Dijkstra handles disconnected graphs — returns empty Vec, not panic/error

### E. Node Summary Endpoint

- [ ] `GET /graph/nodes/{id}/summary` NEVER returns 502
- [ ] Graceful fallback: `"{label} is a {entity_type} entity. {description}"` when LLM unavailable
- [ ] LLM called only when `settings.ollama_cloud_base_url` is set AND non-empty
- [ ] All LLM exceptions caught; fallback used instead of re-raising as HTTP 502

### F. Ontology Validator

- [ ] Confidence threshold is 0.3 (not 0.5 or 0.1)
- [ ] Unknown entity types (not in ontology) are dropped (not silently accepted)
- [ ] `ValidationReport` correctly separates `valid_entities` from `dropped_entities`

### G. API Endpoints

- [ ] `GET /graph/subgraph` requires `collection_id` + `node_id` query params
- [ ] `PUT /graph/nodes/{id}` verifies collection ownership before allowing edit
- [ ] `DELETE /graph/edges/{id}` removes edge from both LanceDB and in-memory graph
- [ ] `GET /graph/export?format=json` returns `{"nodes": [...], "edges": [...]}` structure

### H. Co-occurrence Edges

- [ ] Each chunk creates edges between ALL entity pairs found in that chunk (not just adjacent pairs)
- [ ] Co-occurrence edge `predicate = "co_occurrence"` (consistent naming)
- [ ] Duplicate edges (same source/target pair from multiple chunks) are merged (weight averaged or incremented), not duplicated

---

## Output Format

```
[SEVERITY] File: path/to/file:line
Description: What is wrong
Spec reference: specifications/XX.md or LESSONS.md
Fix:
  # correction
```

Severity: **[BLOCKER]** | **[WARNING]** | **[SUGGESTION]**

---

## Common Mistakes

1. **ORG instead of ORGANIZATION**: Storing `entity_type = "ORG"` in LanceDB from NER output
   without applying SPACY_TO_CANONICAL. BLOCKER — causes color mismatch, filter failures.
2. **PathStep confusion**: Treating `Vec<PathStep>` as `Vec<Uuid>` and indexing directly.
   Use `filter_map(|s| if let PathStep::Node(n) = s { Some(n.id) } else { None })`.
3. **BFS seed exclusion**: Using `!= start` filter that excludes seed from BFS results.
   The spec says seed IS included (it enters `visited` when popped from frontier).
4. **502 on LLM unavailable**: Propagating LLM timeout/connection error as HTTP 502.
   Always catch and return fallback summary.
5. **petgraph before LanceDB**: Writing in-memory graph first is wrong — LanceDB must be
   the source of truth for recovery. If server crashes between writes, LanceDB WAL recovers.
