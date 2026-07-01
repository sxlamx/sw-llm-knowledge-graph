"""Search service — 3-channel hybrid search (vector + keyword + graph proximity).

Three channels run concurrently via asyncio.gather with per-channel timeouts:
  - Vector (LanceDB ANN): 600ms timeout
  - Keyword (Tantivy BM25): 200ms timeout  (rust_keyword_search_async)
  - Graph (BFS proximity):  300ms timeout  (rust_bfs_proximity_async)

Graceful degradation: if a channel times out or errors, it returns empty results
and the other channels still contribute to the fused result.

Embedding cache: before calling embed_query(), the Rust IndexManager LRU cache
is checked via get_cached_embedding(). On cache miss, the embedding is stored
back via cache_embedding(). This avoids re-calling the embedding model for
repeated queries.
"""

import asyncio
import json
import logging
import time
from typing import Optional

from app.db.lancedb_client import vector_search
from app.llm.embedder import embed_query
from app.core.rust_bridge import (
    rust_keyword_search_async,
    rust_bfs_proximity_async,
    get_index_manager,
)

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS = {"vector": 0.6, "keyword": 0.3, "graph": 0.1}
VECTOR_TIMEOUT = 0.6    # 600ms
KEYWORD_TIMEOUT = 0.2   # 200ms
GRAPH_TIMEOUT = 0.3     # 300ms
OVERALL_TIMEOUT = 0.8   # 800ms P95 SLA
OVER_FETCH_FACTOR = 2


async def _get_embedding(query: str) -> list[float]:
    """Check the Rust IndexManager embedding cache first; fall back to embed_query."""
    im = get_index_manager()
    if im is not None:
        try:
            cached = im.get_cached_embedding(query)
            if cached:
                result = json.loads(cached)
                if isinstance(result, list) and len(result) > 0:
                    return result
        except Exception:
            pass

    embedding = await embed_query(query)
    if not embedding:
        return []

    if im is not None:
        try:
            im.cache_embedding(query, json.dumps(embedding))
        except Exception:
            pass

    return embedding


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
        topics: Optional topic filter (applied to all channels where possible)
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

    try:
        results = await asyncio.wait_for(
            _hybrid_search_inner(query, collection_ids, topics, limit, offset, mode, weights),
            timeout=OVERALL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning("Overall hybrid search timed out (%dms SLA exceeded)", int(OVERALL_TIMEOUT * 1000))
        return {"results": [], "total": 0, "offset": offset, "limit": limit,
                "latency_ms": int(OVERALL_TIMEOUT * 1000), "search_mode": mode}

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

async def _hybrid_search_inner(
    query: str,
    collection_ids: list[str],
    topics: Optional[list[str]],
    limit: int,
    offset: int,
    mode: str,
    weights: dict,
) -> list[dict]:
    """Inner hybrid search logic — wrapped by overall timeout in hybrid_search()."""
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
    return results


async def _vector_only(
    query: str,
    collection_ids: list[str],
    topics: Optional[list[str]],
    limit: int,
) -> list[dict]:
    """Pure vector search — no fusion."""
    try:
        embedding = await _get_embedding(query)
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
        embedding = await _get_embedding(query)
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
        embedding = await _get_embedding(query)
        if not embedding:
            return []
    except Exception as e:
        logger.error(f"Embedding error: {e}")
        return []

    vector_handle = asyncio.create_task(_vector_channel(query, collection_ids, topics, limit, embedding=embedding))
    keyword_handle = asyncio.create_task(_keyword_channel(query, collection_ids, limit, embedding=embedding))
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

    results = _fuse_results(vector_results, keyword_results, graph_results, weights)

    if topics:
        results = _post_filter_by_topics(results, topics)

    return results


async def _vector_channel(
    query: str,
    collection_ids: list[str],
    topics: Optional[list[str]],
    limit: int,
    embedding: Optional[list[float]] = None,
) -> list[dict]:
    """LanceDB ANN vector search with 600ms timeout."""
    try:
        if embedding is None:
            embedding = await _get_embedding(query)
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
    embedding: Optional[list[float]] = None,
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

    Algorithm (per spec section 6):
    1. Build a score map keyed by chunk_id
    2. Include ALL hits from all 3 channels (vector, keyword, graph)
    3. For each entry, compute: final_score = w.vector * v + w.keyword * k + w.graph * g
    4. Keyword-only and graph-only hits get 0 for missing channel scores
    5. Sort by final_score descending
    """
    wv = weights.get("vector", 0.6)
    wk = weights.get("keyword", 0.3)
    wg = weights.get("graph", 0.1)

    seen_chunk_ids: set[str] = set()
    score_map: dict[str, dict] = {}

    def _ensure_entry(chunk_id: str, source: dict) -> dict:
        if chunk_id not in score_map:
            score_map[chunk_id] = {
                "chunk_id": chunk_id,
                "doc_id": source.get("doc_id", ""),
                "text": source.get("text", ""),
                "contextual_text": source.get("contextual_text"),
                "vector_score": 0.0,
                "keyword_score": 0.0,
                "graph_proximity_score": 0.0,
                "final_score": 0.0,
                "page": source.get("page"),
                "topics": source.get("topics", []),
                "highlights": source.get("highlights", []),
            }
        return score_map[chunk_id]

    # Process vector results — these carry the richest metadata
    for r in vector_results:
        chunk_id = r.get("chunk_id") or r.get("id", "")
        if not chunk_id:
            continue
        seen_chunk_ids.add(chunk_id)
        entry = _ensure_entry(chunk_id, r)
        entry["vector_score"] = r.get("vector_score", 0)
        entry["doc_id"] = r.get("doc_id") or entry["doc_id"]
        entry["text"] = r.get("text") or entry["text"]
        entry["contextual_text"] = r.get("contextual_text", entry.get("contextual_text"))

    # Process keyword results — includes keyword-only hits not in vector results
    for r in keyword_results:
        chunk_id = r.get("chunk_id") or r.get("id", "")
        if not chunk_id:
            continue
        seen_chunk_ids.add(chunk_id)
        entry = _ensure_entry(chunk_id, r)
        entry["keyword_score"] = r.get("keyword_score", 0)
        kw_highlights = r.get("highlights", [])
        if kw_highlights:
            existing = entry.get("highlights", [])
            seen = set(existing)
            for h in kw_highlights:
                if h not in seen:
                    existing.append(h)
                    seen.add(h)
            entry["highlights"] = existing

    # Process graph results — includes graph-only hits not in other channels
    for r in graph_results:
        chunk_id = r.get("chunk_id") or r.get("id", "")
        if not chunk_id:
            continue
        seen_chunk_ids.add(chunk_id)
        entry = _ensure_entry(chunk_id, r)
        entry["graph_proximity_score"] = r.get("graph_proximity_score", 0)

    # Compute final scores
    results = []
    for entry in score_map.values():
        v = entry["vector_score"]
        k = entry["keyword_score"]
        g = entry["graph_proximity_score"]
        entry["final_score"] = wv * v + wk * k + wg * g
        results.append(entry)

    return results


def _post_filter_by_topics(results: list[dict], topics: list[str]) -> list[dict]:
    """Post-filter fused results by topics.

    Vector search applies topics as a LanceDB pre-filter. This function provides
    topic filtering for keyword-only and graph-only hits that don't carry topics
    metadata from their respective channels. Results WITHOUT a topics field are
    kept (optimistic — they came from keyword/graph channels which don't store topics).
    Results WITH topics that have NO overlap are removed.
    """
    if not topics:
        return results

    topic_set = {t.lower() for t in topics}
    filtered = []
    for r in results:
        r_topics = r.get("topics")
        if r_topics is None:
            filtered.append(r)
            continue
        r_topic_set = {t.lower() for t in r_topics} if r_topics else set()
        if r_topic_set & topic_set:
            filtered.append(r)
    return filtered
