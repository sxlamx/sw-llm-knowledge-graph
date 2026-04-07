# Bot 3 — Test: Phase 3 — Knowledge Graph Engine

## Your Role

You are a QA engineer writing tests for the knowledge graph engine covering ontology validation,
entity resolution, graph construction, traversal, and API endpoints.

---

## Test Frameworks

- **Rust**: `cargo test` (inline + `tests/` dir)
- **Python**: `pytest` + `pytest-asyncio`
- **Coverage**: cargo tarpaulin (Rust) + pytest-cov (Python)

---

## Test File Locations

```
rust-core/
  src/
    ontology/validator.rs   ← #[cfg(test)] validator edge cases
    graph/builder.rs        ← #[cfg(test)] entity resolution tests
    graph/traversal.rs      ← #[cfg(test)] BFS/Dijkstra tests
  tests/
    integration_graph.rs    ← LanceDB + petgraph full flow

python-api/tests/
  test_graph_api.py         ← REST endpoint tests
  test_build_graph_ner.py   ← NER→graph construction tests
  test_ontology.py          ← ontology CRUD tests
```

---

## Critical Test Cases

### Rust: Entity Resolution (`src/graph/builder.rs`)

```rust
#[test]
fn test_exact_match_case_insensitive() {
    let resolver = EntityResolver::default();
    let existing = vec![GraphNode { label: "Apple Inc".to_string(), ... }];
    let candidate = ExtractedEntity { name: "apple inc".to_string(), ... };
    let resolution = resolver.resolve(&candidate, &existing, &[]);
    assert!(matches!(resolution, Resolution::Merge { strategy: MergeStrategy::ExactMatch, .. }));
}

#[test]
fn test_no_merge_below_cosine_threshold() {
    // Levenshtein < 3 but cosine similarity < 0.92 → NewNode
    let resolver = EntityResolver { embedding_threshold: 0.92, levenshtein_threshold: 3 };
    // candidate embedding is dissimilar → should NOT merge
    assert!(matches!(resolver.resolve(...), Resolution::NewNode));
}

#[test]
fn test_merge_strategy_unions_aliases() {
    // Merge candidate with aliases=["OpenAI Inc"] into existing with aliases=["OAI"]
    // Result: aliases contains both
}
```

### Rust: BFS Traversal (`src/graph/traversal.rs`)

```rust
#[test]
fn test_bfs_includes_seed_node() {
    // Create graph with node A → B → C
    let reachable = bfs_reachable(&graph, node_a_id, 2, 0.0);
    assert!(reachable.contains(&node_a_id), "Seed node must be in result");
}

#[test]
fn test_bfs_respects_max_hops() {
    // A → B → C → D; max_hops=1 → only A, B
    let reachable = bfs_reachable(&graph, node_a_id, 1, 0.0);
    assert!(!reachable.contains(&node_c_id));
    assert!(!reachable.contains(&node_d_id));
}

#[test]
fn test_bfs_prunes_low_weight_edges() {
    // Edge A→B weight=0.1, min_weight=0.5 → B not reachable
    let reachable = bfs_reachable(&graph, node_a_id, 3, 0.5);
    assert!(!reachable.contains(&node_b_id));
}

#[test]
fn test_dijkstra_returns_path_step_alternating() {
    // Path A→B→C; result should be [Node(A), Edge(AB), Node(B), Edge(BC), Node(C)]
    let path = find_shortest_path(&graph, a_id, c_id);
    assert_eq!(path.len(), 5);
    assert!(matches!(path[0], PathStep::Node(_)));
    assert!(matches!(path[1], PathStep::Edge(_)));
    assert!(matches!(path[2], PathStep::Node(_)));
}

#[test]
fn test_dijkstra_returns_empty_for_disconnected_nodes() {
    let path = find_shortest_path(&graph, a_id, isolated_id);
    assert!(path.is_empty());  // NOT panic
}

#[test]
fn test_dijkstra_prefers_higher_weight_edges() {
    // Path 1: A→B (weight=0.5) → B→C (weight=0.5), cost=4
    // Path 2: A→D (weight=0.9) → D→C (weight=0.9), cost=2.2
    // Dijkstra should return Path 2 (lower cost)
}
```

### Rust: Ontology Validator

```rust
#[test]
fn test_drops_unknown_entity_type() {
    let ontology = default_ontology();
    let entity = ExtractedEntity { entity_type: "FICTIONAL_TYPE".to_string(), confidence: 0.9, ... };
    let report = validate_extraction_result(&ExtractionResult { entities: vec![entity], ... }, &ontology);
    assert_eq!(report.valid_entities.len(), 0);
    assert_eq!(report.dropped_entities.len(), 1);
}

#[test]
fn test_drops_low_confidence_entity() {
    let entity = ExtractedEntity { entity_type: "PERSON", confidence: 0.2, ... }; // below 0.3
    let report = validate_extraction_result(...);
    assert_eq!(report.valid_entities.len(), 0);
}

#[test]
fn test_accepts_valid_entity() {
    let entity = ExtractedEntity { entity_type: "ORGANIZATION", confidence: 0.8, ... };
    let report = validate_extraction_result(...);
    assert_eq!(report.valid_entities.len(), 1);
}
```

### Python: Graph API (`test_graph_api.py`)

```python
@pytest.mark.asyncio
async def test_get_subgraph_includes_seed(authed_client, graph_with_nodes):
    node_id, collection_id = graph_with_nodes
    resp = await authed_client.get(
        f"/api/v1/graph/subgraph?collection_id={collection_id}&node_id={node_id}&depth=1")
    assert resp.status_code == 200
    node_ids = [n["id"] for n in resp.json()["nodes"]]
    assert node_id in node_ids  # seed included

@pytest.mark.asyncio
async def test_node_summary_never_returns_502(authed_client, graph_with_nodes, monkeypatch):
    """Even when Ollama is unavailable, should return 200 with fallback summary."""
    monkeypatch.setattr("app.config.Settings.ollama_cloud_base_url", "")
    node_id, collection_id = graph_with_nodes
    resp = await authed_client.get(f"/api/v1/graph/nodes/{node_id}/summary?collection_id={collection_id}")
    assert resp.status_code == 200
    assert "summary" in resp.json()
    assert len(resp.json()["summary"]) > 0

@pytest.mark.asyncio
async def test_node_entity_type_is_canonical(authed_client, graph_with_nodes):
    """Entity type in response uses canonical names, not spaCy shorthand."""
    resp = await authed_client.get(f"/api/v1/graph/nodes/{node_id}?collection_id={collection_id}")
    entity_type = resp.json()["entity_type"]
    assert entity_type not in ("ORG", "GPE", "LOC", "NORP", "FAC")
    assert entity_type in ("ORGANIZATION", "LOCATION", "PERSON", "DATE", "LAW", "MONEY", ...)
```

### Python: NER→Graph Construction (`test_build_graph_ner.py`)

```python
@pytest.mark.asyncio
async def test_build_graph_creates_nodes_with_canonical_types(ingested_collection):
    result = await build_graph_from_ner(ingested_collection)
    nodes = get_all_nodes(ingested_collection)
    for node in nodes:
        assert node["entity_type"] not in ("ORG", "GPE", "LOC"), \
            f"Non-canonical entity_type found: {node['entity_type']}"

@pytest.mark.asyncio
async def test_build_graph_creates_co_occurrence_edges(ingested_collection):
    result = await build_graph_from_ner(ingested_collection)
    assert result["added_edges"] > 0

@pytest.mark.asyncio
async def test_build_graph_merges_duplicate_entities(ingested_collection_with_duplicates):
    result = await build_graph_from_ner(ingested_collection_with_duplicates)
    # Should merge duplicate entity mentions into single node
    assert result["merged_nodes"] > 0
```

---

## Mock Patterns

- Build in-memory petgraph for Rust unit tests using helper functions
- Use `tempfile::tempdir()` for LanceDB paths
- Mock Ollama HTTP calls with `respx` in Python tests
