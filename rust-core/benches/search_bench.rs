//! search_bench — Phase 3 Criterion benchmarks for search-critical paths.
//!
//! Benchmarks:
//!   - `bfs_reachable`   : BFS on a synthetic 1 000-node / 5 000-edge graph.
//!   - `score_fusion`    : 3-channel score fusion across 500 candidate IDs.
//!   - `ontology_validate`: Rayon-parallel entity validation (100 entities).
//!   - `graph_prune`     : Edge pruning on a dense 500-node graph.

use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion};
use rand::{rngs::SmallRng, Rng, SeedableRng};
use rust_core::graph::traversal::bfs_reachable;
use rust_core::models::{EdgeType, ExtractedEntity, GraphEdge, GraphNode, KnowledgeGraph, NodeType};
use rust_core::ontology::{Ontology, OntologyValidator};
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
                *scores.entry(id.clone()).or_default() += s * 0.6;
            }
            for (id, s) in black_box(&keyword) {
                *scores.entry(id.clone()).or_default() += s * 0.3;
            }
            for (id, s) in black_box(&graph) {
                *scores.entry(id.clone()).or_default() += s * 0.1;
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
    bench_graph_prune
);
criterion_main!(benches);
