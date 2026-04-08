# Bot 2 — Review: Phase 8 (Two-Stage Extraction)

> **Feature**: F2 (Two-Stage Extraction)
> **Spec References**: `15-hyper-extract-integration.md` Section 2.2

---

## Role

Senior code reviewer. Audit Phase 8 implementation for spec compliance, correctness, and data integrity.

---

## Reference Documents

- `specifications/15-hyper-extract-integration.md` Section 2.2
- `specifications/03-ingestion-pipeline.md`
- `tasks/LESSONS.md`
- `prompts/README.md` (cross-cutting rules)

---

## Review Checklist

### A. Two-Stage Extraction Logic

- [ ] Stage 1 (entity extraction) uses `entity_schema` from template to build structured output
- [ ] Stage 2 (relation extraction) receives the entity list from Stage 1 as context
- [ ] Stage 2 prompt explicitly instructs "ONLY create relationships involving entities from the known list"
- [ ] Stage 2 prompt includes formatted entity list with `name`, `entity_type`, and any required fields
- [ ] Empty entity list from Stage 1 skips Stage 2 (returns empty relations, not an error)
- [ ] One-stage mode still works when `extraction.mode` is `"one_stage"`
- [ ] One-stage mode uses combined entity+relation schema in a single LLM call
- **Severity**: HIGH — missing entity context in Stage 2 defeats the purpose

### B. Dynamic Pydantic Models

- [ ] `build_entity_pydantic_model()` creates a model with all fields from `entity_schema.fields`
- [ ] `build_relation_pydantic_model()` creates a model with all fields from `relation_schema.fields`
- [ ] Optional fields (`required: false`) are `Optional[Type] = None` in the model
- [ ] `List` type fields in schema become `List[str]` in Pydantic model
- [ ] Model names are unique per template (e.g., `"graph_Entity"`, `"graph_Relation"`)
- [ ] List wrapper models (`EntityList`, `RelationList`) have `items: List[EntityModel]`
- **Severity**: HIGH — wrong Pydantic models produce malformed LLM output

### C. Prompt Construction

- [ ] System prompt includes entity/role definition from template
- [ ] `node_prompt_extra` is appended to entity system prompt when present
- [ ] `edge_prompt_extra` is appended to edge system prompt when present
- [ ] Output format section lists all fields with type and required/optional status
- [ ] Edge extraction prompt includes "CRITICAL RULES" section with entity constraints
- [ ] No template prompts are leaked through API responses
- **Severity**: MEDIUM — poor prompts reduce extraction quality

### D. Dangling Edge Pruning

- [ ] Binary edges pruned when `source` or `target` not in entity key set
- [ ] Hyperedges pruned when ANY participant not in entity key set
- [ ] Pruning runs after every two-stage extraction (not optional)
- [ ] Rust `prune_dangling_edges()` handles both `participants: Option<Vec<Uuid>>` and null participants
- [ ] Pruning log message includes count of pruned edges
- [ ] Pruning does NOT remove valid edges whose entities exist in the entity set
- **Severity**: HIGH — unpruned dangling edges corrupt the graph

### E. Ollama Cloud API Client

- [ ] `call_ollama_cloud()` is the single point of LLM API access
- [ ] No other module makes direct `httpx` calls to Ollama Cloud
- [ ] API key read from `settings.ollama_cloud_api_key`
- [ ] Missing API key raises clear error, not silent failure
- [ ] 401 responses raise descriptive error
- [ ] 429 responses trigger exponential backoff (3 retries)
- [ ] 500 responses raise descriptive error
- [ ] Markdown code fences stripped from LLM response
- [ ] Response structure includes `content` and `usage` fields
- [ ] `temperature` and `max_tokens` are configurable parameters
- **Severity**: HIGH — broken LLM client breaks all extraction

### F. Cost Tracking Integration

- [ ] Every `call_ollama_cloud()` call is wrapped in `cost_tracker` tracking
- [ ] Token counts extracted from response `usage` field
- [ ] Budget cap per ingest job is enforced
- [ ] `BudgetExceededError` raised when cap is hit
- **Severity**: MEDIUM — unbounded LLM spend

### G. Ingest Worker Integration

- [ ] `_extract_graph_with_template()` is called when a template is provided
- [ ] `_extract_graph()` (existing) is called when no template is provided
- [ ] Dedup keys computed from template key pattern and attached to entities/relations
- [ ] Display labels computed from template label patterns and attached
- [ ] Two-stage extraction processes chunks sequentially (not parallel, to preserve entity context)
- [ ] One-stage extraction can process chunks in parallel (existing behavior)
- **Severity**: HIGH — wrong extraction path produces bad graphs

### H. Rust Pruning

- [ ] `prune_dangling_edges()` correctly iterates over `edges` HashMap
- [ ] Binary edge check: `!valid_node_ids.contains(&edge.source) || !valid_node_ids.contains(&edge.target)`
- [ ] Hyperedge check: `participants.iter().any(|p| !valid_node_ids.contains(p))`
- [ ] Adjacency maps rebuilt after pruning (`graph.rebuild_adjacency()`)
- [ ] Returns count of pruned edges
- [ ] Existing edges NOT dangling are preserved exactly
- **Severity**: HIGH — incorrect pruning silently corrupts graph data

### I. Backward Compatibility

- [ ] Existing `extract_from_chunk()` in `extractor.py` still works unmodified
- [ ] Existing NER pipeline still works unmodified
- [ ] Existing `_extract_graph()` in `ingest_worker.py` still works unmodified
- [ ] Templates are optional — `None` means use existing extraction
- [ ] Existing LanceDB records without `dedup_key`, `display_label`, `predicate` still read correctly

---

## Common Mistakes to Watch For

1. **Stage 2 missing entity context** — if the known-entity list is not formatted and included in the edge extraction prompt, two-stage extraction degrades to disconnected edges
2. **Direct httpx calls to Ollama Cloud** outside of `ollama_client.py` — must route all calls through the centralized client
3. **Pruning before dedup** — edges should be deduplicated first, then pruned. Pruning before dedup can leave dangling edge remnants
4. **Missing `response_format`** — Ollama Cloud requires `response_format: {"type": "json_object"}` for structured output
5. **Parallel two-stage extraction** — chunks must be processed sequentially in two-stage mode because Stage 2 depends on Stage 1 results within each chunk
6. **Not clearing markdown fences** — LLM responses often wrap JSON in ```json...```, must strip
7. **Rust pruning not rebuilding adjacency** — if `rebuild_adjacency()` is not called after removing edges, the in-memory graph becomes inconsistent
8. **Cost tracker not tracking two-stage calls** — two LLM calls per chunk doubles the cost

---

## Output Format

For each issue found:

```
### Issue: [short title]
- **File**: `path/to/file.py:line`
- **Section**: [A-I from checklist]
- **Severity**: CRITICAL | HIGH | MEDIUM | LOW
- **Description**: [what's wrong]
- **Fix**: [specific code change]
```

After listing all issues, provide a summary of changes applied.