# Bot 1 — Build: Phase 9 (Merge Strategies + Incremental Feeding)

> **Features**: F3 (LLM-Powered Entity/Edge Field-Level Merging) + F4 (Incremental Document Feeding)
> **Spec References**: `15-hyper-extract-integration.md` Sections 2.3, 2.4, 7.2.2, 7.2.3
> **Depends On**: Phase 7 (templates), Phase 8 (two-stage extraction)

---

## Role

You are a senior engineer implementing intelligent entity/edge merging and incremental document feeding for the sw-llm-knowledge-graph project.

---

## Project Context

The current entity resolution is 3-step deterministic (exact → Levenshtein → cosine). This works for dedup but cannot intelligently merge conflicting field values (e.g., two descriptions of the same entity). This phase adds:

- **7 merge strategies** (3 deterministic in Rust, 4 LLM-based in Python)
- **Incremental feeding** — add new documents to an existing graph without rebuilding

**All LLM calls use Ollama Cloud API** via `call_ollama_cloud()` from `ollama_client.py`.

---

## LESSONS.md Rules

- NER model: always `en_core_web_trf`, never `en_core_web_sm`
- LanceDB for all metadata, no PostgreSQL
- Lock ordering: L1 (atomic) → L2 (outer HashMap) → L3 (per-collection) → L4 (leaf)
- `crate-type = ["cdylib", "rlib"]`

---

## Implementation Tasks

### Task 1: Rust — Deterministic Merge Strategies

Create `rust-core/src/graph/merge.rs`:

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum DeterministicMergeStrategy {
    KeepFirst,
    KeepLast,
    FieldOverwrite,
}

pub fn merge_nodes_deterministic(
    existing: &GraphNode,
    incoming: &GraphNode,
    strategy: &DeterministicMergeStrategy,
) -> GraphNode {
    match strategy {
        DeterministicMergeStrategy::KeepFirst => existing.clone(),
        DeterministicMergeStrategy::KeepLast => {
            let mut merged = incoming.clone();
            merged.id = existing.id; // Preserve canonical ID
            merged
        }
        DeterministicMergeStrategy::FieldOverwrite => {
            let mut merged = existing.clone();
            // Overwrite null fields with incoming
            if incoming.description.is_some() && existing.description.is_none() {
                merged.description = incoming.description.clone();
            } else if incoming.description.is_some() && existing.description.is_some() {
                merged.description = Some(format!("{} | {}", existing.description.as_ref().unwrap(), incoming.description.as_ref().unwrap()));
            }
            // Append new aliases
            for alias in &incoming.aliases {
                if !merged.aliases.contains(alias) {
                    merged.aliases.push(alias.clone());
                }
            }
            // Average confidence
            merged.confidence = (existing.confidence + incoming.confidence) / 2.0;
            merged
        }
    }
}

pub fn merge_edges_deterministic(
    existing: &GraphEdge,
    incoming: &GraphEdge,
    strategy: &DeterministicMergeStrategy,
) -> GraphEdge {
    // Similar logic for edges: preserve canonical ID, merge context, append doc_origins
}
```

### Task 2: Rust — Conflict Detection

Add to `rust-core/src/graph/merge.rs`:

```rust
#[derive(Serialize, Deserialize)]
pub struct FieldConflict {
    pub field_name: String,
    pub existing_value: Option<serde_json::Value>,
    pub incoming_value: Option<serde_json::Value>,
}

#[derive(Serialize, Deserialize)]
pub struct MergeConflict {
    pub existing_id: Uuid,
    pub incoming_dedup_key: String,
    pub field_conflicts: Vec<FieldConflict>,
}

pub fn detect_node_conflicts(
    existing_nodes: &[GraphNode],
    incoming_nodes: &[GraphNode],
) -> Vec<MergeConflict> {
    // Build HashMap<String, Uuid> from existing nodes' dedup_key
    // For each incoming node with a dedup_key matching an existing node,
    // compare fields and record conflicts
}
```

### Task 3: PyO3 — Expose Merge Methods

In `rust-core/src/lib.rs`:

```rust
#[pymethods]
impl IndexManager {
    fn detect_node_conflicts(&self, collection_id: &str, new_nodes_json: &str) -> PyResult<String> {
        // 1. Get existing nodes from in-memory graph
        // 2. Parse incoming nodes
        // 3. Call detect_node_conflicts()
        // 4. Return JSON
    }

    fn merge_nodes_into_collection(&self, collection_id: &str, new_nodes_json: &str, strategy: &str) -> PyResult<String> {
        // Parse strategy string → DeterministicMergeStrategy
        // For each new node:
        //   - If dedup_key matches existing → merge_deterministic()
        //   - If no match → insert as new
        // Return merge report JSON: { merged: N, inserted: N, conflicts: [...] }
    }

    fn merge_edges_into_collection(&self, collection_id: &str, new_edges_json: &str, strategy: &str) -> PyResult<String> {
        // Similar to merge_nodes_into_collection
    }

    fn prune_dangling_edges(&self, collection_id: &str) -> PyResult<usize> {
        // Lock graph, call prune_dangling_edges(), return count
    }
}
```

### Task 4: Python — Merge Strategy Service

Create `python-api/app/services/merge_strategy.py`:

```python
class MergeStrategy(str, Enum):
    EXACT = "exact"
    KEEP_FIRST = "keep_first"
    KEEP_LAST = "keep_last"
    FIELD_OVERWRITE = "field_overwrite"
    LLM_BALANCED = "llm_balanced"
    LLM_PREFER_FIRST = "llm_prefer_first"
    LLM_PREFER_LAST = "llm_prefer_last"

    @property
    def is_deterministic(self) -> bool:
        return self in (MergeStrategy.EXACT, MergeStrategy.KEEP_FIRST,
                       MergeStrategy.KEEP_LAST, MergeStrategy.FIELD_OVERWRITE)

    @property
    def rust_strategy_name(self) -> str | None:
        mapping = {
            MergeStrategy.KEEP_FIRST: "keep_first",
            MergeStrategy.KEEP_LAST: "keep_last",
            MergeStrategy.FIELD_OVERWRITE: "field_overwrite",
        }
        return mapping.get(self)
```

### Task 5: Python — LLM Entity Merger

Create `python-api/app/services/entity_merger.py`:

```python
class EntityMerger:
    """Merge conflicting entities/edges using deterministic or LLM strategies."""

    def __init__(self, template: TemplateConfig):
        self.template = template

    async def merge(self, existing: dict, incoming: dict,
                    strategy: MergeStrategy, item_type: str = "node") -> dict:
        if strategy == MergeStrategy.EXACT:
            return existing  # Current behavior: keep existing, drop incoming
        if strategy == MergeStrategy.KEEP_FIRST:
            return existing
        if strategy == MergeStrategy.KEEP_LAST:
            return {**incoming, "id": existing.get("id")}  # Preserve ID
        if strategy == MergeStrategy.FIELD_OVERWRITE:
            return self._field_overwrite(existing, incoming)
        if strategy in (MergeStrategy.LLM_BALANCED, MergeStrategy.LLM_PREFER_FIRST, MergeStrategy.LLM_PREFER_LAST):
            return await self._llm_merge(existing, incoming, strategy, item_type)

    def _field_overwrite(self, existing: dict, incoming: dict) -> dict:
        merged = existing.copy()
        for key, value in incoming.items():
            if key == "id":
                continue  # Preserve canonical ID
            if value is not None and (key not in merged or merged[key] is None):
                merged[key] = value
            elif isinstance(value, list) and isinstance(merged.get(key), list):
                for item in value:
                    if item not in merged[key]:
                        merged[key].append(item)
        return merged

    async def _llm_merge(self, existing: dict, incoming: dict,
                         strategy: MergeStrategy, item_type: str) -> dict:
        """Call Ollama Cloud to reconcile field-level conflicts."""
        schema_fields = self._get_schema_fields(item_type)
        bias = "prefer the existing version" if strategy == MergeStrategy.LLM_PREFER_FIRST else \
               "prefer the incoming version" if strategy == MergeStrategy.LLM_PREFER_LAST else \
               "balance both versions equally"

        system_prompt = f"""You are an entity merge specialist. Reconcile two versions of the same {item_type}.
{bias}. For each conflicting field, produce a single merged value.

Output a JSON object with the merged {item_type} fields:
{json.dumps(schema_fields, indent=2)}"""

        user_prompt = f"""### Existing Version:
{json.dumps(existing, indent=2)}

### Incoming Version:
{json.dumps(incoming, indent=2)}

Produce the merged version:"""

        response = await call_ollama_cloud(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_format={"type": "json_object"},
        )
        merged = json.loads(response["content"])
        merged["id"] = existing.get("id")  # Preserve canonical ID
        return merged
```

### Task 6: Incremental Feed API Endpoint

In `python-api/app/routers/ingest.py`, add:

```python
@router.post("/collections/{collection_id}/feed")
async def feed_documents(
    collection_id: str,
    request: FeedRequest,
    user: dict = Depends(get_current_user),
):
    """Add new documents to an existing collection's graph (incremental merge)."""
    # Validate collection exists and user owns it
    # Create an ingest job with feed_mode=True
    # Return 202 with job_id
```

### Task 7: Feed Mode in Ingest Worker

In `python-api/app/pipeline/ingest_worker.py`, add:

```python
async def run_feed_pipeline(job_id: str, collection_id: str,
                             file_paths: List[str], template_key: str | None):
    """Incremental feeding: extract new docs, merge into existing graph."""
    # 1. Load template (if provided)
    template = TemplateGallery.get_instance().get(template_key) if template_key else None
    strategy_nodes = MergeStrategy(template.extraction.merge_strategy_nodes) if template else MergeStrategy.EXACT
    strategy_edges = MergeStrategy(template.extraction.merge_strategy_edges) if template else MergeStrategy.EXACT

    # 2. Process new files (extract → chunk → embed → NER → LLM extract)
    #    Reuse existing pipeline steps 2-8

    # 3. Resolve conflicts
    if strategy_nodes.is_deterministic:
        # Call Rust merge_nodes_into_collection
        im.merge_nodes_into_collection(collection_id, new_nodes_json, strategy_nodes.rust_strategy_name)
    else:
        # Detect conflicts via Rust, resolve each via LLM merger, then upsert
        conflicts = json.loads(im.detect_node_conflicts(collection_id, new_nodes_json))
        for conflict in conflicts:
            existing = _load_existing_node(conflict)
            incoming = _load_incoming_node(conflict)
            merged = await entity_merger.merge(existing, incoming, strategy_nodes, "node")
            im.update_node(collection_id, json.dumps(merged))

    # 4. Same for edges
    # 5. Prune dangling edges
    im.prune_dangling_edges(collection_id)

    # 6. Trigger incremental index rebuild
```

---

## Constraints

1. **Rust handles deterministic merge** (KeepFirst, KeepLast, FieldOverwrite). Python handles LLM merge.
2. **Canonical ID is always preserved** during merge. The merged entity keeps the existing node's UUID.
3. **All LLM merge calls use Ollama Cloud API** via `call_ollama_cloud()`.
4. **Feed mode does not rebuild the graph**. It merges new data into the existing graph using the template's merge strategy.
5. **Feed mode writes to LanceDB AND in-memory graph** (both layers must be consistent).
6. **Lock ordering** during merge: acquire L2 (outer HashMap) briefly → clone Arc → release L2 → lock L3 (per-collection graph) → merge → release L3.
7. **WAL must log feed operations** for crash recovery.

---

## Acceptance Criteria

1. `DeterministicMergeStrategy` enum in Rust with KeepFirst, KeepLast, FieldOverwrite.
2. `merge_nodes_deterministic()` preserves canonical ID, handles aliases, averages confidence.
3. `detect_node_conflicts()` returns list of field-level conflicts between existing and incoming nodes.
4. PyO3 methods: `detect_node_conflicts()`, `merge_nodes_into_collection()`, `merge_edges_into_collection()`, `prune_dangling_edges()`.
5. `MergeStrategy` enum in Python with 7 strategies, `is_deterministic` and `rust_strategy_name` properties.
6. `EntityMerger._llm_merge()` sends both versions to Ollama Cloud and returns merged result.
7. `POST /collections/{id}/feed` creates an ingest job with `feed_mode=True`.
8. Feed pipeline extracts new documents, detects conflicts, resolves using template merge strategy, and writes merged results to both LanceDB and in-memory graph.
9. Existing ingestion (non-feed) is not affected by merge strategy changes.
10. Feed operations logged to WAL for crash recovery.