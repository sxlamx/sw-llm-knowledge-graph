"""Search router."""

from fastapi import APIRouter, Depends, Query
from app.auth.middleware import get_current_user
from app.models.schemas import (
    SearchRequest, SearchResponse, SearchResultItem, SuggestionResponse,
)
from app.llm.embedder import embed_texts
from app.core.rust_bridge import get_index_manager
from app.core.search_service import hybrid_search
from app.core.metrics import KG_SEARCH_REQUESTS_TOTAL, KG_SEARCH_LATENCY
import time
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    current_user: dict = Depends(get_current_user),
):
    start = time.time()

    try:
        results = await hybrid_search(
            query=body.query,
            collection_ids=body.collection_ids,
            topics=body.topics,
            limit=body.limit,
            offset=body.offset,
            mode=body.mode,
            weights=body.weights,
        )
    except Exception as e:
        logger.error(f"Search error: {e}")
        results = []

    elapsed = time.time() - start
    elapsed_ms = int(elapsed * 1000)
    KG_SEARCH_REQUESTS_TOTAL.labels(mode=body.mode).inc()
    KG_SEARCH_LATENCY.observe(elapsed)

    return SearchResponse(
        results=[
            SearchResultItem(
                chunk_id=r.get("chunk_id", ""),
                doc_id=r.get("doc_id", ""),
                doc_title=r.get("doc_title"),
                text=r.get("text", ""),
                page=r.get("page"),
                vector_score=r.get("vector_score", 0.0),
                keyword_score=r.get("keyword_score", 0.0),
                graph_proximity_score=r.get("graph_proximity_score", 0.0),
                final_score=r.get("final_score", 0.0),
                topics=r.get("topics", []),
                highlights=r.get("highlights", []),
            )
            for r in results
        ],
        total=len(results),
        offset=body.offset,
        limit=body.limit,
        latency_ms=elapsed_ms,
        search_mode=body.mode,
    )


@router.get("/suggestions", response_model=SuggestionResponse)
async def get_suggestions(
    q: str = Query(..., min_length=2),
    collection_id: str | None = None,
    limit: int = Query(10, le=50),
    current_user: dict = Depends(get_current_user),
):
    if len(q) < 2:
        return SuggestionResponse(suggestions=[])

    suggestions = [
        f"{q} applications",
        f"{q} implementation",
        f"{q} architecture",
        f"{q} research",
        f"{q} overview",
    ]
    return SuggestionResponse(suggestions=suggestions[:limit])
