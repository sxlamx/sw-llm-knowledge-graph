"""Search service — 3-channel hybrid search (vector + keyword + graph proximity).

Three channels run concurrently via asyncio.gather with per-channel timeouts:
  - Vector (LanceDB ANN): 600ms timeout
  - Keyword (Tantivy BM25): 200ms timeout  (rust_keyword_search_async)
  - Graph (BFS proximity):  300ms timeout  (rust_bfs_proximity_async)

Graceful degradation: if a channel times out or errors, it returns empty results
and the other channels still contribute to the fused result.
"""

import asyncio
import logging
import time
from typing import Optional

from app.db.lancedb_client import vector_search
from app.llm.embedder import embed_query
from app.core.rust_bridge import (
    rust_search_async,
    rust_keyword_search_async,
    rust_bfs_proximity_async,
    get_index_manager,
)

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS = {"vector": 0.6, "keyword": 0.3, "graph": 0.1}
VECTOR_TIMEOUT = 0.6    # 600ms
KEYWORD_TIMEOUT = 0.2   # 200ms
GRAPH_TIMEOUT = 0.3     # 300ms
OVER_FETCH_FACTOR = 2


async def hybrid_search(
    query: str,
    collection_ids: list[str],
    topics: Optional[list[str]] = None,
    limit: int = 20,
    offset: int = 0,
    mode: str = "hybrid",
    weights: Optional[dict] = None,
) -> dict:
    """Run hybrid search across 3 concurrent channels and fuse scores.

    Args:
        query: Natural language query string
        collection_ids: List of collection UUIDs to search
        topics: Optional topic filter (applied to vector channel)
        limit: Max results to return
        offset: Pagination offset
        mode: "hybrid" | "vector" | "keyword" | "graph"
        weights: Score fusion weights dict, e.g. {"vector": 0.6, "keyword": 0.3, "graph": 0.1}

    Returns:
        dict with keys: results, total, offset, limit, latency_ms, search_mode
    """
    start = time.monotonic()
    if weights is None:
        weights = DEFAULT_WEIGHTS

    if not collection_ids:
        return {"results": [], "total": 0, "offset": offset, "limit": limit, "latency_ms": 0, "search_mode": mode}

    over_fetch = limit * OVER_FETCH_FACTOR

    if mode == "vector":
        results = await _vector_only(query, collection_ids, topics, over_fetch)
    elif mode == "keyword":
        results = await _keyword_only(query, collection_ids, over_fetch)
    elif mode == "graph":
        results = await _graph_only(query, collection_ids, over_fetch)
    else:  # hybrid
        results = await _hybrid_3channel(query, collection_ids, topics, over_fetch, weights)

    # Sort by final_score descending
    results.sort(key=lambda r: r.get("final_score", 0), reverse=True)
    total = len(results)
    page = results[offset:offset + limit]
    latency_ms = int((time.monotonic() - start) * 1000)

    return {
        "results": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "latency_ms": latency_ms,
        "search_mode": mode,
    }


async def _vector_only(
    query: str,
    collection_ids: list[str],
    topics: Optional[list[str]],
    limit: int,
) -> list[dict]:
    """Pure vector search — no fusion."""
    try:
        embedding = await embed_query(query)
        if not embedding:
            return []

        all_results = []
        for cid in collection_ids:
            channel_results = await asyncio.wait_for(
                vector_search(cid, embedding, limit=limit, topics=topics),
                timeout=VECTOR_TIMEOUT,
            )
            for r in channel_results:
                r["final_score"] = r.get("vector_score", 0)
                r["keyword_score"] = 0.0
                r["graph_proximity_score"] = 0.0
            all_results.extend(channel_results)
        return all_results
    except asyncio.TimeoutError:
        logger.warning("Vector channel timed out")
        return []
    except Exception as e:
        logger.error(f"Vector channel error: {e}")
        return []


async def _keyword_only(
    query: str,
    collection_ids: list[str],
    limit: int,
) -> list[dict]:
    """Pure keyword search (BM25 via Tantivy)."""
    try:
        all_results = []
        for cid in collection_ids:
            channel_results = await asyncio.wait_for(
                rust_keyword_search_async(cid, query, limit),
                timeout=KEYWORD_TIMEOUT,
            )
            for r in channel_results:
                r["final_score"] = r.get("keyword_score", 0)
                r["vector_score"] = 0.0
                r["graph_proximity_score"] = 0.0
            all_results.extend(channel_results)
        return all_results
    except asyncio.TimeoutError:
        logger.warning("Keyword channel timed out")
        return []
    except Exception as e:
        logger.error(f"Keyword channel error: {e}")
        return []


async def _graph_only(
    query: str,
    collection_ids: list[str],
    limit: int,
) -> list[dict]:
    """Graph-only traversal from query entity match."""
    try:
        embedding = await embed_query(query)
        if not embedding:
            return []

        all_results = []
        for cid in collection_ids:
            channel_results = await asyncio.wait_for(
                rust_bfs_proximity_async(cid, embedding, limit),
                timeout=GRAPH_TIMEOUT,
            )
            for r in channel_results:
                r["final_score"] = r.get("graph_proximity_score", 0)
                r["vector_score"] = 0.0
                r["keyword_score"] = 0.0
            all_results.extend(channel_results)
        return all_results
    except asyncio.TimeoutError:
        logger.warning("Graph channel timed out")
        return []
    except Exception as e:
        logger.error(f"Graph channel error: {e}")
        return []


async def _hybrid_3channel(
    query: str,
    collection_ids: list[str],
    topics: Optional[list[str]],
    limit: int,
    weights: dict,
) -> list[dict]:
    """Run all 3 channels concurrently and fuse their scores."""
    try:
        embedding = await embed_query(query)
        if not embedding:
            return []
    except Exception as e:
        logger.error(f"Embedding error: {e}")
        return []

    vector_handle = asyncio.create_task(_vector_channel(query, collection_ids, topics, limit))
    keyword_handle = asyncio.create_task(_keyword_channel(query, collection_ids, limit))
    graph_handle = asyncio.create_task(_graph_channel(embedding, collection_ids, limit))

    try:
        vector_results, keyword_results, graph_results = await asyncio.gather(
            vector_handle,
            keyword_handle,
            graph_handle,
            return_exceptions=True,
        )
    except Exception as e:
        logger.error(f"Channel gather error: {e}")
        vector_results = []
        keyword_results = []
        graph_results = []

    if isinstance(vector_results, Exception):
        logger.warning(f"Vector channel exception: {vector_results}")
        vector_results = []
    if isinstance(keyword_results, Exception):
        logger.warning(f"Keyword channel exception: {keyword_results}")
        keyword_results = []
    if isinstance(graph_results, Exception):
        logger.warning(f"Graph channel exception: {graph_results}")
        graph_results = []

    return _fuse_results(vector_results, keyword_results, graph_results, weights)


async def _vector_channel(
    query: str,
    collection_ids: list[str],
    topics: Optional[list[str]],
    limit: int,
) -> list[dict]:
    """LanceDB ANN vector search with 600ms timeout."""
    try:
        embedding = await embed_query(query)
        if not embedding:
            return []

        all_results = []
        for cid in collection_ids:
            try:
                results = await asyncio.wait_for(
                    vector_search(cid, embedding, limit=limit, topics=topics),
                    timeout=VECTOR_TIMEOUT,
                )
                all_results.extend(results)
            except asyncio.TimeoutError:
                logger.warning(f"Vector channel timed out for collection {cid}")
            except Exception as e:
                logger.error(f"Vector channel error for collection {cid}: {e}")
        return all_results
    except Exception as e:
        logger.error(f"Vector channel setup error: {e}")
        return []


async def _keyword_channel(
    query: str,
    collection_ids: list[str],
    limit: int,
) -> list[dict]:
    """Tantivy BM25 keyword search with 200ms timeout."""
    all_results = []
    for cid in collection_ids:
        try:
            results = await asyncio.wait_for(
                rust_keyword_search_async(cid, query, limit),
                timeout=KEYWORD_TIMEOUT,
            )
            all_results.extend(results)
        except asyncio.TimeoutError:
            logger.warning(f"Keyword channel timed out for collection {cid}")
        except Exception as e:
            logger.error(f"Keyword channel error for collection {cid}: {e}")
    return all_results


async def _graph_channel(
    embedding: Optional[list[float]],
    collection_ids: list[str],
    limit: int,
) -> list[dict]:
    """Graph BFS proximity search with 300ms timeout."""
    if not embedding:
        return []
    all_results = []
    for cid in collection_ids:
        try:
            results = await asyncio.wait_for(
                rust_bfs_proximity_async(cid, embedding, limit),
                timeout=GRAPH_TIMEOUT,
            )
            all_results.extend(results)
        except asyncio.TimeoutError:
            logger.warning(f"Graph channel timed out for collection {cid}")
        except Exception as e:
            logger.error(f"Graph channel error for collection {cid}: {e}")
    return all_results


def _fuse_results(
    vector_results: list[dict],
    keyword_results: list[dict],
    graph_results: list[dict],
    weights: dict,
) -> list[dict]:
    """Fuse scores from 3 channels into final_score.

    Algorithm:
    1. Build a score map keyed by chunk_id
    2. For each channel result, update the corresponding entry
    3. final_score = w.vector * v + w.keyword * k + w.graph * g
    4. Sort by final_score descending
    """
    wv = weights.get("vector", 0.6)
    wk = weights.get("keyword", 0.3)
    wg = weights.get("graph", 0.1)

    score_map: dict[str, dict] = {}

    # Process vector results
    for r in vector_results:
        chunk_id = r.get("chunk_id") or r.get("id", "")
        if not chunk_id:
            continue
        if chunk_id not in score_map:
            score_map[chunk_id] = {
                "chunk_id": chunk_id,
                "doc_id": r.get("doc_id", ""),
                "text": r.get("text", ""),
                "contextual_text": r.get("contextual_text"),
                "vector_score": 0.0,
                "keyword_score": 0.0,
                "graph_proximity_score": 0.0,
                "final_score": 0.0,
                "page": r.get("page"),
                "topics": r.get("topics", []),
            }
        score_map[chunk_id]["vector_score"] = r.get("vector_score", 0)
        score_map[chunk_id]["doc_id"] = r.get("doc_id", score_map[chunk_id]["doc_id"])
        score_map[chunk_id]["text"] = r.get("text", score_map[chunk_id]["text"])
        score_map[chunk_id]["contextual_text"] = r.get("contextual_text")

    # Process keyword results
    for r in keyword_results:
        chunk_id = r.get("chunk_id") or r.get("id", "")
        if not chunk_id:
            continue
        if chunk_id not in score_map:
            score_map[chunk_id] = {
                "chunk_id": chunk_id,
                "doc_id": r.get("doc_id", ""),
                "text": r.get("text", ""),
                "contextual_text": r.get("contextual_text"),
                "vector_score": 0.0,
                "keyword_score": 0.0,
                "graph_proximity_score": 0.0,
                "final_score": 0.0,
                "page": r.get("page"),
                "topics": r.get("topics", []),
            }
        score_map[chunk_id]["keyword_score"] = r.get("keyword_score", 0)

    # Process graph results
    for r in graph_results:
        chunk_id = r.get("chunk_id") or r.get("id", "")
        if not chunk_id:
            continue
        if chunk_id not in score_map:
            score_map[chunk_id] = {
                "chunk_id": chunk_id,
                "doc_id": r.get("doc_id", ""),
                "text": r.get("text", ""),
                "contextual_text": r.get("contextual_text"),
                "vector_score": 0.0,
                "keyword_score": 0.0,
                "graph_proximity_score": 0.0,
                "final_score": 0.0,
                "page": r.get("page"),
                "topics": r.get("topics", []),
            }
        score_map[chunk_id]["graph_proximity_score"] = r.get("graph_proximity_score", 0)

    # Compute final scores
    results = []
    for entry in score_map.values():
        v = entry["vector_score"]
        k = entry["keyword_score"]
        g = entry["graph_proximity_score"]
        entry["final_score"] = wv * v + wk * k + wg * g
        results.append(entry)

    return results
