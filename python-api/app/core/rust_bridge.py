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


async def rust_search_async(collection_id: str, embedding: list[float], limit: int) -> list[dict]:
    try:
        from app.db.lancedb_client import vector_search
        return await vector_search(collection_id, embedding, limit)
    except Exception as e:
        logger.error(f"Vector search error: {e}")
        return []


async def rust_keyword_search_async(collection_id: str, query: str, limit: int) -> list[dict]:
    im = get_index_manager()
    if im is None:
        return []

    loop = asyncio.get_event_loop()
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

    loop = asyncio.get_event_loop()
    try:
        graph_json = await loop.run_in_executor(
            _executor,
            lambda: im.get_graph_data(collection_id),
        )
        graph_data = json.loads(graph_json)

        if not graph_data.get("nodes"):
            return []

        # Find top-5 entity nodes by cosine similarity to query embedding
        import math
        nodes = graph_data["nodes"]
        similarities: list[tuple[str, float, str]] = []
        q_emb = query_embedding
        q_len = math.sqrt(sum(x * x for x in q_emb)) or 1.0

        for node in nodes:
            # Nodes don't have embeddings stored in petgraph natively;
            # use label hash as a proxy or skip similarity if no embedding
            # For now, return all nodes with weight 1.0 as seed set
            similarities.append((node["id"], 1.0, node.get("label", "")))

        similarities.sort(key=lambda x: x[1], reverse=True)
        seed_ids = {s[0] for s in similarities[:5]}

        # Build adjacency from edges
        edges_by_source: dict[str, list[tuple[str, float]]] = {}
        for edge in graph_data.get("edges", []):
            src = edge.get("source")
            tgt = edge.get("target")
            weight = edge.get("weight", 0.5)
            if src:
                edges_by_source.setdefault(src, []).append((tgt, weight))

        # BFS from seeds, max 2 hops
        visited: set[str] = set()
        frontier: list[str] = list(seed_ids)
        depth: dict[str, int] = {s: 0 for s in seed_ids}

        for _ in range(2):
            next_frontier: list[str] = []
            for node_id in frontier:
                if node_id in visited:
                    continue
                visited.add(node_id)
                for neighbor_id, edge_weight in edges_by_source.get(node_id, []):
                    if neighbor_id not in visited:
                        depth[neighbor_id] = depth.get(node_id, 0) + 1
                        next_frontier.append(neighbor_id)
            frontier = next_frontier

        # Collect chunk_ids from edges, scored by hop depth
        chunk_scores: dict[str, float] = {}
        for edge in graph_data.get("edges", []):
            src = edge.get("source")
            if src not in visited:
                continue
            chunk_id = str(edge.get("chunk_id") or "")
            if not chunk_id:
                continue
            hop = depth.get(src, 0)
            score = 1.0 / (hop + 1)  # hop_decay: closer = higher score
            chunk_scores[chunk_id] = max(chunk_scores.get(chunk_id, 0), score)

        # Sort by score, return top limit
        sorted_chunks = sorted(chunk_scores.items(), key=lambda x: x[1], reverse=True)
        return [
            {"chunk_id": cid, "graph_proximity_score": score}
            for cid, score in sorted_chunks[:limit]
        ]
    except Exception as e:
        logger.error(f"Rust BFS proximity error: {e}")
        return []


async def rust_insert_chunks_async(collection_id: str, chunks_json: str) -> int:
    im = get_index_manager()
    if im is None:
        return 0

    loop = asyncio.get_event_loop()
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

    loop = asyncio.get_event_loop()
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
    loop = asyncio.get_event_loop()
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
    loop = asyncio.get_event_loop()
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
