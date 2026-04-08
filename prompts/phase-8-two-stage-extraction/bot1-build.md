# Bot 1 — Build: Phase 8 (Two-Stage Extraction)

> **Feature**: F2 (Two-Stage Extraction — nodes first, then edges with entity context)
> **Spec References**: `15-hyper-extract-integration.md` Section 2.2
> **Depends On**: Phase 7 (templates and identifiers must exist)

---

## Role

You are a senior engineer implementing the two-stage extraction pipeline for the sw-llm-knowledge-graph project. This is the most impactful quality improvement: extracting entities first, then feeding the known-entity list into the edge extraction prompt drastically reduces hallucinated edges.

---

## Project Context

The existing `python-api/app/llm/extractor.py` has a single `extract_from_chunk()` function that extracts entities and relationships in one LLM call. This phase adds a two-stage mode where:

1. **Stage 1**: LLM extracts entities only (using template's `entity_schema`)
2. **Stage 2**: LLM extracts relationships only (using template's `relation_schema` + known entity context)

**All LLM calls use Ollama Cloud API** (`settings.ollama_cloud_base_url`). No local Ollama, no OpenAI.

---

## LESSONS.md Rules

- NER model: always `en_core_web_trf`, never `en_core_web_sm`
- Storage: LanceDB for all metadata, no PostgreSQL
- Embeddings: `Qwen/Qwen3-Embedding-0.6B`, 1024-dim
- NER labels: canonical only (`ORGANIZATION` not `ORG`)
- Contextual prefix: gated behind `settings.enable_contextual_prefix`

---

## Implementation Tasks

### Task 1: Two-Stage Extraction Service

Create `python-api/app/llm/two_stage_extractor.py`:

```python
class TwoStageExtractor:
    """Two-stage LLM extraction: entities first, then edges with entity context."""

    def __init__(self, template: TemplateConfig):
        self.template = template
        self.entity_schema = self._build_entity_pydantic_model(template)
        self.relation_schema = self._build_relation_pydantic_model(template)

    async def extract_entities(self, chunk_text: str) -> List[dict]:
        """Stage 1: Extract entities from text using Ollama Cloud."""
        system_prompt = self._build_entity_system_prompt()
        user_prompt = f"Extract entities from the following text.\n\n### Source Text:\n{chunk_text}"

        response = await call_ollama_cloud(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_format={"type": "json_object"},
        )
        # Parse structured output using entity_schema Pydantic model
        return self._parse_entity_response(response)

    async def extract_relations(self, chunk_text: str, known_entities: List[dict]) -> List[dict]:
        """Stage 2: Extract relations with known entity context."""
        system_prompt = self._build_edge_system_prompt()
        known_nodes_str = self._format_known_entities(known_entities)
        user_prompt = (
            f"Extract relationships between the following known entities.\n\n"
            f"# Known Entities\n{known_nodes_str}\n\n"
            f"### Source Text:\n{chunk_text}"
        )

        response = await call_ollama_cloud(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_format={"type": "json_object"},
        )
        return self._parse_relation_response(response)

    async def extract_two_stage(self, chunk_text: str) -> Tuple[List[dict], List[dict]]:
        """Full two-stage extraction: entities then relations."""
        entities = await self.extract_entities(chunk_text)
        if not entities:
            return entities, []
        relations = await self.extract_relations(chunk_text, entities)
        return entities, relations
```

### Task 2: Dynamic Pydantic Model Builder

In `python-api/app/services/template_factory.py`, add:

```python
def build_entity_pydantic_model(template: TemplateConfig) -> Type[BaseModel]:
    """Build a dynamic Pydantic model from the template's entity schema.
    Used for structured LLM output validation."""
    fields = {}
    for f in template.entity_schema.fields:
        field_type = _FIELD_TYPE_MAP[f.type]  # string→str, integer→int, etc.
        if not f.required:
            field_type = Optional[field_type]
        fields[f.name] = (field_type, Field(description=f.description))
    return create_model(f"{template.name}_Entity", **fields)

def build_relation_pydantic_model(template: TemplateConfig) -> Type[BaseModel]:
    """Build a dynamic Pydantic model from the template's relation schema."""
    fields = {}
    for f in template.relation_schema.fields:
        field_type = _FIELD_TYPE_MAP[f.type]
        if f.type == FieldType.LIST:
            field_type = List[str]  # lists are always List[str] for extraction
        if not f.required:
            field_type = Optional[field_type]
        fields[f.name] = (field_type, Field(description=f.description))
    return create_model(f"{template.name}_Relation", **fields)

def build_entity_list_model(entity_model: Type[BaseModel]) -> Type[BaseModel]:
    """Build a list wrapper: { items: [EntityModel] }"""
    return create_model(f"{entity_model.__name__}List",
                        items=(List[entity_model], Field(default_factory=list)))

def build_relation_list_model(relation_model: Type[BaseModel]) -> Type[BaseModel]:
    """Build a list wrapper: { items: [RelationModel] }"""
    return create_model(f"{relation_model.__name__}List",
                        items=(List[relation_model], Field(default_factory=list)))
```

### Task 3: Prompt Construction from Template

In `python-api/app/llm/two_stage_extractor.py`:

```python
def _build_entity_system_prompt(self) -> str:
    """Build entity extraction prompt from template config."""
    parts = [
        "You are an expert entity extraction specialist.",
        f"Your task is to extract all important entities from the text.",
    ]
    # Add domain-specific context from template
    if self.template.extraction.node_prompt_extra:
        parts.append(f"\n### Context & Instructions:\n{self.template.extraction.node_prompt_extra}")
    # Add field descriptions from entity_schema
    parts.append("\n### Output Format:")
    for field in self.template.entity_schema.fields:
        req = "required" if field.required else "optional"
        parts.append(f"- {field.name} ({field.type}, {req}): {field.description}")
    return "\n".join(parts)

def _build_edge_system_prompt(self) -> str:
    """Build edge extraction prompt with entity context rules."""
    parts = [
        "You are an expert relationship extraction specialist.",
        "Extract meaningful relationships between the provided entities.",
    ]
    if self.template.extraction.edge_prompt_extra:
        parts.append(f"\n### Context & Instructions:\n{self.template.extraction.edge_prompt_extra}")
    parts.append("\n### CRITICAL RULES:")
    parts.append("1. ONLY extract relationships connecting entities from the known entity list.")
    parts.append("2. DO NOT create relationships involving entities that are not listed.")
    parts.append("3. If an entity is not in the known list, exclude it from the relationship.")
    # Add field descriptions from relation_schema
    parts.append("\n### Output Format:")
    for field in self.template.relation_schema.fields:
        req = "required" if field.required else "optional"
        parts.append(f"- {field.name} ({field.type}, {req}): {field.description}")
    return "\n".join(parts)

def _format_known_entities(self, entities: List[dict]) -> str:
    """Format entity list for edge extraction prompt."""
    if not entities:
        return "No entities identified."
    lines = []
    for i, e in enumerate(entities, 1):
        name = e.get("name", e.get("label", f"entity_{i}"))
        etype = e.get("entity_type", e.get("type", "unknown"))
        lines.append(f"{i}. {name} ({etype})")
    return "\n".join(lines)
```

### Task 4: Integrating Two-Stage into Ingest Worker

Modify `python-api/app/pipeline/ingest_worker.py`:

```python
async def _extract_graph_with_template(chunks: List[dict], template: TemplateConfig) -> Tuple[List[dict], List[dict]]:
    """Extract entities and relations using template-driven two-stage extraction."""
    extractor = TwoStageExtractor(template)
    all_entities = []
    all_relations = []

    for chunk in chunks:
        if template.extraction.mode == "two_stage":
            entities, relations = await extractor.extract_two_stage(chunk["text"])
        else:
            # One-stage: extract both in a single call (fallback)
            entities, relations = await _extract_one_stage(extractor, chunk["text"])
        all_entities.extend(entities)
        all_relations.extend(relations)

    # Compute dedup keys from template identifiers
    entity_key_fn = TemplateFactory._compile_key_pattern(template.entity_schema.key)
    relation_key_fn = TemplateFactory._compile_key_pattern(template.relation_schema.key)

    for e in all_entities:
        e["_dedup_key"] = entity_key_fn(e)
    for r in all_relations:
        r["_dedup_key"] = relation_key_fn(r)

    return all_entities, all_relations
```

### Task 5: Dangling Edge Pruning

Create `python-api/app/llm/edge_pruner.py`:

```python
class EdgePruner:
    """Prune edges that reference non-existent entities (dangling edges)."""

    @staticmethod
    def prune_dangling_binary(edges: List[dict], entity_keys: set) -> List[dict]:
        """Prune binary edges whose source or target is not in the entity key set."""
        valid = []
        pruned_count = 0
        for edge in edges:
            source = edge.get("source", "")
            target = edge.get("target", "")
            if source in entity_keys and target in entity_keys:
                valid.append(edge)
            else:
                pruned_count += 1
        if pruned_count > 0:
            logger.info(f"Pruned {pruned_count} dangling edges (binary)")
        return valid

    @staticmethod
    def prune_dangling_hyperedges(edges: List[dict], entity_keys: set,
                                   participants_field: str = "participants") -> List[dict]:
        """Prune hyperedges where ANY participant is not in the entity key set."""
        valid = []
        pruned_count = 0
        for edge in edges:
            participants = edge.get(participants_field, [])
            if isinstance(participants, str):
                participants = [participants]
            if all(p in entity_keys for p in participants) and len(participants) > 0:
                valid.append(edge)
            else:
                pruned_count += 1
        if pruned_count > 0:
            logger.info(f"Pruned {pruned_count} dangling hyperedges")
        return valid

    @staticmethod
    def prune(edges: List[dict], entity_keys: set, template: TemplateConfig) -> List[dict]:
        """Auto-detect edge type and prune accordingly."""
        if template.type == "hypergraph":
            participants_field = template.relation_schema.participants_field or "participants"
            return EdgePruner.prune_dangling_hyperedges(edges, entity_keys, participants_field)
        return EdgePruner.prune_dangling_binary(edges, entity_keys)
```

Also add a Rust-side pruning method that can be called via PyO3 for large graphs:

In `rust-core/src/graph/builder.rs`, add:

```rust
pub fn prune_dangling_edges(graph: &mut KnowledgeGraph) -> usize {
    let valid_node_ids: HashSet<Uuid> = graph.nodes.keys().copied().collect();
    let dangling: Vec<Uuid> = graph.edges.iter()
        .filter(|(_, edge)| {
            if let Some(participants) = &edge.participants {
                participants.iter().any(|p| !valid_node_ids.contains(p))
            } else {
                !valid_node_ids.contains(&edge.source) || !valid_node_ids.contains(&edge.target)
            }
        })
        .map(|(id, _)| *id)
        .collect();
    let count = dangling.len();
    for id in &dangling { graph.edges.remove(id); }
    graph.rebuild_adjacency();
    count
}
```

### Task 6: Ollama Cloud API Call Helper

Create `python-api/app/llm/ollama_client.py`:

```python
import httpx
from app.config import settings

async def call_ollama_cloud(
    system_prompt: str,
    user_prompt: str,
    response_format: dict | None = None,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> dict:
    """Call Ollama Cloud API (OpenAI-compatible).
    All LLM features must use this function — no direct httpx calls elsewhere."""
    model = model or settings.ollama_cloud_model
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        payload["response_format"] = response_format

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{settings.ollama_cloud_base_url}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {settings.ollama_cloud_api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()

    content = data["choices"][0]["message"]["content"]
    # Strip markdown code fences if present
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0]
    return {"content": content.strip(), "usage": data.get("usage", {})}
```

### Task 7: Update Extractor to Use Ollama Cloud Client

Modify `python-api/app/llm/extractor.py` to use `call_ollama_cloud()` from `ollama_client.py` instead of making direct `httpx` calls. This consolidates all LLM API calls into one place.

---

## Constraints

1. **All LLM calls use `call_ollama_cloud()`** from `ollama_client.py`. No direct `httpx` calls to Ollama Cloud elsewhere.
2. **Two-stage mode is template-driven**. Only activates when a template with `extraction.mode: two_stage` is provided.
3. **Existing one-stage extraction must not break**. When no template is provided, the existing `extract_from_chunk()` code path runs unchanged.
4. **Dangling edge pruning runs after every extraction**. This is not optional — every two-stage extraction must prune dangling edges.
5. **Entity context in edge extraction** uses the template's `entity_schema.key` to build the known-entity list, not just the `name` field.
6. **Ollama Cloud API key** is read from `settings.ollama_cloud_api_key`. If not set, extraction should raise a clear error, not silently fail.
7. **Cost tracking**: All LLM calls must go through `cost_tracker.create_tracker()` for per-job budget caps.

---

## Acceptance Criteria

1. `TwoStageExtractor` extracts entities (Stage 1) and relations (Stage 2) from text using template-defined schemas.
2. Stage 2 edge extraction prompt includes the known-entity list from Stage 1.
3. Dynamic Pydantic models built from template schemas produce valid structured output.
4. `EdgePruner.prune()` removes edges whose source/target/participants don't exist in the entity set.
5. Rust `prune_dangling_edges()` correctly handles both binary edges and hyperedges.
6. `call_ollama_cloud()` is the single point of LLM API access — no other module makes direct Ollama Cloud calls.
7. Ingest worker routes to two-stage or one-stage extraction based on template `extraction.mode`.
8. Backward compatibility: existing `extract_from_chunk()` works when no template is provided.
9. All LLM calls tracked by `cost_tracker` per job.
10. `ollama_client.py` cleanly handles API errors (401, 429, 500) with retry logic.