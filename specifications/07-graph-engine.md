# 07 — Graph Engine

## 1. Graph Model

The knowledge graph is a **Directed Property Graph** where both nodes and edges carry typed
properties. It uses a two-layer storage architecture: a hot in-memory layer for fast traversal
and a cold persistent layer for durability and vector search.

### Node Types

| Node Type | Description | Key Properties |
|-----------|-------------|----------------|
| `Entity/Person` | A human individual | name, role, affiliation, confidence |
| `Entity/Organization` | A company, agency, NGO, or university | name, founded, headquarters |
| `Entity/Location` | A geographic place | name, country, coordinates |
| `Entity/Concept` | An abstract idea, technology, or product | name, description, domain |
| `Entity/Event` | A discrete occurrence in time | name, date, participants |
| `Document` | A source document | title, path, file_type, file_hash |
| `Chunk` | A text segment from a document | text, position, page, topics |
| `Topic` | A topic cluster centroid | name, keywords, frequency |

### Edge Types

| Edge Type | Domain | Range | Description |
|-----------|--------|-------|-------------|
| `MENTIONS` | Chunk | Entity (any) | A chunk mentions an entity |
| `RELATES_TO` | Entity | Entity | Generic semantic relationship |
| `WORKS_AT` | Person | Organization | Employment/affiliation |
| `FOUNDED` | Person | Organization | Founding relationship |
| `LOCATED_IN` | Org/Person/Event | Location | Geographic association |
| `PARTICIPATED_IN` | Person/Org | Event | Event participation |
| `BELONGS_TO_TOPIC` | Entity/Chunk | Topic | Topic assignment |
| `DERIVED_FROM` | Chunk | Document | Provenance relationship |
| `SIMILAR_TO` | Entity | Entity | Embedding similarity (symmetric) |
| `NEXT` | Chunk | Chunk | Sequential ordering within document |

---

## 2. Storage Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     Two-Layer Graph Storage                              │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │                    HOT LAYER (in-memory)                           │  │
│  │                                                                    │  │
│  │  Arc<RwLock<KnowledgeGraph>>                                      │  │
│  │  ┌─────────────────────────────────────────────────────────────┐  │  │
│  │  │  nodes: HashMap<Uuid, GraphNode>           O(1) lookup      │  │  │
│  │  │  edges: HashMap<Uuid, GraphEdge>           O(1) lookup      │  │  │
│  │  │  adjacency_out: HashMap<Uuid, Vec<(edge_id, target_id)>>    │  │  │
│  │  │  adjacency_in:  HashMap<Uuid, Vec<(edge_id, source_id)>>    │  │  │
│  │  │  version: AtomicU64   (cache invalidation)                  │  │  │
│  │  └─────────────────────────────────────────────────────────────┘  │  │
│  │  Fast: BFS O(V+E), Dijkstra O((V+E)logV), neighbor lookup O(degree)  │
│  └────────────────────────────────────────────────────────────────────┘  │
│              ↑ loaded at startup / updated on write                      │
│              │ write: update both layers atomically                      │
│              ↓                                                           │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │                    COLD LAYER (persistent)                         │  │
│  │                                                                    │  │
│  │  LanceDB Tables (Arrow columnar, IVF-PQ vector index)             │  │
│  │  ┌────────────────────────────────────────────────────────────┐   │  │
│  │  │  {collection_id}_nodes  — entity embeddings for ANN search │   │  │
│  │  │  {collection_id}_edges  — provenance, context, weights     │   │  │
│  │  │  {collection_id}_chunks — text chunks with embeddings      │   │  │
│  │  └────────────────────────────────────────────────────────────┘   │  │
│  │  Durable, MVCC, supports vector similarity queries on entities     │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

### Sync Protocol

- **On startup**: Load all rows from LanceDB `nodes` and `edges` tables into the in-memory
  `KnowledgeGraph`. This is done once per collection, loading into `HashMap` structures for O(1)
  lookup.
- **On write**: Update LanceDB FIRST (durable, MVCC). Then acquire a brief write lock on the
  in-memory `KnowledgeGraph` and update the adjacency lists.
- **On crash recovery**: Reload from LanceDB (single source of truth for persistence).

---

## 3. Entity Resolution

Entity resolution prevents graph fragmentation by merging duplicate entities extracted from
different documents.

### Resolution Algorithm (3-Step)

```
Incoming extracted entity: "Sam Altman" (Person/Executive)
        │
        ▼
Step 1: Exact name match (case-insensitive, Unicode normalized, whitespace collapsed)
  ┌─────────────────────────────────────────────────────────────────┐
  │ normalize("Sam Altman") == normalize("sam altman") → MATCH       │
  │ normalize("Sam Altman") == normalize("Samuel Altman") → NO MATCH │
  └─────────────────────────────────────────────────────────────────┘
        │ no match
        ▼
Step 2: Levenshtein distance < 3 AND same entity_type
  ┌─────────────────────────────────────────────────────────────────┐
  │ levenshtein("sam altman", "sam alman") = 1 → candidate         │
  │ levenshtein("sam altman", "elon musk") = 8 → not a candidate   │
  └─────────────────────────────────────────────────────────────────┘
        │ candidate found
        ▼
Step 3: Cosine similarity on embeddings > 0.92
  ┌─────────────────────────────────────────────────────────────────┐
  │ cos_sim(embed("Sam Altman CEO"), embed("Sam Alman")) = 0.96     │
  │ → MERGE                                                         │
  └─────────────────────────────────────────────────────────────────┘
        │ no merge
        ▼
Create new node
```

### Merge Strategy

When two entities are merged, the following rules apply:

```rust
pub fn merge_nodes(canonical: &mut GraphNode, incoming: &GraphNode) {
    // 1. Keep canonical ID (earliest insertion)
    // canonical.id unchanged

    // 2. Merge aliases
    canonical.aliases.extend(
        incoming.aliases.iter().chain(std::iter::once(&incoming.label))
            .filter(|a| !canonical.aliases.contains(a) && **a != canonical.label)
            .cloned()
    );

    // 3. Average confidence
    canonical.confidence = (canonical.confidence + incoming.confidence) / 2.0;

    // 4. Keep longer description
    if incoming.description.as_deref().map_or(0, |d| d.len())
        > canonical.description.as_deref().map_or(0, |d| d.len())
    {
        canonical.description = incoming.description.clone();
    }

    // 5. Merge properties (incoming wins on conflict)
    for (k, v) in &incoming.properties {
        canonical.properties.insert(k.clone(), v.clone());
    }

    // 6. Re-embed if aliases list has grown significantly (queued for batch re-embed)
    canonical.needs_reembed = true;
}
```

---

## 4. Graph Construction Flow

```
Python LLM extraction output (JSON via PyO3)
        │
        ▼
Rust: Deserialize into Vec<ExtractedEntity>, Vec<ExtractedRelationship>
        │
        ▼
Rust OntologyValidator.validate_batch()  [Rayon parallel]
  → valid_entities, valid_relationships
        │
        ▼
Rust EntityResolver.resolve_batch()
  For each entity:
    → Merge into existing node  (if match found)
    → Create new GraphNode       (if no match)
  → node_id_map: HashMap<entity_name, Uuid>
        │
        ▼
Rust: Build Vec<GraphEdge> from valid_relationships
  Lookup source/target UUIDs from node_id_map
        │
        ▼
Rust: batch_upsert to LanceDB nodes table (RecordBatch, atomic)
Rust: batch_upsert to LanceDB edges table (RecordBatch, atomic)
        │
        ▼
Rust: Acquire write lock on Arc<RwLock<KnowledgeGraph>>
  Insert nodes and edges into HashMaps
  Increment version counter
  Release write lock
        │
        ▼
Rust: Append to WAL (for crash recovery)
        │
        ▼
Rust: Invalidate graph_neighbor_cache
        │
        ▼
Return GraphWriteReport { added_nodes: N, added_edges: M }
```

---

## 5. Graph Operations

### 5.1 Get Node with Neighbors

```rust
pub async fn get_node_with_neighbors(
    &self,
    node_id: Uuid,
    collection_id: Uuid,
    depth: u32,
    edge_types: Option<Vec<EdgeType>>,
    topic_filter: Option<Vec<String>>,
) -> Result<NodeDetail> {
    let graph_arc = self.get_graph(&collection_id)?;
    let graph = graph_arc.read().await;

    let node = graph.nodes.get(&node_id)
        .ok_or(GraphError::NodeNotFound(node_id))?
        .clone();

    let subgraph = batched_bfs(
        &graph,
        vec![node_id],
        depth,
        MAX_DEGREE_DEFAULT,
        MIN_EDGE_WEIGHT_DEFAULT,
    );

    Ok(NodeDetail { node, subgraph })
}
```

### 5.2 Path Finding (Dijkstra)

```rust
pub async fn find_path(
    &self,
    from_id: Uuid,
    to_id: Uuid,
    collection_id: Uuid,
    max_depth: u32,
) -> Result<Option<Vec<PathStep>>> {
    let graph_arc = self.get_graph(&collection_id)?;
    let graph = graph_arc.read().await;

    // Dijkstra: edge cost = 1.0 / edge.weight (higher weight = lower cost = preferred)
    use std::collections::BinaryHeap;
    use std::cmp::Reverse;

    let mut dist: HashMap<Uuid, f32> = HashMap::new();
    let mut prev: HashMap<Uuid, (Uuid, Uuid)> = HashMap::new(); // node_id → (prev_node_id, edge_id)
    let mut heap: BinaryHeap<Reverse<(ordered_float::OrderedFloat<f32>, Uuid)>> = BinaryHeap::new();

    dist.insert(from_id, 0.0);
    heap.push(Reverse((ordered_float::OrderedFloat(0.0), from_id)));

    while let Some(Reverse((cost, u))) = heap.pop() {
        if u == to_id {
            return Ok(Some(reconstruct_path(&prev, from_id, to_id, &graph)));
        }
        if cost.0 > *dist.get(&u).unwrap_or(&f32::INFINITY) { continue; }
        if let Some(neighbors) = graph.adjacency_out.get(&u) {
            for &(edge_id, v) in neighbors {
                let edge_cost = graph.edges.get(&edge_id)
                    .map(|e| 1.0 / e.weight.max(0.001))
                    .unwrap_or(1.0);
                let new_cost = cost.0 + edge_cost;
                if new_cost < *dist.get(&v).unwrap_or(&f32::INFINITY) {
                    dist.insert(v, new_cost);
                    prev.insert(v, (u, edge_id));
                    heap.push(Reverse((ordered_float::OrderedFloat(new_cost), v)));
                }
            }
        }
    }

    Ok(None) // No path found
}
```

### 5.3 Topic Subgraph

```rust
pub async fn topic_subgraph(
    &self,
    topics: Vec<String>,
    collection_id: Uuid,
    max_nodes: usize,
) -> Result<SubGraph> {
    let graph_arc = self.get_graph(&collection_id)?;
    let graph = graph_arc.read().await;

    // Find all entities assigned to these topics via BELONGS_TO_TOPIC edges
    let topic_node_ids: Vec<Uuid> = graph.edges.values()
        .filter(|e| matches!(e.edge_type, EdgeType::BelongsToTopic))
        .filter(|e| {
            graph.nodes.get(&e.target)
                .map(|n| n.node_type == NodeType::Topic && topics.contains(&n.label))
                .unwrap_or(false)
        })
        .map(|e| e.source)
        .collect::<std::collections::HashSet<_>>()
        .into_iter()
        .take(max_nodes)
        .collect();

    Ok(batched_bfs(&graph, topic_node_ids, 1, 50, 0.2))
}
```

### 5.4 Export Operations

```rust
pub async fn export_graphml(&self, collection_id: Uuid) -> Result<String> {
    let graph_arc = self.get_graph(&collection_id)?;
    let graph = graph_arc.read().await;

    let mut xml = String::from(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<graphml xmlns="http://graphml.graphdrawing.org/graphml">
  <graph id="G" edgedefault="directed">
"#
    );

    for (id, node) in &graph.nodes {
        xml.push_str(&format!(
            "    <node id=\"{}\">\n      <data key=\"label\">{}</data>\n      <data key=\"type\">{:?}</data>\n    </node>\n",
            id, xml_escape(&node.label), node.node_type
        ));
    }
    for (id, edge) in &graph.edges {
        xml.push_str(&format!(
            "    <edge id=\"{}\" source=\"{}\" target=\"{}\">\n      <data key=\"predicate\">{:?}</data>\n      <data key=\"weight\">{}</data>\n    </edge>\n",
            id, edge.source, edge.target, edge.edge_type, edge.weight
        ));
    }

    xml.push_str("  </graph>\n</graphml>");
    Ok(xml)
}

pub async fn export_json(&self, collection_id: Uuid) -> Result<serde_json::Value> {
    let graph_arc = self.get_graph(&collection_id)?;
    let graph = graph_arc.read().await;

    Ok(serde_json::json!({
        "nodes": graph.nodes.values().collect::<Vec<_>>(),
        "edges": graph.edges.values().collect::<Vec<_>>(),
        "stats": {
            "node_count": graph.node_count(),
            "edge_count": graph.edge_count(),
            "version": graph.version.load(std::sync::atomic::Ordering::Relaxed),
        }
    }))
}
```

---

## 6. Graph Pruning

Graph pruning runs as a background task (hourly) to keep the graph clean and performant.

```rust
pub async fn prune_graph(
    &self,
    collection_id: Uuid,
    config: PruningConfig,
) -> Result<PruningReport> {
    let graph_arc = self.get_graph(&collection_id)?;

    // Collect prune candidates (read lock — concurrent reads OK)
    let (edges_to_remove, nodes_to_tombstone) = {
        let graph = graph_arc.read().await;

        // 1. Remove edges below weight threshold
        let low_weight_edges: Vec<Uuid> = graph.edges.values()
            .filter(|e| e.weight < config.min_edge_weight)
            .map(|e| e.id)
            .collect();

        // 2. Enforce max out-degree per node (keep highest weight edges)
        let mut excess_edges: Vec<Uuid> = Vec::new();
        for (node_id, out_edges) in &graph.adjacency_out {
            if out_edges.len() > config.max_out_degree {
                let mut sorted: Vec<_> = out_edges.iter()
                    .filter_map(|(edge_id, _)| graph.edges.get(edge_id).map(|e| (e.weight, *edge_id)))
                    .collect();
                sorted.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap()); // descending weight
                excess_edges.extend(sorted[config.max_out_degree..].iter().map(|(_, id)| id));
            }
        }

        // 3. Tombstone orphaned nodes (no edges at all)
        let connected_nodes: std::collections::HashSet<Uuid> = graph.edges.values()
            .flat_map(|e| [e.source, e.target])
            .collect();
        let orphaned: Vec<Uuid> = graph.nodes.keys()
            .filter(|id| !connected_nodes.contains(id))
            .copied()
            .collect();

        let all_edge_removals: Vec<Uuid> = low_weight_edges.into_iter()
            .chain(excess_edges)
            .collect::<std::collections::HashSet<_>>()
            .into_iter()
            .collect();

        (all_edge_removals, orphaned)
    }; // read lock released

    if edges_to_remove.is_empty() && nodes_to_tombstone.is_empty() {
        return Ok(PruningReport::default());
    }

    // Apply removals (write lock — brief)
    {
        let mut graph = graph_arc.write().await;
        for edge_id in &edges_to_remove {
            if let Some(edge) = graph.edges.remove(edge_id) {
                if let Some(out_adj) = graph.adjacency_out.get_mut(&edge.source) {
                    out_adj.retain(|(eid, _)| eid != edge_id);
                }
                if let Some(in_adj) = graph.adjacency_in.get_mut(&edge.target) {
                    in_adj.retain(|(eid, _)| eid != edge_id);
                }
            }
        }
        for node_id in &nodes_to_tombstone {
            graph.nodes.remove(node_id);
        }
        graph.version.fetch_add(1, std::sync::atomic::Ordering::Release);
    }

    // Persist removals to LanceDB
    self.delete_edges_from_lancedb(&collection_id, &edges_to_remove).await?;
    self.tombstone_nodes_in_lancedb(&collection_id, &nodes_to_tombstone).await?;

    Ok(PruningReport {
        removed_edges: edges_to_remove.len(),
        tombstoned_nodes: nodes_to_tombstone.len(),
    })
}

pub struct PruningConfig {
    pub min_edge_weight: f32,     // default: 0.3
    pub max_out_degree: usize,    // default: 100
}
```

---

## 7. Human-in-the-Loop Graph Editing

Users can manually correct, create, or delete entities and relationships. All changes are:
1. Applied immediately to both LanceDB and in-memory graph
2. Recorded in `user_feedback` PostgreSQL table for audit and future LLM prompt enrichment

### Manual Node Edit

```rust
pub async fn update_node(
    &self,
    node_id: Uuid,
    update: NodeUpdate,
    collection_id: Uuid,
    user_id: Uuid,
    pg_pool: &PgPool,
) -> Result<GraphNode> {
    // Get previous state (for feedback record)
    let previous = {
        let graph_arc = self.get_graph(&collection_id)?;
        let graph = graph_arc.read().await;
        graph.nodes.get(&node_id).cloned().ok_or(GraphError::NodeNotFound(node_id))?
    };

    // Build updated node
    let updated = GraphNode {
        label: update.label.unwrap_or(previous.label.clone()),
        description: update.description.or(previous.description.clone()),
        aliases: update.aliases.unwrap_or(previous.aliases.clone()),
        confidence: update.confidence.unwrap_or(previous.confidence),
        ..previous.clone()
    };

    // Persist to LanceDB
    self.upsert_node_to_lancedb(&collection_id, &updated).await?;

    // Update in-memory graph (brief write lock)
    {
        let graph_arc = self.get_graph(&collection_id)?;
        let mut graph = graph_arc.write().await;
        graph.nodes.insert(node_id, updated.clone());
        graph.version.fetch_add(1, std::sync::atomic::Ordering::Release);
    }

    // Record feedback
    sqlx::query!(
        "INSERT INTO user_feedback (user_id, collection_id, entity_id, action, previous_value, new_value)
         VALUES ($1, $2, $3, 'edit', $4, $5)",
        user_id, collection_id, node_id,
        serde_json::to_value(&previous)?,
        serde_json::to_value(&updated)?,
    )
    .execute(pg_pool)
    .await?;

    Ok(updated)
}
```

### Manual Edge Delete (Negative Signal)

Deleted edges are stored as negative feedback. Future LLM extraction prompts can include recent
negative feedback to prevent re-hallucinating the same incorrect edges.

```rust
pub async fn delete_edge(
    &self,
    edge_id: Uuid,
    collection_id: Uuid,
    user_id: Uuid,
    pg_pool: &PgPool,
) -> Result<()> {
    // Get edge (for feedback record)
    let edge = {
        let graph_arc = self.get_graph(&collection_id)?;
        let graph = graph_arc.read().await;
        graph.edges.get(&edge_id).cloned().ok_or(GraphError::EdgeNotFound(edge_id))?
    };

    // Remove from LanceDB (tombstone, or hard delete)
    self.delete_edge_from_lancedb(&collection_id, edge_id).await?;

    // Remove from in-memory graph
    {
        let graph_arc = self.get_graph(&collection_id)?;
        let mut graph = graph_arc.write().await;
        if let Some(e) = graph.edges.remove(&edge_id) {
            graph.adjacency_out.get_mut(&e.source).map(|v| v.retain(|(id, _)| id != &edge_id));
            graph.adjacency_in.get_mut(&e.target).map(|v| v.retain(|(id, _)| id != &edge_id));
        }
        graph.version.fetch_add(1, std::sync::atomic::Ordering::Release);
    }

    // Record as negative feedback
    sqlx::query!(
        "INSERT INTO user_feedback (user_id, collection_id, edge_id, action, previous_value)
         VALUES ($1, $2, $3, 'reject', $4)",
        user_id, collection_id, edge_id, serde_json::to_value(&edge)?
    )
    .execute(pg_pool)
    .await?;

    Ok(())
}
```

---

## 8. Memory Estimation

For capacity planning, the in-memory graph footprint is estimated as follows:

| Structure | Per Element Size | 100K nodes | 500K edges |
|-----------|-----------------|------------|------------|
| `HashMap<Uuid, GraphNode>` | ~400 bytes | ~40 MB | — |
| `HashMap<Uuid, GraphEdge>` | ~200 bytes | — | ~100 MB |
| `adjacency_out` + `adjacency_in` | ~40 bytes/edge | — | ~40 MB |
| **Total** | | | **~180 MB** |

This comfortably fits within a 2 GB memory budget for 100K nodes + 500K edges per collection.
Multiple collections each have their own `KnowledgeGraph` instance.
