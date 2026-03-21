"""Agent-based Graph RAG service — ReAct-style multi-hop reasoning over the knowledge graph."""

import asyncio
import json
import logging
from typing import AsyncGenerator, Optional

from app.config import get_settings
from app.db.lancedb_client import (
    list_graph_nodes,
    list_graph_edges,
    get_graph_node,
    vector_search,
)
from app.llm.embedder import embed_texts
from app.llm.extractor import _llm_client  # shared async OpenAI client

settings = get_settings()
logger = logging.getLogger(__name__)

MAX_HOPS = 4
MAX_NODES_PER_HOP = 10
MAX_CHUNKS_PER_NODE = 3
MAX_CONTEXT_TOKENS = 6000  # rough guard; ~4 chars per token


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _embed_query(query: str) -> list[float]:
    vectors = await embed_texts([query])
    return vectors[0] if vectors else []


async def _retrieve_seed_chunks(
    collection_id: str,
    query: str,
    top_k: int = 8,
) -> list[dict]:
    """Vector search for initial entry points into the graph."""
    embedding = await _embed_query(query)
    if not embedding:
        return []
    return await vector_search(collection_id, embedding, limit=top_k)


async def _chunks_for_nodes(
    collection_id: str,
    node_ids: list[str],
    max_per_node: int = MAX_CHUNKS_PER_NODE,
) -> list[dict]:
    """Fetch chunk records linked to given node IDs via source_chunk_ids."""
    from app.db.lancedb_client import get_lancedb

    db = await get_lancedb()
    table_name = f"{collection_id}_chunks"
    try:
        tbl = await db.open_table(table_name)
    except Exception:
        return []

    chunks = []
    for nid in node_ids:
        try:
            results = (
                await tbl.search()
                .where(f"'{nid}' IN source_node_ids OR doc_id = '{nid}'")
                .limit(max_per_node)
                .to_list()
            )
            chunks.extend(results)
        except Exception:
            pass
    return chunks


async def _neighbors(
    collection_id: str,
    node_id: str,
    edges: list[dict],
    visited: set[str],
    max_neighbors: int = MAX_NODES_PER_HOP,
) -> list[str]:
    """Return unvisited neighbor IDs for a given node."""
    neighbors = []
    for e in edges:
        src = e.get("source") or e.get("source_id", "")
        tgt = e.get("target") or e.get("target_id", "")
        if src == node_id and tgt not in visited:
            neighbors.append(tgt)
        elif tgt == node_id and src not in visited:
            neighbors.append(src)
    return neighbors[:max_neighbors]


def _truncate_context(text: str, max_chars: int = MAX_CONTEXT_TOKENS * 4) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[context truncated]"


# ---------------------------------------------------------------------------
# ReAct agent loop
# ---------------------------------------------------------------------------


async def run_agent(
    collection_id: str,
    query: str,
    max_hops: int = MAX_HOPS,
) -> AsyncGenerator[dict, None]:
    """
    ReAct-style agent that:
    1. Embeds the query and retrieves seed chunks.
    2. Identifies entities mentioned in those chunks.
    3. Hops through the graph following edges.
    4. At each hop, gathers evidence and decides whether to continue.
    5. Streams thought/action/observation events and a final answer via SSE.
    """

    yield {"type": "start", "query": query, "collection_id": collection_id}

    # --- Step 0: Load graph structure once ---
    edges = await list_graph_edges(collection_id)
    nodes = await list_graph_nodes(collection_id)
    node_index: dict[str, dict] = {n["id"]: n for n in nodes}

    # --- Step 1: Seed retrieval ---
    yield {"type": "thought", "hop": 0, "content": "Searching for relevant document chunks..."}

    seed_chunks = await _retrieve_seed_chunks(collection_id, query, top_k=8)
    if not seed_chunks:
        yield {
            "type": "answer",
            "content": "I could not find relevant information in this collection for your query.",
            "hops_taken": 0,
            "nodes_visited": [],
        }
        return

    yield {
        "type": "observation",
        "hop": 0,
        "content": f"Found {len(seed_chunks)} relevant chunks via semantic search.",
    }

    # --- Step 2: Identify seed nodes from chunk metadata ---
    visited: set[str] = set()
    frontier: list[str] = []

    for chunk in seed_chunks:
        # Chunks carry source_node_ids or can be linked via doc_id
        node_ids = chunk.get("source_node_ids") or []
        if isinstance(node_ids, str):
            try:
                node_ids = json.loads(node_ids)
            except Exception:
                node_ids = []
        for nid in node_ids:
            if nid in node_index and nid not in visited:
                frontier.append(nid)
                visited.add(nid)

    # If no node links, use top-k nodes by embedding similarity as fallback
    if not frontier and node_index:
        embedding = await _embed_query(query)
        if embedding:
            import math

            def _cosine(a: list[float], b: list[float]) -> float:
                dot = sum(x * y for x, y in zip(a, b))
                na = math.sqrt(sum(x * x for x in a))
                nb = math.sqrt(sum(x * x for x in b))
                return dot / max(na * nb, 1e-9)

            scored = []
            for nid, nd in node_index.items():
                emb = nd.get("embedding") or []
                if emb:
                    scored.append((nid, _cosine(embedding, emb)))
            scored.sort(key=lambda x: -x[1])
            for nid, _ in scored[:5]:
                if nid not in visited:
                    frontier.append(nid)
                    visited.add(nid)

    # --- Step 3: Accumulate context across hops ---
    context_parts: list[str] = []

    # Add seed chunk texts
    for chunk in seed_chunks[:5]:
        text = chunk.get("contextual_text") or chunk.get("text") or ""
        if text:
            context_parts.append(f"[Document chunk]\n{text}")

    # --- Step 4: Graph traversal hops ---
    hop = 0
    while frontier and hop < max_hops:
        hop += 1
        current_batch = frontier[:MAX_NODES_PER_HOP]
        frontier = frontier[MAX_NODES_PER_HOP:]

        node_labels = [node_index[nid].get("label", nid) for nid in current_batch if nid in node_index]
        yield {
            "type": "thought",
            "hop": hop,
            "content": f"Exploring graph neighbourhood: {', '.join(node_labels[:5])}{'...' if len(node_labels) > 5 else ''}",
        }

        # Gather entity descriptions
        for nid in current_batch:
            nd = node_index.get(nid)
            if nd:
                desc = nd.get("description") or ""
                label = nd.get("label", nid)
                etype = nd.get("entity_type", "")
                context_parts.append(
                    f"[Entity: {label} ({etype})]\n{desc}" if desc else f"[Entity: {label} ({etype})]"
                )

        # --- Reasoning step: should we continue? ---
        reasoning_context = _truncate_context("\n\n".join(context_parts))
        reasoning_prompt = (
            f"You are a graph-based reasoning agent.\n"
            f"Query: {query}\n\n"
            f"Context gathered so far:\n{reasoning_context}\n\n"
            f"Based on the context above, can you fully answer the query? "
            f"Reply with JSON: {{\"sufficient\": true/false, \"reasoning\": \"...\"}}"
        )

        sufficient = False
        try:
            client = _llm_client()
            resp = await client.chat.completions.create(
                model=settings.ollama_cloud_model,
                messages=[{"role": "user", "content": reasoning_prompt}],
                max_tokens=200,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content or "{}"
            parsed = json.loads(raw)
            sufficient = bool(parsed.get("sufficient", False))
            reasoning_note = parsed.get("reasoning", "")
        except Exception as exc:
            logger.warning(f"Agent reasoning step failed: {exc}")
            reasoning_note = ""

        yield {
            "type": "observation",
            "hop": hop,
            "content": f"Visited {len(current_batch)} nodes. {'Context appears sufficient.' if sufficient else 'Continuing traversal.'}",
        }

        if sufficient:
            break

        # Expand frontier with neighbors
        next_nodes: list[str] = []
        for nid in current_batch:
            nbrs = await _neighbors(collection_id, nid, edges, visited)
            for nb in nbrs:
                if nb not in visited:
                    next_nodes.append(nb)
                    visited.add(nb)

        frontier.extend(next_nodes[:MAX_NODES_PER_HOP * 2])

    # --- Step 5: Final synthesis ---
    yield {"type": "thought", "hop": hop + 1, "content": "Synthesising final answer..."}

    final_context = _truncate_context("\n\n".join(context_parts))
    synthesis_prompt = (
        f"You are a knowledge graph assistant. Answer the user's query using ONLY the "
        f"context extracted from the knowledge graph below. Be precise and cite entity "
        f"names when relevant. If the context is insufficient, say so clearly.\n\n"
        f"Query: {query}\n\n"
        f"Context:\n{final_context}\n\n"
        f"Answer:"
    )

    answer_text = ""
    try:
        client = _llm_client()
        stream = await client.chat.completions.create(
            model=settings.ollama_cloud_model,
            messages=[{"role": "user", "content": synthesis_prompt}],
            max_tokens=1024,
            temperature=0.2,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                answer_text += delta
                yield {"type": "token", "content": delta}
    except Exception as exc:
        logger.error(f"Agent synthesis failed: {exc}")
        answer_text = "I encountered an error generating the final answer."
        yield {"type": "token", "content": answer_text}

    yield {
        "type": "answer",
        "content": answer_text,
        "hops_taken": hop,
        "nodes_visited": list(visited),
    }
