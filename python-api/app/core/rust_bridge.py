"""Rust core bridge — PyO3 wrapper with async helpers."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
import logging
import json

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=8)
_index_manager: Optional["PyIndexManager"] = None

try:
    # IndexManager is exported as "IndexManager" by PyO3 (not "PyIndexManager")
    from rust_core import IndexManager as PyIndexManager, PySearchEngine, PyIngestionEngine, PyOntologyValidator
    RUST_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Rust core not available: {e}. Using fallback implementations.")
    RUST_AVAILABLE = False


def get_index_manager() -> Optional["PyIndexManager"]:
    global _index_manager
    if _index_manager is None and RUST_AVAILABLE:
        try:
            from app.config import get_settings
            settings = get_settings()
            _index_manager = PyIndexManager(settings.lancedb_path)
        except Exception as e:
            logger.error(f"Failed to initialize Rust IndexManager: {e}")
            return None
    return _index_manager


def get_search_engine() -> Optional["PySearchEngine"]:
    if RUST_AVAILABLE:
        return PySearchEngine()
    return None


def get_ingestion_engine() -> Optional["PyIngestionEngine"]:
    if RUST_AVAILABLE:
        return PyIngestionEngine()
    return None


def get_ontology_validator() -> Optional["PyOntologyValidator"]:
    if RUST_AVAILABLE:
        return PyOntologyValidator()
    return None


async def rust_keyword_search_async(collection_id: str, query: str, limit: int) -> list[dict]:
    im = get_index_manager()
    if im is None:
        return []

    loop = asyncio.get_running_loop()
    try:
        results_json = await loop.run_in_executor(
            _executor,
            lambda: im.text_search(collection_id, query, limit),
        )
        results = json.loads(results_json)
        fused_results = []
        for r in results:
            bm25 = r.get("bm25_score", 0.0)
            keyword_score = bm25 / (bm25 + 1.0)  # sigmoid normalization to [0, 1]
            fused_results.append({
                "id": r.get("id", ""),
                "doc_id": r.get("doc_id", ""),
                "text": r.get("text", ""),
                "collection_id": collection_id,
                "keyword_score": keyword_score,
                "highlights": r.get("highlights", []),
            })
        return fused_results
    except Exception as e:
        logger.error(f"Rust keyword search error: {e}")
        return []


async def rust_bfs_proximity_async(
    collection_id: str,
    query_embedding: list[float],
    limit: int = 20,
) -> list[dict]:
    """Graph proximity channel: find entity nodes similar to query embedding, run BFS, return chunks.

    Uses the Rust IndexManager.graph_proximity_search() which runs bfs_reachable
    in-memory on the petgraph instead of deserializing JSON to Python and doing
    BFS there.  Falls back to the Python JSON-based BFS when Rust is unavailable.

    Args:
        collection_id: Collection UUID
        query_embedding: 1024-dim query embedding from Qwen3
        limit: max chunks to return

    Returns:
        List of dicts with keys: chunk_id, graph_proximity_score
    """
    im = get_index_manager()
    if im is None:
        return []

    loop = asyncio.get_running_loop()
    try:
        results_json = await loop.run_in_executor(
            _executor,
            lambda: im.graph_proximity_search(collection_id, query_embedding, 2, limit),
        )
        results = json.loads(results_json)
        if not isinstance(results, list):
            return []
        return [
            {
                "chunk_id": r.get("chunk_id", ""),
                "graph_proximity_score": r.get("graph_proximity_score", 0.0),
            }
            for r in results
        ]
    except Exception as e:
        logger.error(f"Rust BFS proximity error: {e}")
        return []


async def rust_insert_chunks_async(collection_id: str, chunks_json: str) -> int:
    im = get_index_manager()
    if im is None:
        return 0

    loop = asyncio.get_running_loop()
    try:
        count = await loop.run_in_executor(
            _executor,
            lambda: im.insert_chunks(collection_id, chunks_json),
        )
        return count
    except Exception as e:
        logger.error(f"Rust insert error: {e}")
        return 0


async def rust_init_collection_async(collection_id: str) -> None:
    im = get_index_manager()
    if im is None:
        return

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            _executor,
            lambda: im.initialize_collection(collection_id),
        )
    except Exception as e:
        logger.error(f"Rust init collection error: {e}")


# ---------------------------------------------------------------------------
# Phase 3 background tasks
# ---------------------------------------------------------------------------

async def _tantivy_commit_loop(interval_seconds: float = 0.5) -> None:
    """Commit staged Tantivy documents every `interval_seconds`.

    This implements the Phase 3 "Tantivy batch committer: 500 ms interval"
    requirement.  Start once at application startup:

        asyncio.create_task(_tantivy_commit_loop())
    """
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(interval_seconds)
        im = get_index_manager()
        if im is None:
            continue
        try:
            await loop.run_in_executor(
                _executor,
                lambda: im.flush_tantivy(),
            )
        except Exception as e:
            logger.debug(f"Tantivy commit loop error (non-fatal): {e}")


async def _graph_prune_loop(
    collection_ids_fn,
    interval_seconds: float = 3600.0,
    min_weight: float = 0.3,
    max_degree: int = 100,
) -> None:
    """Hourly graph-pruning background task.

    `collection_ids_fn` is a zero-argument async callable that returns the
    list of active collection IDs to prune.  Example startup usage:

        asyncio.create_task(
            _graph_prune_loop(
                collection_ids_fn=lambda: list_active_collection_ids(),
            )
        )
    """
    import json as _json
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(interval_seconds)
        im = get_index_manager()
        if im is None:
            continue
        try:
            collection_ids = await collection_ids_fn()
            for cid in collection_ids:
                try:
                    result_json = await loop.run_in_executor(
                        _executor,
                        lambda c=cid: im.prune_graph(c, min_weight, max_degree),
                    )
                    result = _json.loads(result_json)
                    if result.get("edges_removed", 0) > 0:
                        logger.info(
                            "Graph pruned collection=%s removed=%d affected=%d",
                            cid,
                            result["edges_removed"],
                            result.get("nodes_affected", 0),
                        )
                except Exception as e:
                    logger.warning(f"Graph prune failed for collection {cid}: {e}")
        except Exception as e:
            logger.warning(f"Graph prune loop error: {e}")


async def rust_prune_dangling_edges_async(collection_id: str) -> int:
    """Prune dangling edges (edges whose source/target/participants don't exist in the node set).

    Uses the Rust IndexManager.prune_dangling_edges_pyo3() for fast in-memory pruning.
    Returns the number of pruned edges.
    """
    im = get_index_manager()
    if im is None:
        return 0

    loop = asyncio.get_running_loop()
    try:
        count = await loop.run_in_executor(
            _executor,
            lambda: im.prune_dangling_edges_pyo3(collection_id),
        )
        if count > 0:
            logger.info(f"Pruned {count} dangling edges for collection {collection_id}")
        return count
    except Exception as e:
        logger.error(f"Rust dangling edge prune error: {e}")
        return 0


async def rust_detect_node_conflicts_async(collection_id: str, new_nodes_json: str) -> str:
    """Async wrapper for IndexManager.detect_node_conflicts()."""
    im = get_index_manager()
    if im is None:
        return "[]"
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            _executor,
            lambda: im.detect_node_conflicts(collection_id, new_nodes_json),
        )
    except Exception as e:
        logger.error(f"Rust detect_node_conflicts error: {e}")
        return "[]"


async def rust_detect_edge_conflicts_async(collection_id: str, new_edges_json: str) -> str:
    """Async wrapper for IndexManager.detect_edge_conflicts()."""
    im = get_index_manager()
    if im is None:
        return "[]"
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            _executor,
            lambda: im.detect_edge_conflicts(collection_id, new_edges_json),
        )
    except Exception as e:
        logger.error(f"Rust detect_edge_conflicts error: {e}")
        return "[]"


async def rust_merge_nodes_async(collection_id: str, new_nodes_json: str, strategy: str) -> str:
    """Async wrapper for IndexManager.merge_nodes_into_collection()."""
    im = get_index_manager()
    if im is None:
        return '{"merged": 0, "inserted": 0, "conflicted": 0}'
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            _executor,
            lambda: im.merge_nodes_into_collection(collection_id, new_nodes_json, strategy),
        )
    except Exception as e:
        logger.error(f"Rust merge_nodes error: {e}")
        return '{"merged": 0, "inserted": 0, "conflicted": 0}'


async def rust_merge_edges_async(collection_id: str, new_edges_json: str, strategy: str) -> str:
    """Async wrapper for IndexManager.merge_edges_into_collection()."""
    im = get_index_manager()
    if im is None:
        return '{"merged": 0, "inserted": 0, "conflicted": 0}'
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            _executor,
            lambda: im.merge_edges_into_collection(collection_id, new_edges_json, strategy),
        )
    except Exception as e:
        logger.error(f"Rust merge_edges error: {e}")
        return '{"merged": 0, "inserted": 0, "conflicted": 0}'
