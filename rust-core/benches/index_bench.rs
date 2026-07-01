//! index_bench — Phase 3 Criterion benchmarks for index-write and graph-write paths.
//!
//! Benchmarks:
//!   - `kg_insert_nodes`  : Batch insert into KnowledgeGraph (100/500/1000 nodes).
//!   - `kg_insert_edges`  : Batch insert edges after node load.
//!   - `kg_get_data`      : Serialise graph data to JSON (read-path latency).
//!   - `entity_resolution`: EntityResolver on a 200-node existing graph.

use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion};
use rand::rngs::SmallRng;
use rand::{Rng, SeedableRng};
use rust_core::graph::builder::{EntityResolver, build_graph_nodes};
use rust_core::models::{EdgeType, ExtractedEntity, GraphEdge, GraphNode, KnowledgeGraph, NodeType};
use std::collections::HashMap;
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn make_node(label: &str, cid: Uuid) -> GraphNode {
    GraphNode {
        id: Uuid::new_v4(),
        node_type: NodeType::Concept,
        label: label.to_string(),
        description: Some("bench description".into()),
        aliases: vec![],
        confidence: 0.85,
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

fn make_edge(src: Uuid, tgt: Uuid, cid: Uuid) -> GraphEdge {
    GraphEdge {
        id: Uuid::new_v4(),
        source: src,
        target: tgt,
        edge_type: EdgeType::RelatesTo,
        weight: 0.8,
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

fn make_entities(n: usize) -> Vec<ExtractedEntity> {
    let types = ["Person", "Organization", "Location", "Concept"];
    let mut rng = SmallRng::seed_from_u64(42);
    (0..n)
        .map(|i| ExtractedEntity {
            name: format!("Entity {i}"),
            entity_type: types[rng.gen_range(0..types.len())].to_string(),
            description: "bench".into(),
            aliases: vec![],
            confidence: 0.9,
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Node insert benchmarks
// ---------------------------------------------------------------------------

fn bench_kg_insert_nodes(c: &mut Criterion) {
    let cid = Uuid::new_v4();
    let mut group = c.benchmark_group("kg_insert_nodes");

    for n in [100usize, 500, 1_000] {
        let nodes: Vec<GraphNode> = (0..n)
            .map(|i| make_node(&format!("node_{i}"), cid))
            .collect();

        group.bench_with_input(BenchmarkId::from_parameter(n), &nodes, |b, nodes| {
            b.iter(|| {
                let mut kg = KnowledgeGraph::new(cid);
                kg.insert_nodes_batch(black_box(nodes.clone()));
                black_box(kg.node_count())
            })
        });
    }
    group.finish();
}

// ---------------------------------------------------------------------------
// Edge insert benchmark
// ---------------------------------------------------------------------------

fn bench_kg_insert_edges(c: &mut Criterion) {
    let cid = Uuid::new_v4();
    let n_nodes = 200;

    let nodes: Vec<GraphNode> = (0..n_nodes).map(|i| make_node(&format!("n{i}"), cid)).collect();
    let ids: Vec<Uuid> = nodes.iter().map(|n| n.id).collect();

    let mut rng = SmallRng::seed_from_u64(11);
    let edges: Vec<GraphEdge> = (0..1_000)
        .map(|_| {
            let src = ids[rng.gen_range(0..n_nodes)];
            let tgt = ids[rng.gen_range(0..n_nodes)];
            make_edge(src, tgt, cid)
        })
        .collect();

    c.bench_function("kg_insert_1000_edges", |b| {
        b.iter(|| {
            let mut kg = KnowledgeGraph::new(cid);
            kg.insert_nodes_batch(nodes.clone());
            kg.insert_edges_batch(black_box(edges.clone()));
            black_box(kg.edge_count())
        })
    });
}

// ---------------------------------------------------------------------------
// Graph JSON serialisation (read-path)
// ---------------------------------------------------------------------------

fn bench_kg_json_serialise(c: &mut Criterion) {
    let cid = Uuid::new_v4();
    let n = 500;

    let nodes: Vec<GraphNode> = (0..n).map(|i| make_node(&format!("node_{i}"), cid)).collect();
    let ids: Vec<Uuid> = nodes.iter().map(|n| n.id).collect();

    let mut rng = SmallRng::seed_from_u64(55);
    let edges: Vec<GraphEdge> = (0..2_000)
        .map(|_| {
            let src = ids[rng.gen_range(0..n)];
            let tgt = ids[rng.gen_range(0..n)];
            make_edge(src, tgt, cid)
        })
        .collect();

    let mut kg = KnowledgeGraph::new(cid);
    kg.insert_nodes_batch(nodes);
    kg.insert_edges_batch(edges);

    c.bench_function("kg_json_serialise_500n_2000e", |b| {
        b.iter(|| {
            let nodes: Vec<&rust_core::models::GraphNode> = kg.nodes.values().collect();
            let edges: Vec<&rust_core::models::GraphEdge> = kg.edges.values().collect();
            let json = serde_json::json!({
                "nodes": nodes,
                "edges": edges,
            });
            black_box(json.to_string().len())
        })
    });
}

// ---------------------------------------------------------------------------
// Entity resolution benchmark
// ---------------------------------------------------------------------------

fn bench_entity_resolution(c: &mut Criterion) {
    let cid = Uuid::new_v4();
    let resolver = EntityResolver::new();

    // Build 200 existing nodes
    let existing: Vec<GraphNode> = (0..200)
        .map(|i| make_node(&format!("Entity {i}"), cid))
        .collect();

    // 50 candidate entities — some exact matches, some new
    let candidates = make_entities(50);

    c.bench_function("entity_resolution_50_candidates_200_existing", |b| {
        b.iter(|| {
            let (new_nodes, id_map) = build_graph_nodes(
                black_box(candidates.clone()),
                cid,
                &existing,
                &HashMap::new(),
                &resolver,
            );
            black_box((new_nodes.len(), id_map.len()))
        })
    });
}

// ---------------------------------------------------------------------------
// Phase 6 — LanceDB chunk insert throughput benchmark
// ---------------------------------------------------------------------------

fn bench_insert_chunks_batch_throughput(c: &mut Criterion) {
    use rust_core::index_manager::IndexManager;
    use rust_core::models::ChunkRecord;

    let tmp = tempfile::tempdir().unwrap();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();
    let coll_id = Uuid::new_v4().to_string();

    pyo3::prepare_freethreaded_python();
    pyo3::Python::with_gil(|py| {
        im.initialize_collection(py, &coll_id).unwrap();
    });

    let chunks: Vec<ChunkRecord> = (0..512)
        .map(|i| ChunkRecord {
            id: Uuid::new_v4(),
            doc_id: Uuid::new_v4(),
            collection_id: Uuid::parse_str(&coll_id).unwrap(),
            text: format!("benchmark chunk {i} with enough words to be nontrivial"),
            contextual_text: Some(format!("context for chunk {i}")),
            embedding: Some(vec![0.05f32; 1024]),
            position: i as i32,
            token_count: 12,
            page: Some(1),
            topics: Some(vec!["bench".into()]),
            created_at: Some(1700000000000i64),
        })
        .collect();

    let chunks_json = serde_json::to_string(&chunks).unwrap();

    c.bench_function("insert_chunks_batch_512_rows", |b| {
        b.iter(|| {
            pyo3::Python::with_gil(|py| {
                let result = im.insert_chunks_batch(py, &coll_id, black_box(&chunks_json));
                black_box(result.is_ok())
            })
        })
    });
}

// ---------------------------------------------------------------------------

criterion_group!(
    benches,
    bench_kg_insert_nodes,
    bench_kg_insert_edges,
    bench_kg_json_serialise,
    bench_entity_resolution,
    bench_insert_chunks_batch_throughput,
);
criterion_main!(benches);
