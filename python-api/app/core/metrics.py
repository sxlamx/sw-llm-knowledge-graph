"""Prometheus metrics registry — imported by routers and middleware."""

from prometheus_client import Counter, Gauge, Histogram

KG_INDEX_STATE = Gauge(
    "kg_index_state",
    "Index state (0=uninitialized, 1=building, 2=active, 3=compacting, 4=degraded)",
)
KG_PENDING_WRITES = Gauge(
    "kg_index_pending_writes",
    "Vectors written since last compaction",
)
KG_CONCURRENT_SEARCHES = Gauge(
    "kg_concurrent_searches",
    "Currently active search semaphore slots in use (capacity=100)",
)
KG_SEARCH_REQUESTS_TOTAL = Counter(
    "kg_search_requests_total",
    "Total search requests",
    ["mode"],
)
KG_INGEST_JOBS_TOTAL = Counter(
    "kg_ingest_jobs_total",
    "Total ingest jobs started",
    ["status"],
)
KG_SEARCH_LATENCY = Histogram(
    "kg_search_latency_seconds",
    "Search request latency in seconds",
    buckets=[0.05, 0.1, 0.2, 0.4, 0.8, 1.5, 3.0],
)
