"""Topics router — collection topic listing."""

from fastapi import APIRouter, Depends, HTTPException, Query
from app.auth.middleware import get_current_user
from app.db.lancedb_client import get_collection, list_topics, list_graph_nodes
from app.models.schemas import TopicResponse, TopicListResponse

router = APIRouter()


async def _require_access(collection_id: str, current_user: dict):
    collection = await get_collection(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    if collection.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")


@router.get("", response_model=TopicListResponse)
async def list_topics_endpoint(
    collection_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    await _require_access(collection_id, current_user)

    # Topics can come from dedicated topic table or be derived from node topics
    rows = await list_topics(collection_id)

    if not rows:
        # Derive topics from node topic lists
        nodes = await list_graph_nodes(collection_id)
        topic_counts: dict[str, int] = {}
        for n in nodes:
            for t in (n.get("topics") or []):
                topic_counts[t] = topic_counts.get(t, 0) + 1
        topics = [
            TopicResponse(
                id=name.lower().replace(" ", "_"),
                collection_id=collection_id,
                name=name,
                node_count=count,
            )
            for name, count in sorted(topic_counts.items(), key=lambda x: -x[1])
        ]
        return TopicListResponse(topics=topics, total=len(topics))

    topics = [
        TopicResponse(
            id=r.get("id", ""),
            collection_id=r.get("collection_id", collection_id),
            name=r.get("name", ""),
            node_count=r.get("node_count", 0),
            chunk_count=r.get("chunk_count", 0),
        )
        for r in rows
    ]
    return TopicListResponse(topics=topics, total=len(topics))


@router.get("/{topic_id}/nodes")
async def get_topic_nodes(
    topic_id: str,
    collection_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    await _require_access(collection_id, current_user)

    nodes = await list_graph_nodes(collection_id)
    # topic_id is name.lower().replace(" ", "_")
    topic_name = topic_id.replace("_", " ")
    matching = [
        n for n in nodes
        if topic_name.lower() in [t.lower() for t in (n.get("topics") or [])]
        or topic_id.lower() in [t.lower().replace(" ", "_") for t in (n.get("topics") or [])]
    ]
    return {"topic_id": topic_id, "nodes": matching, "total": len(matching)}
