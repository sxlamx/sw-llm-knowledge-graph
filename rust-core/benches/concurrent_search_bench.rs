//! concurrent_search_bench — Phase 6 Criterion benchmarks for 100-concurrent-search.
//!
//! Benchmarks:
//!   - `concurrent_100_searches`  : 100 simultaneous text_search calls against IndexManager.
//!   - `concurrent_50_searches`   : 50 simultaneous searches (warm path, cached).
//!   - `concurrent_100_vector_timeout` : 100 vector searches with 800ms timeout SLA.
//!
//! After the Bot 2 audit, all `#[pymethods]` now require `py: Python<'_>` and
//! release the GIL via `py.allow_threads()`.  The benchmarks therefore call
//! text_search through `pyo3::Python::with_gil(|py| { ... })` and use
//! `std::thread::spawn` for concurrency instead of `tokio::task::spawn`.

use criterion::{black_box, criterion_group, criterion_main, Criterion};
use rust_core::index_manager::IndexManager;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use uuid::Uuid;

fn bench_concurrent_100_searches(c: &mut Criterion) {
    let tmp = tempfile::tempdir().unwrap();
    let im = Arc::new(IndexManager::new(tmp.path().to_str().unwrap()).unwrap());
    let coll_id = Uuid::new_v4().to_string();

    pyo3::prepare_freethreaded_python();
    pyo3::Python::with_gil(|py| {
        im.initialize_collection(py, &coll_id).unwrap();
    });

    let chunks = serde_json::json!([{
        "id": Uuid::new_v4().to_string(),
        "doc_id": Uuid::new_v4().to_string(),
        "collection_id": coll_id,
        "text": "benchmark document content for concurrent search testing",
        "contextual_text": "benchmark document content for concurrent search testing",
        "embedding": vec![0.1f32; 1024],
        "position": 0,
        "token_count": 8,
        "page": 1,
        "topics": ["bench"],
        "created_at": 1700000000000i64
    }]).to_string();

    pyo3::Python::with_gil(|py| {
        im.insert_chunks(py, &coll_id, &chunks).unwrap();
        im.flush_tantivy(py).unwrap();
    });

    std::thread::sleep(std::time::Duration::from_millis(200));

    let queries: Vec<String> = (0..100)
        .map(|i| format!("query term {}", i))
        .collect();

    c.bench_function("concurrent_100_searches", |b| {
        b.iter(|| {
            let _count = AtomicUsize::new(0);
            let handles: Vec<_> = queries.iter().map(|q| {
                let im = Arc::clone(&im);
                let coll = coll_id.clone();
                let query = q.clone();
                let count_ptr = Arc::new(AtomicUsize::new(0));
                std::thread::spawn(move || {
                    let result = pyo3::Python::with_gil(|py| {
                        im.text_search(py, &coll, &query, 10)
                    });
                    if result.is_ok() { count_ptr.fetch_add(1, Ordering::Relaxed); }
                    count_ptr.load(Ordering::Relaxed)
                })
            }).collect();
            let total: usize = handles.into_iter().map(|h| h.join().unwrap()).sum();
            black_box(total)
        })
    });
}

fn bench_concurrent_50_searches(c: &mut Criterion) {
    let tmp = tempfile::tempdir().unwrap();
    let im = Arc::new(IndexManager::new(tmp.path().to_str().unwrap()).unwrap());
    let coll_id = Uuid::new_v4().to_string();

    pyo3::prepare_freethreaded_python();
    pyo3::Python::with_gil(|py| {
        im.initialize_collection(py, &coll_id).unwrap();
    });

    let chunks = serde_json::json!([{
        "id": Uuid::new_v4().to_string(),
        "doc_id": Uuid::new_v4().to_string(),
        "collection_id": coll_id,
        "text": "common query benchmark document content for search",
        "contextual_text": "common query benchmark document content for search",
        "embedding": vec![0.1f32; 1024],
        "position": 0,
        "token_count": 8,
        "page": 1,
        "topics": ["bench"],
        "created_at": 1700000000000i64
    }]).to_string();

    pyo3::Python::with_gil(|py| {
        im.insert_chunks(py, &coll_id, &chunks).unwrap();
        im.flush_tantivy(py).unwrap();
    });

    std::thread::sleep(std::time::Duration::from_millis(200));

    let queries: Vec<String> = (0..50)
        .map(|i| format!("common query {}", i % 10))
        .collect();

    c.bench_function("concurrent_50_searches", |b| {
        b.iter(|| {
            let handles: Vec<_> = queries.iter().map(|q| {
                let im = Arc::clone(&im);
                let coll = coll_id.clone();
                let query = q.clone();
                std::thread::spawn(move || {
                    let result = pyo3::Python::with_gil(|py| {
                        im.text_search(py, &coll, &query, 10)
                    });
                    result.is_ok()
                })
            }).collect();
            let successes: usize = handles.into_iter().map(|h| h.join().unwrap()).filter(|ok| *ok).count();
            black_box(successes)
        })
    });
}

fn bench_concurrent_100_vector_timeout(c: &mut Criterion) {
    let tmp = tempfile::tempdir().unwrap();
    let im = Arc::new(IndexManager::new(tmp.path().to_str().unwrap()).unwrap());
    let coll_id = Uuid::new_v4().to_string();

    pyo3::prepare_freethreaded_python();
    pyo3::Python::with_gil(|py| {
        im.initialize_collection(py, &coll_id).unwrap();
    });

    let chunks = serde_json::json!([{
        "id": Uuid::new_v4().to_string(),
        "doc_id": Uuid::new_v4().to_string(),
        "collection_id": coll_id,
        "text": "vector search timeout benchmark content",
        "contextual_text": "vector search timeout benchmark content",
        "embedding": vec![0.1f32; 1024],
        "position": 0,
        "token_count": 8,
        "page": 1,
        "topics": ["bench"],
        "created_at": 1700000000000i64
    }]).to_string();

    pyo3::Python::with_gil(|py| {
        im.insert_chunks(py, &coll_id, &chunks).unwrap();
        im.flush_tantivy(py).unwrap();
    });

    std::thread::sleep(std::time::Duration::from_millis(200));

    let embeddings: Vec<Vec<f32>> = (0..100)
        .map(|_| vec![0.1f32; 1024])
        .collect();

    c.bench_function("concurrent_100_vector_search_with_timeout", |b| {
        b.iter(|| {
            let handles: Vec<_> = embeddings.iter().map(|emb| {
                let im = Arc::clone(&im);
                let coll = coll_id.clone();
                let emb = emb.clone();
                std::thread::spawn(move || {
                    pyo3::Python::with_gil(|py| {
                        im.vector_search_with_timeout(py, emb, &coll, 10, Some(800))
                    })
                })
            }).collect();
            let successes: usize = handles.into_iter()
                .map(|h| h.join().unwrap())
                .filter(|r| r.is_ok())
                .count();
            black_box(successes)
        })
    });
}

criterion_group!(
    benches,
    bench_concurrent_100_searches,
    bench_concurrent_50_searches,
    bench_concurrent_100_vector_timeout,
);
criterion_main!(benches);