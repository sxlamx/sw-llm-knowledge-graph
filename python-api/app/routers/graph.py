"""Graph router — knowledge graph CRUD and traversal."""

import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from app.auth.middleware import get_current_user
from app.db.lancedb_client import (
    get_collection,
    list_graph_nodes,
    get_graph_node,
    upsert_graph_node,
    update_graph_node,
    list_graph_edges,
    get_graph_edge,
    upsert_graph_edge,
    delete_graph_edge,
    list_documents,
    insert_user_feedback,
)
from app.core.rust_bridge import get_index_manager
from app.models.schemas import (
    GraphDataResponse, GraphNodeResponse, GraphNodeDetailResponse,
    GraphEdgeResponse, GraphPathResponse, LinkedChunk,
    UpdateGraphNodeRequest, CreateGraphEdgeRequest, UserFeedbackCreate,
)

router = APIRouter()


def _node_to_response(n: dict) -> GraphNodeResponse:
    return GraphNodeResponse(
        id=n.get("id", ""),
        label=n.get("label", ""),
        entity_type=n.get("entity_type", n.get("node_type", "Concept")),
        description=n.get("description"),
        confidence=float(n.get("confidence", 1.0)),
        properties=n.get("properties") or {},
        source_chunk_ids=n.get("source_chunk_ids") or [],
        topics=n.get("topics") or [],
        collection_id=n.get("collection_id"),
    )


def _edge_to_response(e: dict) -> GraphEdgeResponse:
    return GraphEdgeResponse(
        id=e.get("id", ""),
        source=e.get("source", e.get("source_id", "")),
        target=e.get("target", e.get("target_id", "")),
        relation_type=e.get("relation_type", e.get("edge_type", e.get("predicate", ""))),
        weight=float(e.get("weight", 1.0)),
        properties=e.get("properties") or {},
        collection_id=e.get("collection_id"),
    )


async def _require_collection_access(collection_id: str, current_user: dict) -> dict:
    collection = await get_collection(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    if collection.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    return collection


@router.get("/subgraph", response_model=GraphDataResponse)
async def get_subgraph(
    collection_id: str,
    depth: int = Query(2, ge=1, le=4),
    page: int = Query(0, ge=0),
    limit: int = Query(500, le=2000),
    current_user: dict = Depends(get_current_user),
):
    await _require_collection_access(collection_id, current_user)

    # Try in-memory Rust graph first
    im = get_index_manager()
    if im is not None:
        try:
            data_json = im.get_graph_data(collection_id)
            data = json.loads(data_json)
            if data.get("total_nodes", 0) > 0:
                offset = page * limit
                nodes = data["nodes"][offset: offset + limit]
                node_ids = {n.get("id", n.get("id")) for n in nodes}
                edges = [
                    e for e in data["edges"]
                    if e.get("source") in node_ids and e.get("target") in node_ids
                ][:limit]
                return GraphDataResponse(
                    nodes=[_node_to_response(n) for n in nodes],
                    edges=[_edge_to_response(e) for e in edges],
                    total_nodes=data.get("total_nodes", len(nodes)),
                    total_edges=data.get("total_edges", len(edges)),
                )
        except Exception:
            pass

    # Fall back to LanceDB
    offset = page * limit
    all_nodes = await list_graph_nodes(collection_id)
    all_edges = await list_graph_edges(collection_id)

    node_page = all_nodes[offset: offset + limit]
    node_ids = {n["id"] for n in node_page}
    edge_page = [
        e for e in all_edges
        if e.get("source", e.get("source_id")) in node_ids
        and e.get("target", e.get("target_id")) in node_ids
    ]

    return GraphDataResponse(
        nodes=[_node_to_response(n) for n in node_page],
        edges=[_edge_to_response(e) for e in edge_page],
        total_nodes=len(all_nodes),
        total_edges=len(all_edges),
    )


@router.get("/nodes/{node_id}", response_model=GraphNodeDetailResponse)
async def get_node_detail(
    node_id: str,
    collection_id: str,
    depth: int = Query(1, ge=1, le=3),
    current_user: dict = Depends(get_current_user),
):
    await _require_collection_access(collection_id, current_user)

    node = await get_graph_node(collection_id, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    # Find linked source chunks
    linked_chunks: list[LinkedChunk] = []
    chunk_ids = node.get("source_chunk_ids") or []
    if chunk_ids:
        from app.db.lancedb_client import get_lancedb
        db = await get_lancedb()
        try:
            tbl = db.open_table(f"{collection_id}_chunks")
            # Fetch doc metadata for titles
            docs = {d["id"]: d for d in await list_documents(collection_id)}
            for cid in chunk_ids[:10]:
                rows = tbl.query().where(f'id = "{cid}"').to_list()
                if rows:
                    c = rows[0]
                    doc = docs.get(c.get("doc_id", ""), {})
                    linked_chunks.append(LinkedChunk(
                        chunk_id=c.get("id", cid),
                        doc_id=c.get("doc_id", ""),
                        doc_title=doc.get("title", c.get("doc_id", "")),
                        text=c.get("text", ""),
                        page=c.get("page"),
                    ))
        except Exception:
            pass

    # Collect 1-hop neighbors
    all_edges = await list_graph_edges(collection_id)
    neighbor_ids = set()
    for e in all_edges:
        src = e.get("source", e.get("source_id"))
        tgt = e.get("target", e.get("target_id"))
        if src == node_id:
            neighbor_ids.add(tgt)
        elif tgt == node_id:
            neighbor_ids.add(src)

    neighbors: list[GraphNodeResponse] = []
    for nid in list(neighbor_ids)[:20]:
        nb = await get_graph_node(collection_id, nid)
        if nb:
            neighbors.append(_node_to_response(nb))

    base = _node_to_response(node)
    return GraphNodeDetailResponse(
        **base.model_dump(),
        linked_chunks=linked_chunks,
        neighbors=neighbors,
    )


@router.get("/path", response_model=GraphPathResponse)
async def get_path(
    start_id: str,
    end_id: str,
    collection_id: str,
    max_hops: int = Query(10, ge=2, le=20),
    current_user: dict = Depends(get_current_user),
):
    await _require_collection_access(collection_id, current_user)

    all_nodes = await list_graph_nodes(collection_id)
    all_edges = await list_graph_edges(collection_id)

    node_map = {n["id"]: n for n in all_nodes}

    # Try Rust path-finding if graph is loaded
    try:
        from app.core.rust_bridge import get_index_manager
        from rust_core import check_shortest_path
        im = get_index_manager()
        if im is not None:
            graph_json = im.get_graph_data(collection_id)
            path = check_shortest_path(graph_json, start_id, end_id)
            if path:
                path_nodes = [_node_to_response(node_map[nid]) for nid in path if nid in node_map]
                path_edges = [
                    _edge_to_response(e) for e in all_edges
                    if e.get("source", e.get("source_id")) in path
                    and e.get("target", e.get("target_id")) in path
                ]
                return GraphPathResponse(path=path, nodes=path_nodes, edges=path_edges)
    except Exception:
        pass

    # BFS fallback
    from collections import deque
    adj: dict[str, list[str]] = {}
    edge_map: dict[tuple, dict] = {}
    for e in all_edges:
        src = e.get("source", e.get("source_id", ""))
        tgt = e.get("target", e.get("target_id", ""))
        adj.setdefault(src, []).append(tgt)
        adj.setdefault(tgt, []).append(src)
        edge_map[(src, tgt)] = e
        edge_map[(tgt, src)] = e

    visited: dict[str, Optional[str]] = {start_id: None}
    queue: deque[str] = deque([start_id])
    found = False

    while queue and not found:
        curr = queue.popleft()
        for nb in adj.get(curr, []):
            if nb not in visited:
                visited[nb] = curr
                if nb == end_id:
                    found = True
                    break
                queue.append(nb)

    if not found:
        raise HTTPException(status_code=404, detail="No path found between nodes")

    path: list[str] = []
    cur: Optional[str] = end_id
    while cur is not None:
        path.append(cur)
        cur = visited.get(cur)
    path.reverse()

    path_nodes = [_node_to_response(node_map[nid]) for nid in path if nid in node_map]
    path_edges = [
        _edge_to_response(e)
        for i in range(len(path) - 1)
        for e in [edge_map.get((path[i], path[i + 1])) or edge_map.get((path[i + 1], path[i]))]
        if e
    ]

    return GraphPathResponse(path=path, nodes=path_nodes, edges=path_edges)


@router.put("/nodes/{node_id}", response_model=GraphNodeResponse)
async def update_node(
    node_id: str,
    collection_id: str,
    body: UpdateGraphNodeRequest,
    current_user: dict = Depends(get_current_user),
):
    await _require_collection_access(collection_id, current_user)

    node = await get_graph_node(collection_id, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    before = dict(node)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    updated = await update_graph_node(collection_id, node_id, updates)

    # Update Rust in-memory graph
    im = get_index_manager()
    if im and updated:
        try:
            im.update_node(collection_id, json.dumps(updated))
        except Exception:
            pass

    # Record feedback
    await insert_user_feedback({
        "id": str(uuid.uuid4()),
        "collection_id": collection_id,
        "user_id": current_user["id"],
        "entity_type": "node_edit",
        "target_id": node_id,
        "action": "update",
        "before": json.dumps(before),
        "after": json.dumps(updated or {}),
    })

    return _node_to_response(updated or node)


@router.post("/edges", response_model=GraphEdgeResponse, status_code=201)
async def create_edge(
    body: CreateGraphEdgeRequest,
    current_user: dict = Depends(get_current_user),
):
    await _require_collection_access(body.collection_id, current_user)

    edge = {
        "id": str(uuid.uuid4()),
        "collection_id": body.collection_id,
        "source": body.source,
        "source_id": body.source,
        "target": body.target,
        "target_id": body.target,
        "relation_type": body.relation_type,
        "edge_type": body.relation_type,
        "weight": body.weight,
        "properties": body.properties,
    }
    await upsert_graph_edge(body.collection_id, edge)

    im = get_index_manager()
    if im:
        try:
            rust_edge = {
                "id": edge["id"],
                "source": edge["source"],
                "target": edge["target"],
                "edge_type": {"custom": body.relation_type},
                "weight": body.weight,
                "context": None,
                "chunk_id": None,
                "properties": {},
                "collection_id": body.collection_id,
            }
            im.upsert_edges(body.collection_id, json.dumps([rust_edge]))
        except Exception:
            pass

    return _edge_to_response(edge)


@router.delete("/edges/{edge_id}", status_code=204)
async def remove_edge(
    edge_id: str,
    collection_id: str,
    current_user: dict = Depends(get_current_user),
):
    await _require_collection_access(collection_id, current_user)

    edge = await get_graph_edge(collection_id, edge_id)
    if not edge:
        raise HTTPException(status_code=404, detail="Edge not found")

    await delete_graph_edge(collection_id, edge_id)

    im = get_index_manager()
    if im:
        try:
            im.delete_edge(collection_id, edge_id)
        except Exception:
            pass

    await insert_user_feedback({
        "id": str(uuid.uuid4()),
        "collection_id": collection_id,
        "user_id": current_user["id"],
        "entity_type": "edge_delete",
        "target_id": edge_id,
        "action": "delete",
        "before": json.dumps(edge),
        "after": "{}",
    })


@router.get("/export")
async def export_graph(
    collection_id: str,
    format: str = Query("json", pattern="^(json|graphml)$"),
    current_user: dict = Depends(get_current_user),
):
    await _require_collection_access(collection_id, current_user)

    im = get_index_manager()
    if im:
        try:
            graph_json = im.get_graph_data(collection_id)
            from rust_core import export_graph as rust_export_graph
            result = rust_export_graph(graph_json, format)
            if format == "graphml":
                return PlainTextResponse(result, media_type="application/xml")
            return PlainTextResponse(result, media_type="application/json")
        except Exception:
            pass

    # Fallback: export from LanceDB
    nodes = await list_graph_nodes(collection_id)
    edges = await list_graph_edges(collection_id)
    data = {"nodes": nodes, "edges": edges}
    return PlainTextResponse(json.dumps(data), media_type="application/json")
