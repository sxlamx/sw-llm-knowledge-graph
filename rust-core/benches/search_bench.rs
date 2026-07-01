//! search_bench — Phase 3+4 Criterion benchmarks for search-critical paths.
//!
//! Benchmarks:
//!   - `bfs_reachable`   : BFS on a synthetic 1 000-node / 5 000-edge graph.
//!   - `score_fusion`    : 3-channel score fusion across 500 candidate IDs.
//!   - `ontology_validate`: Rayon-parallel entity validation (100 entities).
//!   - `graph_prune`     : Edge pruning on a dense 500-node graph.
//!   - [Phase 4] `bm25_normalization` : Sigmoid normalization throughput.
//!   - [Phase 4] `graph_proximity_score` : Chunk proximity computation.

use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion};
use rand::rngs::SmallRng;
use rand::{Rng, SeedableRng};
use rust_core::graph::traversal::bfs_reachable;
use rust_core::models::{EdgeType, ExtractedEntity, GraphEdge, GraphNode, KnowledgeGraph, NodeType};
use rust_core::ontology::{Ontology, OntologyValidator};
use rust_core::storage::normalize_bm25_score;
use std::collections::HashMap;
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn make_node(cid: Uuid) -> GraphNode {
    GraphNode {
        id: Uuid::new_v4(),
        node_type: NodeType::Concept,
        label: "bench_node".into(),
        description: None,
        aliases: vec![],
        confidence: 0.8,
        ontology_class: None,
        properties: HashMap::new(),
        collection_id: cid,
        display_label: None,
        dedup_key: None,
        doc_origins: vec![],
        created_at: None,
        updated_at: None,
    }
}

fn make_edge(src: Uuid, tgt: Uuid, cid: Uuid, weight: f32) -> GraphEdge {
    GraphEdge {
        id: Uuid::new_v4(),
        source: src,
        target: tgt,
        edge_type: EdgeType::RelatesTo,
        weight,
        context: None,
        chunk_id: None,
        properties: HashMap::new(),
        collection_id: cid,
        display_label: None,
        dedup_key: None,
        predicate: String::new(),
        time: None,
        location: None,
        participants: None,
        doc_origins: vec![],
    }
}

/// Build a synthetic graph with `n_nodes` nodes and ~`n_edges` randomly wired edges.
fn build_graph(n_nodes: usize, n_edges: usize) -> (KnowledgeGraph, Vec<Uuid>) {
    let cid = Uuid::new_v4();
    let mut kg = KnowledgeGraph::new(cid);
    let mut rng = SmallRng::seed_from_u64(42);

    let nodes: Vec<GraphNode> = (0..n_nodes).map(|_| make_node(cid)).collect();
    let ids: Vec<Uuid> = nodes.iter().map(|n| n.id).collect();
    kg.insert_nodes_batch(nodes);

    let edges: Vec<GraphEdge> = (0..n_edges)
        .map(|_| {
            let src = ids[rng.gen_range(0..n_nodes)];
            let tgt = ids[rng.gen_range(0..n_nodes)];
            make_edge(src, tgt, cid, rng.gen_range(0.1f32..1.0))
        })
        .collect();
    kg.insert_edges_batch(edges);

    (kg, ids)
}

/// Build a list of entities: mix of valid and invalid types.
fn build_entities(n: usize) -> Vec<ExtractedEntity> {
    let types = ["Person", "Organization", "Location", "Concept", "Event", "Gadget"];
    let mut rng = SmallRng::seed_from_u64(99);
    (0..n)
        .map(|i| ExtractedEntity {
            name: format!("Entity {i}"),
            entity_type: types[rng.gen_range(0..types.len())].to_string(),
            description: "benchmark entity".into(),
            aliases: vec![],
            confidence: rng.gen_range(0.3f32..1.0),
        })
        .collect()
}

// ---------------------------------------------------------------------------
// BFS traversal benchmark
// ---------------------------------------------------------------------------

fn bench_bfs(c: &mut Criterion) {
    let (kg, ids) = build_graph(1_000, 5_000);
    let start = ids[0];

    let mut group = c.benchmark_group("graph_traversal");
    for depth in [2u32, 4, 6] {
        group.bench_with_input(
            BenchmarkId::new("bfs_reachable_depth", depth),
            &depth,
            |b, &d| {
                b.iter(|| {
                    let reachable = bfs_reachable(black_box(&kg), &[start], d, 0.0);
                    black_box(reachable.len())
                })
            },
        );
    }
    group.finish();
}

// ---------------------------------------------------------------------------
// Score fusion benchmark
// ---------------------------------------------------------------------------

fn bench_score_fusion(c: &mut Criterion) {
    const N: usize = 500;
    let mut rng = SmallRng::seed_from_u64(7);

    let vector: Vec<(String, f32)> = (0..N)
        .map(|i| (format!("c{i}"), rng.gen()))
        .collect();
    let keyword: Vec<(String, f32)> = (0..N / 2)
        .map(|i| (format!("c{i}"), rng.gen()))
        .collect();
    let graph: Vec<(String, f32)> = (0..N / 4)
        .map(|i| (format!("c{}", i * 2), rng.gen()))
        .collect();

    c.bench_function("score_fusion_500_candidates", |b| {
        b.iter(|| {
            let mut scores: HashMap<String, f32> = HashMap::with_capacity(N);
            for (id, s) in black_box(&vector) {
                let entry = scores.entry(id.clone()).or_insert(0.0f32);
                *entry += s * 0.6f32;
            }
            for (id, s) in black_box(&keyword) {
                let entry = scores.entry(id.clone()).or_insert(0.0f32);
                *entry += s * 0.3f32;
            }
            for (id, s) in black_box(&graph) {
                let entry = scores.entry(id.clone()).or_insert(0.0f32);
                *entry += s * 0.1f32;
            }
            scores.len()
        })
    });
}

// ---------------------------------------------------------------------------
// Ontology validation benchmark (Rayon parallel)
// ---------------------------------------------------------------------------

fn bench_ontology_validate(c: &mut Criterion) {
    let validator = OntologyValidator::new(Ontology::default_ontology(), 0.4);
    let entities = build_entities(100);

    c.bench_function("ontology_validate_100_entities", |b| {
        b.iter(|| {
            let report =
                validator.validate_batch(black_box(entities.clone()), vec![]);
            black_box(report.valid_entities.len())
        })
    });
}

// ---------------------------------------------------------------------------
// Graph pruning benchmark
// ---------------------------------------------------------------------------

fn bench_graph_prune(c: &mut Criterion) {
    c.bench_function("graph_prune_500_nodes_5000_edges", |b| {
        b.iter(|| {
            let (mut kg, _) = build_graph(500, 5_000);
            let (removed, _) = kg.prune_edges(black_box(0.3), black_box(20));
            black_box(removed)
        })
    });
}

// ---------------------------------------------------------------------------

criterion_group!(
    benches,
    bench_bfs,
    bench_score_fusion,
    bench_ontology_validate,
    bench_graph_prune,
    bench_bm25_normalization,
    bench_graph_proximity_scoring
);
criterion_main!(benches);

// ---------------------------------------------------------------------------
// Phase 4 — BM25 normalization throughput
// ---------------------------------------------------------------------------

fn bench_bm25_normalization(c: &mut Criterion) {
    let scores: Vec<f32> = (0..10_000).map(|i| (i as f32) * 0.01).collect();
    c.bench_function("bm25_normalization_10k_scores", |b| {
        b.iter(|| {
            let normalized: Vec<f32> = black_box(&scores).iter().map(|&s| normalize_bm25_score(s)).collect();
            black_box(normalized.len())
        })
    });
}

// ---------------------------------------------------------------------------
// Phase 4 — Graph proximity chunk scoring
// ---------------------------------------------------------------------------

fn bench_graph_proximity_scoring(c: &mut Criterion) {
    let (kg, ids) = build_graph(200, 1000);
    let seeds = vec![ids[0]];

    c.bench_function("graph_proximity_scoring_200_nodes", |b| {
        b.iter(|| {
            let reachable = bfs_reachable(black_box(&kg), &seeds, 2, 0.0);
            let mut chunk_scores: HashMap<uuid::Uuid, f32> = HashMap::new();
            for edge in kg.edges.values() {
                if reachable.contains(&edge.source) {
                    if let Some(chunk_id) = edge.chunk_id {
                        let score = 1.0 / (1.0 + 1.0);
                        chunk_scores.insert(chunk_id, score);
                    }
                }
            }
            black_box(chunk_scores.len())
        })
    });
}
