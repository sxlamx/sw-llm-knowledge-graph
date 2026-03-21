"""Agent-based Graph RAG router — ReAct loop with multi-hop graph traversal."""

import json
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.middleware import get_current_user
from app.db.lancedb_client import get_collection

router = APIRouter()
logger = logging.getLogger(__name__)


class AgentQueryRequest(BaseModel):
    collection_id: str
    query: str = Field(..., min_length=1, max_length=2000)
    max_hops: int = Field(4, ge=1, le=6)


@router.post("/query")
async def agent_query(
    body: AgentQueryRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Run a ReAct-style graph RAG agent over the collection.

    Returns a Server-Sent Events stream with events of the following types:
    - `start`       — query accepted, agent starting
    - `thought`     — agent's internal reasoning step
    - `observation` — result after an action (chunk retrieval / graph hop)
    - `token`       — streaming token of the final answer
    - `answer`      — complete final answer with metadata
    - `error`       — fatal error during agent execution
    """
    collection = await get_collection(body.collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    if collection.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    from app.services.agent_service import run_agent

    async def event_stream():
        try:
            async for event in run_agent(
                collection_id=body.collection_id,
                query=body.query,
                max_hops=body.max_hops,
            ):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            logger.error(f"Agent error for collection {body.collection_id}: {exc}")
            error_event = {"type": "error", "content": str(exc)}
            yield f"data: {json.dumps(error_event)}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/status")
async def agent_status(
    collection_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Health check: returns whether the collection is ready for agent queries."""
    collection = await get_collection(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    if collection.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    from app.db.lancedb_client import list_graph_nodes, list_graph_edges

    nodes = await list_graph_nodes(collection_id)
    edges = await list_graph_edges(collection_id)

    return {
        "collection_id": collection_id,
        "ready": len(nodes) > 0,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "max_hops": 6,
    }
