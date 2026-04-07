//! concurrent_search_bench — Phase 6 Criterion benchmarks for 100-concurrent-search.
//!
//! Benchmarks:
//!   - `concurrent_100_searches`  : 100 simultaneous text_search calls against IndexManager.
//!   - `concurrent_50_searches`   : 50 simultaneous searches (warm path, cached).
//!
//! Uses tokio::task::spawn so all searches run concurrently on the
//! multi-threaded Tokio runtime.

use criterion::{black_box, criterion_group, criterion_main, Criterion};
use rust_core::index_manager::IndexManager;
use std::sync::Arc;
use tokio::task::JoinSet;

fn make_test_env() -> tempfile::TempDir {
    tempfile::tempdir().unwrap()
}

fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_multi_thread()
        .worker_threads(8)
        .enable_all()
        .build()
        .unwrap()
}

async fn run_concurrent_searches(
    im: Arc<IndexManager>,
    coll_id: &str,
    queries: &[String],
    limit: usize,
) -> usize {
    let mut set = JoinSet::new();
    for q in queries {
        let im = Arc::clone(&im);
        let coll = coll_id.to_string();
        let query = q.clone();
        set.spawn(async move {
            im.text_search(&coll, &query, limit).ok()
        });
    }
    let mut successes = 0;
    while let Some(res) = set.join_next().await {
        if res.unwrap().is_some() {
            successes += 1;
        }
    }
    successes
}

fn bench_concurrent_100_searches(c: &mut Criterion) {
    let rt = runtime();
    let tmp = make_test_env();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();
    let coll_id = uuid::Uuid::new_v4().to_string();
    im.initialize_collection(&coll_id).unwrap();

    let queries: Vec<String> = (0..100)
        .map(|i| format!("query term {}", i))
        .collect();

    let im = Arc::new(im);
    rt.block_on(async {
        c.bench_function("concurrent_100_searches", |b| {
            b.to_async(&rt).iter(|| {
                let im = Arc::clone(&im);
                async move {
                    let count = run_concurrent_searches(im, &coll_id, &queries, 10).await;
                    black_box(count)
                }
            })
        });
    });
}

fn bench_concurrent_50_searches(c: &mut Criterion) {
    let rt = runtime();
    let tmp = make_test_env();
    let im = IndexManager::new(tmp.path().to_str().unwrap()).unwrap();
    let coll_id = uuid::Uuid::new_v4().to_string();
    im.initialize_collection(&coll_id).unwrap();

    let queries: Vec<String> = (0..50)
        .map(|i| format!("common query {}", i % 10))
        .collect();

    let im = Arc::new(im);
    rt.block_on(async {
        c.bench_function("concurrent_50_searches", |b| {
            b.to_async(&rt).iter(|| {
                let im = Arc::clone(&im);
                async move {
                    let count = run_concurrent_searches(im, &coll_id, &queries, 10).await;
                    black_box(count)
                }
            })
        });
    });
}

criterion_group!(
    benches,
    bench_concurrent_100_searches,
    bench_concurrent_50_searches,
);
criterion_main!(benches);
