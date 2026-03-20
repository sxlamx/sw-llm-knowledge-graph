# 06 — Search Engine

## 1. Overview

The search engine provides hybrid retrieval that combines three complementary search strategies:
vector similarity, BM25 keyword matching, and graph proximity. The three searches run concurrently
via Tokio, and their scores are fused into a single ranked result list.

**Design goals:**
- P50 latency < 200ms, P95 latency < 800ms
- Support 100 concurrent searches (enforced by `IndexManager.search_semaphore`)
- Non-blocking: searches never wait for index writes
- Graceful degradation: if one channel fails, return partial results from the others

---

## 2. Search Pipeline

```
HTTP POST /api/v1/search
        │
        ▼
FastAPI handler → PyO3 bridge → Rust SearchEngine::hybrid_search()
        │
        ▼
1. Acquire search_semaphore permit (max 100 concurrent; blocks if at capacity)
        │
        ▼
2. Check query_embedding_cache (LRU, key=SHA256(query_text))
   HIT  ──────────────────────────────────────────────► skip to step 4
   MISS ─► embed query via OpenAI API (Python → passes embedding to Rust)
        │
        ▼
3. Store embedding in query_embedding_cache
        │
        ▼
4. Apply pre-filters: topic filter, collection_id filter
        │
        ▼
5. Launch three concurrent search channels (tokio::join!)
   ┌────────────────┬────────────────┬────────────────────┐
   │  Vector Search │ Keyword Search │  Graph Proximity   │
   │  (LanceDB ANN) │ (Tantivy BM25) │  (BFS from seeds)  │
   └───────┬────────┴───────┬────────┴────────┬───────────┘
           │                │                 │
           └────────────────┴─────────────────┘
                            │
                            ▼
6. Score fusion:
   final_score = (vector_sim × 0.6) + (keyword_score × 0.3) + (graph_proximity × 0.1)
        │
        ▼
7. Deduplicate by chunk_id (keep highest final_score)
        │
        ▼
8. Optional: re-ranking via cross-encoder (if enabled)
        │
        ▼
9. Paginate and return top-K results
        │
        ▼
release search_semaphore permit
```

---

## 3. Vector Search Channel

```rust
// rust-core/src/search_engine.rs

pub async fn vector_search_channel(
    index_manager: &IndexManager,
    collection_id: &Uuid,
    embedding: &[f32],
    limit: usize,
    topic_filter: Option<&[String]>,
) -> Result<Vec<VectorSearchResult>> {
    // Get table handle — hold outer RwLock only briefly
    let table_key = format!("{}_chunks", collection_id);
    let table = {
        let tables = index_manager.tables.read().await;
        tables.get(&table_key)
            .ok_or(SearchError::TableNotFound)?
            .clone()
    }; // lock released

    // Build query
    let mut builder = table
        .vector_search(embedding.to_vec())?
        .limit(limit * 2)  // over-fetch for fusion; deduplicate later
        .distance_type(lancedb::DistanceType::Cosine);

    // Apply topic pre-filter (LanceDB predicate pushdown)
    if let Some(topics) = topic_filter {
        if !topics.is_empty() {
            let topic_list = topics.iter()
                .map(|t| format!("'{}'", t.replace('\'', "''")))
                .collect::<Vec<_>>()
                .join(", ");
            builder = builder.filter(
                format!("array_has_any(topics, ARRAY[{}])", topic_list)
            );
        }
    }

    let batches = builder.execute().await?.collect::<Vec<_>>().await?;

    // Parse Arrow RecordBatch results
    Ok(parse_vector_results(batches))
}

pub struct VectorSearchResult {
    pub chunk_id: Uuid,
    pub doc_id: Uuid,
    pub text: String,
    pub contextual_text: String,
    pub cosine_distance: f32,
    pub vector_score: f32,  // = 1.0 - cosine_distance
    pub page: Option<i32>,
    pub topics: Vec<String>,
}
```

---

## 4. Keyword Search Channel (Tantivy BM25)

```rust
pub fn keyword_search_channel(
    tantivy_handle: &TantivyHandle,
    query_text: &str,
    limit: usize,
) -> Vec<KeywordSearchResult> {
    // IndexReader is Clone and requires no lock
    let searcher = tantivy_handle.reader.searcher();

    let schema = &tantivy_handle.schema;
    let text_field = schema.get_field("text").unwrap();
    let chunk_id_field = schema.get_field("chunk_id").unwrap();

    let query_parser = tantivy::query::QueryParser::for_index(
        searcher.index(),
        vec![text_field],
    );

    // Use multi-phrase query: "machine learning" OR machine OR learning
    let query = query_parser.parse_query(query_text)
        .unwrap_or_else(|_| {
            // Fall back to term query if parsing fails
            Box::new(tantivy::query::AllQuery)
        });

    let top_docs = searcher
        .search(&query, &tantivy::collector::TopDocs::with_limit(limit * 2))
        .unwrap_or_default();

    top_docs.into_iter()
        .map(|(score, doc_addr)| {
            let doc = searcher.doc(doc_addr).unwrap();
            let chunk_id_str = doc.get_first(chunk_id_field)
                .and_then(|v| v.as_text())
                .unwrap_or("");
            KeywordSearchResult {
                chunk_id: Uuid::parse_str(chunk_id_str).unwrap_or_default(),
                bm25_score: score,
                keyword_score: normalize_bm25_score(score),  // normalize to [0, 1]
            }
        })
        .collect()
}

fn normalize_bm25_score(raw_score: f32) -> f32 {
    // BM25 scores are unbounded positive floats; normalize via sigmoid-like transform
    // Typical BM25 scores range from 0 to ~20
    (raw_score / 10.0).tanh()
}
```

---

## 5. Graph Proximity Search Channel

Graph proximity scores chunks that are connected to the most relevant entities for the query.

```rust
pub async fn graph_proximity_channel(
    index_manager: &IndexManager,
    collection_id: &Uuid,
    query_text: &str,
    embedding: &[f32],
    depth: u32,
    result_chunk_ids: &[Uuid],  // candidate chunks from vector/keyword search
) -> Vec<GraphProximityResult> {
    // Step 1: Find seed entities by embedding similarity on nodes table
    let seed_entities = find_seed_entities(
        index_manager,
        collection_id,
        embedding,
        top_k: 5,
    ).await.unwrap_or_default();

    if seed_entities.is_empty() {
        return vec![];
    }

    // Step 2: BFS from seed entities up to `depth` hops
    let graph_arc = {
        let graphs = index_manager.graphs.read().await;
        graphs.get(&collection_id.to_string()).cloned()
    };

    let graph_arc = match graph_arc {
        Some(g) => g,
        None => return vec![],
    };

    let reachable = {
        let graph = graph_arc.read().await;  // read lock — concurrent BFS OK
        bfs_reachable(&graph, &seed_entities, depth)
    }; // read lock released

    // Step 3: Score each candidate chunk by its proximity to reachable entities
    // A chunk that MENTIONS a reachable entity gets high proximity score
    let mut scores: HashMap<Uuid, f32> = HashMap::new();
    for chunk_id in result_chunk_ids {
        let proximity = compute_chunk_proximity(chunk_id, &reachable, &seed_entities);
        if proximity > 0.0 {
            scores.insert(*chunk_id, proximity);
        }
    }

    scores.into_iter()
        .map(|(chunk_id, score)| GraphProximityResult { chunk_id, proximity_score: score })
        .collect()
}

fn bfs_reachable(
    graph: &KnowledgeGraph,
    seeds: &[Uuid],
    max_depth: u32,
) -> HashSet<Uuid> {
    let mut visited: HashSet<Uuid> = HashSet::new();
    let mut frontier: VecDeque<(Uuid, u32)> = seeds.iter()
        .map(|&id| (id, 0))
        .collect();

    while let Some((node_id, depth)) = frontier.pop_front() {
        if visited.contains(&node_id) { continue; }
        visited.insert(node_id);

        if depth < max_depth {
            if let Some(neighbors) = graph.adjacency_out.get(&node_id) {
                for &(_, neighbor_id) in neighbors {
                    if !visited.contains(&neighbor_id) {
                        frontier.push_back((neighbor_id, depth + 1));
                    }
                }
            }
        }
    }
    visited
}
```

---

## 6. Score Fusion

```rust
pub fn fuse_scores(
    vector_results: Vec<VectorSearchResult>,
    keyword_results: Vec<KeywordSearchResult>,
    graph_results: Vec<GraphProximityResult>,
    weights: ScoreWeights,
) -> Vec<SearchResult> {
    // Build lookup maps
    let keyword_map: HashMap<Uuid, f32> = keyword_results.into_iter()
        .map(|r| (r.chunk_id, r.keyword_score))
        .collect();
    let graph_map: HashMap<Uuid, f32> = graph_results.into_iter()
        .map(|r| (r.chunk_id, r.proximity_score))
        .collect();

    let mut results: Vec<SearchResult> = vector_results.into_iter()
        .map(|vr| {
            let keyword_score = keyword_map.get(&vr.chunk_id).copied().unwrap_or(0.0);
            let graph_score = graph_map.get(&vr.chunk_id).copied().unwrap_or(0.0);
            let final_score = (vr.vector_score * weights.vector)
                + (keyword_score * weights.keyword)
                + (graph_score * weights.graph);

            SearchResult {
                chunk_id: vr.chunk_id,
                doc_id: vr.doc_id,
                text: vr.text,
                contextual_text: vr.contextual_text,
                vector_score: vr.vector_score,
                keyword_score,
                graph_proximity_score: graph_score,
                final_score,
                page: vr.page,
                topics: vr.topics,
            }
        })
        .collect();

    // Also include keyword-only hits not found in vector search
    for (chunk_id, keyword_score) in &keyword_map {
        if !results.iter().any(|r| &r.chunk_id == chunk_id) {
            results.push(SearchResult {
                chunk_id: *chunk_id,
                keyword_score: *keyword_score,
                final_score: keyword_score * weights.keyword,
                ..Default::default()
            });
        }
    }

    results.sort_by(|a, b| b.final_score.partial_cmp(&a.final_score).unwrap());
    results
}

pub struct ScoreWeights {
    pub vector: f32,   // default: 0.6
    pub keyword: f32,  // default: 0.3
    pub graph: f32,    // default: 0.1
}

impl Default for ScoreWeights {
    fn default() -> Self {
        Self { vector: 0.6, keyword: 0.3, graph: 0.1 }
    }
}
```

---

## 7. Query Types

### 7.1 Natural Language (Default)

```
1. Embed query text → 1536-dim vector
2. Run hybrid search (all 3 channels)
3. Return ranked chunks with surrounding context
```

### 7.2 Entity Search

```
1. Fuzzy name match against nodes table (Levenshtein, case-insensitive)
2. Embedding lookup in nodes table for semantic match
3. Return matching entity nodes with their linked chunks
```

### 7.3 Topic-Filtered

```
1. Standard hybrid search
2. Add LanceDB pre-filter: "array_has_any(topics, ARRAY['topic_name'])"
3. Topic filter is applied at the storage layer (predicate pushdown) for efficiency
```

### 7.4 Graph Traversal

```
1. Find seed entity by name or embedding
2. BFS from seed entity, collect neighboring entities and their chunks
3. Return subgraph as nodes + edges
```

### 7.5 Path Finding

```
1. Resolve from_id and to_id to graph nodes
2. Run Dijkstra (weight = 1/edge_weight, higher weight = shorter path)
3. Return path as ordered list of nodes and edges
```

---

## 8. Search API

```
POST /api/v1/search
Content-Type: application/json
Authorization: Bearer <JWT>

{
  "query": "What are the main applications of transformer architecture?",
  "collection_ids": ["uuid1", "uuid2"],
  "topics": ["machine learning", "NLP"],
  "depth": 2,
  "limit": 20,
  "offset": 0,
  "mode": "hybrid",
  "weights": {
    "vector": 0.6,
    "keyword": 0.3,
    "graph": 0.1
  },
  "timeout_ms": 800
}
```

**Response:**

```json
{
  "results": [
    {
      "chunk_id": "uuid",
      "doc_id": "uuid",
      "doc_title": "Attention Is All You Need",
      "text": "The Transformer model architecture was introduced...",
      "page": 3,
      "vector_score": 0.91,
      "keyword_score": 0.78,
      "graph_proximity_score": 0.45,
      "final_score": 0.823,
      "topics": ["machine learning", "transformers"],
      "highlights": ["transformer architecture", "self-attention"]
    }
  ],
  "total": 47,
  "offset": 0,
  "limit": 20,
  "latency_ms": 143,
  "search_mode": "hybrid"
}
```

**Suggestions (autocomplete):**

```
GET /api/v1/search/suggestions?q=transform&collection_id=uuid

Response:
{
  "suggestions": [
    "transformer architecture",
    "transfer learning",
    "transformation functions"
  ]
}
```

---

## 9. Concurrent Search Architecture

```
                 HTTP Requests (up to 100 concurrent)
                         │
              ┌──────────┼──────────┐
              │          │          │
         Req 1 (task)  Req 2 (task) Req 3 (task) ...
              │          │          │
     [Each task acquires one search_semaphore permit]
              │
   ┌──────────┴───────────────────────────────────┐
   │  tokio::join!(                                │
   │    vector_search_channel(),                  │
   │    keyword_search_channel(),                 │
   │    graph_proximity_channel(),                │
   │  )                                           │
   └──────────────────────────────────────────────┘
              │
   [No shared locks held during search execution]
   [LanceDB: MVCC — concurrent reads against same snapshot]
   [Tantivy: IndexReader is Clone — no lock]
   [petgraph: Arc<RwLock> read lock — many concurrent OK]
```

---

## 10. LRU Caching

### Query Embedding Cache

Avoids re-calling the OpenAI API for repeated queries:

```rust
const EMBEDDING_CACHE_CAPACITY: usize = 1000;
const EMBEDDING_CACHE_TTL: Duration = Duration::from_secs(300); // 5 minutes

pub async fn get_or_embed(
    cache: &Arc<Mutex<LruCache<String, CachedEmbedding>>>,
    query: &str,
    embedder: &dyn Embedder,
) -> Result<Vec<f32>> {
    let cache_key = sha256_hex(query);

    // Check cache
    {
        let mut cache = cache.lock().await;
        if let Some(cached) = cache.get(&cache_key) {
            if cached.cached_at.elapsed() < EMBEDDING_CACHE_TTL {
                return Ok(cached.embedding.clone());
            }
            // TTL expired — fall through to re-embed
        }
    }

    // Cache miss: call embedder
    let embedding = embedder.embed(query).await?;

    // Store in cache
    {
        let mut cache = cache.lock().await;
        cache.put(cache_key, CachedEmbedding {
            embedding: embedding.clone(),
            cached_at: std::time::Instant::now(),
        });
    }

    Ok(embedding)
}
```

### Graph Neighborhood Cache

Caches BFS results for frequently accessed entities:

```rust
const GRAPH_CACHE_CAPACITY: usize = 500;
const GRAPH_CACHE_TTL: Duration = Duration::from_secs(120); // 2 minutes

pub async fn get_or_traverse(
    cache: &Arc<Mutex<LruCache<GraphCacheKey, CachedSubGraph>>>,
    graph: &Arc<RwLock<KnowledgeGraph>>,
    params: TraversalParams,
) -> SubGraph {
    let key = GraphCacheKey {
        node_id: params.root_id,
        depth: params.depth,
        edge_types_hash: hash_edge_types(&params.edge_types),
        topics_hash: hash_topics(&params.topics),
    };

    // Check cache
    {
        let mut cache = cache.lock().await;
        if let Some(cached) = cache.get(&key) {
            let g = graph.read().await;
            if is_cache_valid(cached, &g) {
                return cached.subgraph.clone();
            }
        }
    }

    // Cache miss: traverse
    let subgraph = {
        let g = graph.read().await;
        traverse_subgraph(&g, &params)
    };
    let graph_version = {
        let g = graph.read().await;
        g.version.load(Ordering::Acquire)
    };

    // Store in cache
    {
        let mut cache = cache.lock().await;
        cache.put(key, CachedSubGraph {
            subgraph: subgraph.clone(),
            cached_at: std::time::Instant::now(),
            graph_version,
        });
    }

    subgraph
}
```

### Cache Invalidation

Both caches are fully cleared on each successful index write:

```rust
// Called after batch_insert_chunks() completes
pub async fn invalidate_search_caches(&self) {
    self.query_embedding_cache.lock().await.clear();
    self.graph_neighbor_cache.lock().await.clear();
    tracing::debug!("Search caches invalidated after index write");
}
```

---

## 11. Graph Traversal Optimization

### Batched Hop Queries

Rather than fetching each neighbor individually, batched hop queries collect all target IDs from
hop N and issue a single query for hop N+1:

```rust
pub async fn batched_bfs(
    graph: &KnowledgeGraph,
    seeds: Vec<Uuid>,
    max_depth: u32,
    max_degree: usize,
    min_edge_weight: f32,
) -> SubGraph {
    let mut all_nodes: HashMap<Uuid, &GraphNode> = HashMap::new();
    let mut all_edges: Vec<&GraphEdge> = Vec::new();
    let mut frontier: Vec<Uuid> = seeds;

    for _hop in 0..max_depth {
        // Collect all neighbors of current frontier in one pass (no per-node DB queries)
        let mut next_frontier: Vec<Uuid> = Vec::new();

        // Rayon parallel processing of frontier nodes
        let results: Vec<_> = frontier.par_iter()
            .filter_map(|&node_id| graph.adjacency_out.get(&node_id))
            .flat_map(|edges| edges.iter())
            .collect();

        for &(edge_id, target_id) in &results {
            if let Some(edge) = graph.edges.get(edge_id) {
                if edge.weight < min_edge_weight { continue; }
                if !all_nodes.contains_key(&target_id) {
                    next_frontier.push(*target_id);
                }
                all_edges.push(edge);
            }
        }

        // Enforce max_degree per node by keeping highest-weight edges
        // (pruning happens at insert time — see graph engine spec)

        for node_id in &next_frontier {
            if let Some(node) = graph.nodes.get(node_id) {
                all_nodes.insert(*node_id, node);
            }
        }

        frontier = next_frontier;
        if frontier.is_empty() { break; }
    }

    SubGraph {
        nodes: all_nodes.into_values().cloned().collect(),
        edges: all_edges.into_iter().cloned().collect(),
        root_id: seeds[0],
        depth: max_depth,
    }
}
```

---

## 12. Search Performance Targets

| Metric | Target | Mechanism |
|--------|--------|-----------|
| P50 latency | < 200ms | Parallel 3-way search, embedding cache |
| P95 latency | < 800ms | Configurable timeout, bounded semaphore |
| Throughput | 100 concurrent | `search_semaphore(100)` |
| Cache hit rate | > 60% for common queries | LRU embedding cache, 5min TTL |
| Graph BFS (depth 3) | < 50ms | In-memory petgraph, Rayon parallel hops |
| Vector search (100K chunks) | < 150ms | LanceDB IVF-PQ ANN index |
| BM25 (100K docs) | < 30ms | Tantivy on-disk inverted index |
