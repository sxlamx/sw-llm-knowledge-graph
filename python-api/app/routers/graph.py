"""Graph router — knowledge graph CRUD and traversal."""

import json
import re
import uuid
from pathlib import Path
from typing import Optional

_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
    re.IGNORECASE,
)

def _resolve_doc_title(doc: dict) -> str:
    raw = doc.get("title", "")
    if raw and not _UUID_RE.match(raw):
        return raw
    file_path = doc.get("file_path") or doc.get("path") or ""
    return Path(file_path).name if file_path else (raw or "Untitled")


def _parse_props(v) -> dict:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return {}
    return v or {}

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
    get_node_summary,
    upsert_node_summary,
    get_chunk_ids_with_ner_labels,
    get_chunk_ids_with_ner_keywords,
)
from app.core.rust_bridge import get_index_manager
from app.models.schemas import (
    GraphDataResponse, GraphNodeResponse, GraphNodeDetailResponse,
    GraphEdgeResponse, GraphPathResponse, LinkedChunk,
    UpdateGraphNodeRequest, CreateGraphEdgeRequest, UserFeedbackCreate,
    NodeSummaryResponse,
)

router = APIRouter()


def _node_to_response(n: dict) -> GraphNodeResponse:
    return GraphNodeResponse(
        id=n.get("id", ""),
        label=n.get("label", ""),
        entity_type=n.get("entity_type", n.get("node_type", "Concept")),
        description=n.get("description"),
        confidence=float(n.get("confidence", 1.0)),
        properties=_parse_props(n.get("properties")),
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
        properties=_parse_props(e.get("properties")),
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
    date_from: Optional[str] = Query(None, description="ISO date filter start (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="ISO date filter end (YYYY-MM-DD)"),
    doc_id: Optional[str] = Query(None, description="Filter nodes sourced from this document"),
    entity_type_filters: list[str] = Query(default=[], description="Filter nodes by entity_type (e.g. Person, Organization)"),
    ner_label_filters: list[str] = Query(default=[], description="Filter nodes whose source chunks contain these NER labels (e.g. LEGISLATION_TITLE)"),
    ner_keyword_filters: list[str] = Query(default=[], description="Filter nodes whose source chunks contain these specific NER keyword texts (e.g. Chicago, Singapore)"),
    current_user: dict = Depends(get_current_user),
):
    await _require_collection_access(collection_id, current_user)

    # Parse date bounds to microsecond timestamps (LanceDB stores created_at in µs)
    from datetime import datetime as _dt
    ts_from: Optional[int] = None
    ts_to: Optional[int] = None
    if date_from:
        try:
            ts_from = int(_dt.fromisoformat(date_from).timestamp() * 1_000_000)
        except ValueError:
            pass
    if date_to:
        try:
            ts_to = int(_dt.fromisoformat(date_to).timestamp() * 1_000_000) + 86_400_000_000  # inclusive day-end
        except ValueError:
            pass

    # Resolve chunk IDs for the given document
    doc_chunk_ids: Optional[set[str]] = None
    if doc_id:
        from app.db.lancedb_client import get_lancedb
        db = await get_lancedb()
        try:
            tbl = db.open_table(f"{collection_id}_chunks")
            rows = tbl.search().where(f'doc_id = "{doc_id}"', prefilter=True).to_list()
            doc_chunk_ids = {r["id"] for r in rows if r.get("id")}
        except Exception:
            doc_chunk_ids = set()

    # Resolve chunk IDs that carry the requested NER labels
    ner_chunk_ids: Optional[set[str]] = None
    if ner_label_filters:
        ner_chunk_ids = await get_chunk_ids_with_ner_labels(collection_id, ner_label_filters)

    # Build a label set for direct node-label matching against keyword filters.
    # Chunk-based lookup was too broad — it returned every node co-extracted from
    # the same chunk, not just the node whose label IS the keyword.
    ner_keyword_label_set: Optional[set[str]] = (
        {k.lower() for k in ner_keyword_filters} if ner_keyword_filters else None
    )

    # Normalise entity_type filter to a set for O(1) lookup
    entity_type_set: Optional[set[str]] = set(entity_type_filters) if entity_type_filters else None

    def _passes_filters(node: dict) -> bool:
        # Date filter
        ts = node.get("created_at") or node.get("updated_at") or 0
        if ts_from is not None and ts < ts_from:
            return False
        if ts_to is not None and ts > ts_to:
            return False
        # Document filter — node must reference at least one chunk from the doc
        if doc_chunk_ids is not None:
            chunk_ids = set(node.get("source_chunk_ids") or [])
            if not chunk_ids & doc_chunk_ids:
                return False
        # Entity type filter
        if entity_type_set is not None:
            etype = node.get("entity_type") or node.get("node_type") or ""
            if etype not in entity_type_set:
                return False
        # NER label filter — node must source from at least one NER-matching chunk
        if ner_chunk_ids is not None:
            chunk_ids = set(node.get("source_chunk_ids") or [])
            if not chunk_ids & ner_chunk_ids:
                return False
        # NER keyword filter — node label must directly match one of the selected keywords
        if ner_keyword_label_set is not None:
            label = (node.get("label") or "").lower()
            if label not in ner_keyword_label_set:
                return False
        return True

    # Try in-memory Rust graph first — only when no field-dependent filters are set.
    # Rust graph nodes lack source_chunk_ids / entity_type, so skip it when those
    # filters are active to avoid silently returning an empty graph.
    im = get_index_manager()
    use_rust = im is not None and entity_type_set is None and ner_chunk_ids is None
    if use_rust:
        try:
            data_json = im.get_graph_data(collection_id)
            data = json.loads(data_json)
            if data.get("total_nodes", 0) > 0:
                all_nodes = [n for n in data["nodes"] if _passes_filters(n)]
                offset = page * limit
                nodes = all_nodes[offset: offset + limit]
                node_ids = {n.get("id") for n in nodes}
                edges = [
                    e for e in data["edges"]
                    if e.get("source") in node_ids and e.get("target") in node_ids
                ][:limit]
                return GraphDataResponse(
                    nodes=[_node_to_response(n) for n in nodes],
                    edges=[_edge_to_response(e) for e in edges],
                    total_nodes=len(all_nodes),
                    total_edges=len(edges),
                )
        except Exception:
            pass

    # Fall back to LanceDB
    offset = page * limit
    all_nodes_raw = await list_graph_nodes(collection_id)
    all_nodes_raw = [n for n in all_nodes_raw if _passes_filters(n)]
    all_edges = await list_graph_edges(collection_id)

    node_page = all_nodes_raw[offset: offset + limit]
    node_ids = {n["id"] for n in node_page}
    edge_page = [
        e for e in all_edges
        if e.get("source", e.get("source_id")) in node_ids
        and e.get("target", e.get("target_id")) in node_ids
    ]

    return GraphDataResponse(
        nodes=[_node_to_response(n) for n in node_page],
        edges=[_edge_to_response(e) for e in edge_page],
        total_nodes=len(all_nodes_raw),
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

    # Find linked source chunks.
    # Use NER-matched chunks as the authoritative set (they are what the NER
    # keyword panel counts), falling back to source_chunk_ids only when the
    # node label produces no NER matches (e.g. node was extracted without a
    # corresponding NER tag).  Never union the two — that inflates the count
    # beyond what the NER panel shows.
    linked_chunks: list[LinkedChunk] = []
    node_label = node.get("label", "")
    ner_matched = (
        await get_chunk_ids_with_ner_keywords(collection_id, [node_label])
        if node_label else set()
    )
    chunk_ids = list(ner_matched) if ner_matched else list(node.get("source_chunk_ids") or [])
    if chunk_ids:
        from app.db.lancedb_client import get_lancedb
        db = await get_lancedb()
        try:
            tbl = db.open_table(f"{collection_id}_chunks")
            # Fetch doc metadata for titles
            docs = {d["id"]: d for d in await list_documents(collection_id)}
            for cid in chunk_ids[:200]:
                rows = tbl.search().where(f'id = "{cid}"', prefilter=True).limit(1).to_list()
                if rows:
                    c = rows[0]
                    doc = docs.get(c.get("doc_id", ""), {})
                    linked_chunks.append(LinkedChunk(
                        chunk_id=c.get("id", cid),
                        doc_id=c.get("doc_id", ""),
                        doc_title=_resolve_doc_title(doc) if doc else c.get("doc_id", ""),
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


@router.get("/nodes/{node_id}/summary", response_model=NodeSummaryResponse)
async def get_node_summary_endpoint(
    node_id: str,
    collection_id: str,
    force: bool = Query(False, description="Force regeneration even if cached"),
    current_user: dict = Depends(get_current_user),
):
    """Return a cached LLM summary for a node; regenerate if chunks changed."""
    import hashlib
    import httpx
    from app.config import get_settings

    await _require_collection_access(collection_id, current_user)

    node = await get_graph_node(collection_id, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    # Collect chunk texts using the same NER-first logic as the detail endpoint
    # so the LLM summary is grounded in exactly the chunks the panel shows.
    node_label = node.get("label", node_id)
    ner_matched = (
        await get_chunk_ids_with_ner_keywords(collection_id, [node_label])
        if node_label else set()
    )
    chunk_ids: list[str] = list(ner_matched) if ner_matched else list(node.get("source_chunk_ids") or [])
    chunk_texts: list[str] = []
    if chunk_ids:
        from app.db.lancedb_client import get_lancedb
        db = await get_lancedb()
        try:
            tbl = db.open_table(f"{collection_id}_chunks")
            for cid in chunk_ids[:20]:  # cap to avoid huge prompts
                rows = tbl.search().where(f'id = "{cid}"', prefilter=True).limit(1).to_list()
                if rows:
                    text = rows[0].get("contextual_text") or rows[0].get("text", "")
                    if text.strip():
                        chunk_texts.append(text[:600])
        except Exception:
            pass

    # Compute content hash over chunk IDs (cheap proxy for content change)
    raw_hash = "|".join(sorted(chunk_ids))
    chunk_hash = hashlib.sha1(raw_hash.encode()).hexdigest()

    # Check cache
    if not force:
        cached = await get_node_summary(collection_id, node_id)
        if cached and cached.get("chunk_hash") == chunk_hash:
            return NodeSummaryResponse(
                node_id=node_id,
                summary=cached["summary"],
                chunk_hash=chunk_hash,
                updated_at=cached.get("updated_at"),
                from_cache=True,
            )

    # Generate summary via LLM
    settings = get_settings()
    node_label = node.get("label", node_id)
    node_desc = node.get("description", "")
    entity_type = node.get("entity_type", "entity")

    if chunk_texts:
        excerpts = "\n\n---\n\n".join(chunk_texts)
        prompt = (
            f"You are analysing a knowledge graph node.\n\n"
            f"Node: {node_label} (type: {entity_type})\n"
            f"Description: {node_desc}\n\n"
            f"Source document excerpts:\n{excerpts}\n\n"
            "Write a concise 2-4 sentence summary of what this entity represents "
            "based on the source excerpts. Focus on facts, roles, and relationships."
        )
    else:
        prompt = (
            f"Describe the entity '{node_label}' (type: {entity_type}) "
            f"based on this description: {node_desc or 'no description available'}. "
            "Keep it to 2-3 sentences."
        )

    # Build a static fallback summary from node metadata (used when LLM is unavailable)
    fallback_summary = (
        f"{node_label} is a {entity_type.lower()} entity."
        + (f" {node_desc}" if node_desc else "")
    ).strip()

    summary = fallback_summary
    llm_available = bool(settings.ollama_cloud_base_url)
    if llm_available:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{settings.ollama_cloud_base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {settings.ollama_cloud_api_key}"},
                    json={
                        "model": settings.ollama_cloud_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 200,
                        "temperature": 0.3,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                summary = data["choices"][0]["message"]["content"].strip()
        except Exception:
            summary = fallback_summary

    now = int(__import__("time").time() * 1_000_000)
    await upsert_node_summary(collection_id, node_id, summary, chunk_hash)

    return NodeSummaryResponse(
        node_id=node_id,
        summary=summary,
        chunk_hash=chunk_hash,
        updated_at=now,
        from_cache=False,
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


@router.get("/ner-keywords")
async def get_ner_keywords(
    collection_id: str,
    labels: list[str] = Query(default=[], description="NER labels to count keywords for (e.g. PERSON, LEGISLATION_TITLE). Empty = all labels."),
    top_n: int = Query(default=30, ge=1, le=200, description="Max keywords to return per label"),
    current_user: dict = Depends(get_current_user),
):
    """Return top keyword frequencies per NER label from chunk ner_tags.

    Response example:
      {
        "PERSON": [{"text": "John Smith", "count": 42}, ...],
        "ORGANIZATION": [...]
      }
    """
    from collections import Counter
    await _require_collection_access(collection_id, current_user)

    from app.db.lancedb_client import get_chunks_for_collection
    chunks = await get_chunks_for_collection(collection_id)

    label_set = set(labels) if labels else None
    counters: dict[str, Counter] = {}

    for chunk in chunks:
        raw = chunk.get("ner_tags") or "[]"
        try:
            tags = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            continue
        # Count each (label, text) pair once per chunk so the frequency matches
        # the number of distinct chunks returned by get_chunk_ids_with_ner_keywords.
        seen_in_chunk: set[tuple[str, str]] = set()
        for tag in tags:
            lbl = tag.get("label", "")
            # Normalise to lowercase — matches the case-insensitive logic in
            # get_chunk_ids_with_ner_keywords so counts always equal chunk counts.
            text = tag.get("text", "").strip().lower()
            if not lbl or not text:
                continue
            if label_set is not None and lbl not in label_set:
                continue
            key = (lbl, text)
            if key not in seen_in_chunk:
                counters.setdefault(lbl, Counter())[text] += 1
                seen_in_chunk.add(key)

    return {
        lbl: [{"text": t, "count": c} for t, c in counter.most_common(top_n)]
        for lbl, counter in sorted(counters.items())
    }


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
