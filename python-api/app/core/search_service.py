"""Search service — 3-channel hybrid search (vector + BM25 + graph proximity)."""

import asyncio
import json
import logging
from typing import Optional

from app.llm.embedder import embed_query
from app.db.lancedb_client import vector_search as lancedb_vector_search, get_lancedb
from app.core.rust_bridge import get_index_manager

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
    if not collection_ids:
        return []
    if weights is None:
        weights = {"vector": 0.6, "keyword": 0.3, "graph": 0.1}

    all_results: list[dict] = []
    for cid in collection_ids:
        results = await _search_collection(query, cid, topics, limit + offset, mode, weights)
        all_results.extend(results)

    all_results.sort(key=lambda r: r.get("final_score", 0.0), reverse=True)
    return all_results[offset: offset + limit]


async def _search_collection(
    query: str,
    collection_id: str,
    topics: Optional[list[str]],
    limit: int,
    mode: str,
    weights: dict,
) -> list[dict]:
    w_vec = weights.get("vector", 0.6)
    w_kw = weights.get("keyword", 0.3)
    w_graph = weights.get("graph", 0.1)

    # ---------- 3 channels in parallel ----------
    vec_task = asyncio.create_task(_vector_channel(query, collection_id, topics, limit))
    kw_task = asyncio.create_task(_keyword_channel(query, collection_id, limit))
    graph_task = asyncio.create_task(_graph_channel(query, collection_id, limit))

    vec_results, kw_results, graph_results = await asyncio.gather(
        vec_task, kw_task, graph_task, return_exceptions=True
    )

    if isinstance(vec_results, Exception):
        logger.warning(f"Vector channel failed: {vec_results}")
        vec_results = []
    if isinstance(kw_results, Exception):
        logger.warning(f"Keyword channel failed: {kw_results}")
        kw_results = []
    if isinstance(graph_results, Exception):
        logger.warning(f"Graph channel failed: {graph_results}")
        graph_results = []

    if mode == "vector":
        return _attach_scores(vec_results, 1.0, 0.0, 0.0)
    if mode == "keyword":
        return _attach_scores(kw_results, 0.0, 1.0, 0.0)
    if mode == "graph":
        return _attach_scores(graph_results, 0.0, 0.0, 1.0)

    # Hybrid: fuse scores
    return _fuse(vec_results, kw_results, graph_results, w_vec, w_kw, w_graph, limit)


async def _vector_channel(
    query: str,
    collection_id: str,
    topics: Optional[list[str]],
    limit: int,
) -> list[dict]:
    embedding = await embed_query(query)
    return await lancedb_vector_search(collection_id, embedding, limit=limit, topics=topics)


async def _keyword_channel(query: str, collection_id: str, limit: int) -> list[dict]:
    im = get_index_manager()
    if im is None:
        return []
    loop = asyncio.get_event_loop()
    try:
        results_json = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: im.text_search(collection_id, query, limit)),
            timeout=0.8,
        )
        rows = json.loads(results_json)
        # Normalise: Tantivy returns {id, text, doc_id} without scores
        # Assign BM25 rank-based score (1/(rank+1) normalised to [0,1])
        total = max(len(rows), 1)
        return [
            {
                "chunk_id": r.get("id", ""),
                "doc_id": r.get("doc_id", ""),
                "text": r.get("text", ""),
                "keyword_score": 1.0 - (i / total),
            }
            for i, r in enumerate(rows)
        ]
    except asyncio.TimeoutError:
        logger.warning("BM25 channel timed out")
        return []
    except Exception as e:
        logger.warning(f"BM25 channel error: {e}")
        return []


async def _graph_channel(query: str, collection_id: str, limit: int) -> list[dict]:
    """Graph proximity: embed query → find nearest entity nodes → score chunks by node proximity."""
    try:
        from app.db.lancedb_client import list_graph_nodes, list_graph_edges
        embedding = await embed_query(query)

        nodes = await list_graph_nodes(collection_id)
        if not nodes:
            return []

        # Cosine similarity between query embedding and node embeddings (if stored)
        scored_nodes: list[tuple[str, float]] = []
        for n in nodes:
            node_emb = n.get("embedding")
            if node_emb and len(node_emb) == len(embedding):
                score = _cosine_sim(embedding, node_emb)
                scored_nodes.append((n["id"], score))

        if not scored_nodes:
            return []

        scored_nodes.sort(key=lambda x: -x[1])
        top_node_ids = {nid for nid, _ in scored_nodes[:10]}
        node_score_map = {nid: score for nid, score in scored_nodes[:10]}

        # Find chunk_ids linked to top nodes
        chunk_scores: dict[str, float] = {}
        for n in nodes:
            if n["id"] in top_node_ids:
                for cid in (n.get("source_chunk_ids") or []):
                    chunk_scores[cid] = max(chunk_scores.get(cid, 0.0), node_score_map[n["id"]])

        if not chunk_scores:
            return []

        # Fetch chunk text
        db = await get_lancedb()
        results = []
        try:
            tbl = db.open_table(f"{collection_id}_chunks")
            for cid, score in list(chunk_scores.items())[:limit]:
                rows = tbl.search().where(f'id = "{cid}"', prefilter=True).limit(1).to_list()
                if rows:
                    c = rows[0]
                    results.append({
                        "chunk_id": cid,
                        "doc_id": c.get("doc_id", ""),
                        "text": c.get("text", ""),
                        "graph_proximity_score": float(score),
                    })
        except Exception:
            pass
        return results

    except Exception as e:
        logger.warning(f"Graph channel error: {e}")
        return []


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _attach_scores(
    results: list[dict],
    w_vec: float,
    w_kw: float,
    w_graph: float,
) -> list[dict]:
    out = []
    for r in results:
        vs = float(r.get("vector_score", 0.0))
        ks = float(r.get("keyword_score", 0.0))
        gs = float(r.get("graph_proximity_score", 0.0))
        final = vs * w_vec + ks * w_kw + gs * w_graph
        out.append({
            "chunk_id": r.get("chunk_id", r.get("id", "")),
            "doc_id": r.get("doc_id", ""),
            "doc_title": r.get("doc_title"),
            "text": r.get("text", ""),
            "page": r.get("page"),
            "vector_score": vs,
            "keyword_score": ks,
            "graph_proximity_score": gs,
            "final_score": final,
            "topics": r.get("topics") or [],
            "highlights": [],
        })
    return out


def _fuse(
    vec: list[dict],
    kw: list[dict],
    graph: list[dict],
    w_vec: float,
    w_kw: float,
    w_graph: float,
    limit: int,
) -> list[dict]:
    # Build per-chunk score maps
    vec_map: dict[str, dict] = {r.get("chunk_id", r.get("id", "")): r for r in vec}
    kw_map: dict[str, dict] = {r.get("chunk_id", ""): r for r in kw}
    graph_map: dict[str, dict] = {r.get("chunk_id", ""): r for r in graph}

    all_ids = set(vec_map) | set(kw_map) | set(graph_map)

    fused = []
    for cid in all_ids:
        v = vec_map.get(cid, {})
        k = kw_map.get(cid, {})
        g = graph_map.get(cid, {})
        base = v or k or g

        vs = float(v.get("vector_score", 0.0))
        ks = float(k.get("keyword_score", 0.0))
        gs = float(g.get("graph_proximity_score", 0.0))
        final = vs * w_vec + ks * w_kw + gs * w_graph

        fused.append({
            "chunk_id": cid,
            "doc_id": base.get("doc_id", ""),
            "doc_title": base.get("doc_title"),
            "text": base.get("text", ""),
            "page": base.get("page"),
            "vector_score": vs,
            "keyword_score": ks,
            "graph_proximity_score": gs,
            "final_score": final,
            "topics": base.get("topics") or [],
            "highlights": [],
        })

    fused.sort(key=lambda x: -x["final_score"])
    return fused[:limit]
