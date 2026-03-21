"""Graph analytics router — PageRank, betweenness centrality, community detection."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth.middleware import get_current_user
from app.db.lancedb_client import get_collection, list_graph_nodes, list_graph_edges
from app.services.analytics_service import pagerank, betweenness_centrality, louvain_communities

router = APIRouter()


class NodeScore(BaseModel):
    node_id: str
    label: str
    score: float


class AnalyticsResponse(BaseModel):
    collection_id: str
    metric: str
    scores: list[NodeScore]
    communities: dict[str, str] = {}


async def _require_access(collection_id: str, current_user: dict) -> None:
    collection = await get_collection(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    if collection.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")


@router.get("/pagerank", response_model=AnalyticsResponse)
async def get_pagerank(
    collection_id: str = Query(...),
    top_k: int = Query(50, le=500),
    current_user: dict = Depends(get_current_user),
):
    await _require_access(collection_id, current_user)
    nodes = await list_graph_nodes(collection_id)
    edges = await list_graph_edges(collection_id)

    scores = pagerank(nodes, edges)
    node_labels = {n["id"]: n.get("label", n["id"]) for n in nodes}

    sorted_scores = sorted(scores.items(), key=lambda x: -x[1])[:top_k]
    return AnalyticsResponse(
        collection_id=collection_id,
        metric="pagerank",
        scores=[
            NodeScore(node_id=nid, label=node_labels.get(nid, nid), score=round(s, 6))
            for nid, s in sorted_scores
        ],
    )


@router.get("/betweenness", response_model=AnalyticsResponse)
async def get_betweenness(
    collection_id: str = Query(...),
    top_k: int = Query(50, le=500),
    current_user: dict = Depends(get_current_user),
):
    await _require_access(collection_id, current_user)
    nodes = await list_graph_nodes(collection_id)
    edges = await list_graph_edges(collection_id)

    if len(nodes) > 500:
        # Limit for performance
        nodes = nodes[:500]

    scores = betweenness_centrality(nodes, edges)
    node_labels = {n["id"]: n.get("label", n["id"]) for n in nodes}

    sorted_scores = sorted(scores.items(), key=lambda x: -x[1])[:top_k]
    return AnalyticsResponse(
        collection_id=collection_id,
        metric="betweenness_centrality",
        scores=[
            NodeScore(node_id=nid, label=node_labels.get(nid, nid), score=round(s, 6))
            for nid, s in sorted_scores
        ],
    )


@router.get("/communities", response_model=AnalyticsResponse)
async def get_communities(
    collection_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    await _require_access(collection_id, current_user)
    nodes = await list_graph_nodes(collection_id)
    edges = await list_graph_edges(collection_id)

    communities = louvain_communities(nodes, edges)
    node_labels = {n["id"]: n.get("label", n["id"]) for n in nodes}

    # Count members per community
    comm_counts: dict[str, int] = {}
    for c in communities.values():
        comm_counts[c] = comm_counts.get(c, 0) + 1

    # Score = fraction of total nodes in same community (relative size)
    total = max(len(nodes), 1)
    scores = [
        NodeScore(
            node_id=nid,
            label=node_labels.get(nid, nid),
            score=round(comm_counts.get(communities[nid], 0) / total, 4),
        )
        for nid in communities
    ]
    scores.sort(key=lambda x: (communities[x.node_id], -x.score))

    return AnalyticsResponse(
        collection_id=collection_id,
        metric="communities",
        scores=scores,
        communities=communities,
    )


@router.get("/summary")
async def analytics_summary(
    collection_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Quick summary: top-5 PageRank, top-5 betweenness, community count."""
    await _require_access(collection_id, current_user)
    nodes = await list_graph_nodes(collection_id)
    edges = await list_graph_edges(collection_id)

    node_labels = {n["id"]: n.get("label", n["id"]) for n in nodes}

    pr = pagerank(nodes, edges)
    top_pr = sorted(pr.items(), key=lambda x: -x[1])[:5]

    bc_nodes = nodes[:200]  # cap for speed
    bc = betweenness_centrality(bc_nodes, edges)
    top_bc = sorted(bc.items(), key=lambda x: -x[1])[:5]

    comms = louvain_communities(nodes, edges)
    num_communities = len(set(comms.values()))

    return {
        "collection_id": collection_id,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "num_communities": num_communities,
        "top_pagerank": [{"id": nid, "label": node_labels.get(nid), "score": round(s, 4)} for nid, s in top_pr],
        "top_betweenness": [{"id": nid, "label": node_labels.get(nid), "score": round(s, 4)} for nid, s in top_bc],
    }
