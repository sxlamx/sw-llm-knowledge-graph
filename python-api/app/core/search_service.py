"""Search service — hybrid search orchestration."""

from typing import Optional
import logging

from app.llm.embedder import embed_texts
from app.core.rust_bridge import rust_search_async, rust_keyword_search_async

logger = logging.getLogger(__name__)


async def hybrid_search(
    query: str,
    collection_ids: list[str],
    topics: list[str] = None,
    limit: int = 20,
    offset: int = 0,
    mode: str = "hybrid",
    weights: dict = None,
) -> list[dict]:
    if weights is None:
        weights = {"vector": 0.6, "keyword": 0.3, "graph": 0.1}

    if not collection_ids:
        return []

    if mode == "vector" or mode == "hybrid":
        return await vector_search(query, collection_ids, limit, offset)
    elif mode == "keyword":
        return await keyword_search_fallback(query, collection_ids, limit, offset)
    else:
        return []


async def vector_search(
    query: str,
    collection_ids: list[str],
    limit: int,
    offset: int,
) -> list[dict]:
    try:
        embeddings = await embed_texts([query])
        if not embeddings:
            return []

        embedding = embeddings[0]
        all_results = []

        for collection_id in collection_ids:
            results = await rust_search_async(collection_id, embedding, limit + offset)
            all_results.extend(results)

        all_results.sort(key=lambda r: r.get("vector_score", 0), reverse=True)

        return all_results[offset:offset + limit]
    except Exception as e:
        logger.error(f"Vector search error: {e}")
        return []


async def keyword_search_fallback(
    query: str,
    collection_ids: list[str],
    limit: int,
    offset: int,
) -> list[dict]:
    try:
        all_results = []
        for collection_id in collection_ids:
            results = await rust_keyword_search_async(collection_id, query, limit + offset)
            all_results.extend(results)

        all_results.sort(key=lambda r: r.get("keyword_score", 0), reverse=True)
        return all_results[offset:offset + limit]
    except Exception as e:
        logger.error(f"Keyword search error: {e}")
        return []
