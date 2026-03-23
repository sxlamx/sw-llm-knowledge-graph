"""Graph analytics — PageRank, betweenness centrality, community detection."""

import logging
import math
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PageRank (iterative, damping factor 0.85)
# ---------------------------------------------------------------------------

def pagerank(
    nodes: list[dict],
    edges: list[dict],
    iterations: int = 50,
    damping: float = 0.85,
) -> dict[str, float]:
    n = len(nodes)
    if n == 0:
        return {}

    node_ids = [nd["id"] for nd in nodes]
    rank = {nid: 1.0 / n for nid in node_ids}

    # Build out-degree adjacency
    out_edges: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        src = e.get("source", e.get("source_id", ""))
        tgt = e.get("target", e.get("target_id", ""))
        if src and tgt:
            out_edges[src].append(tgt)

    out_degree = {nid: len(out_edges[nid]) for nid in node_ids}

    for _ in range(iterations):
        new_rank: dict[str, float] = {}
        for nid in node_ids:
            # Sum contributions from all in-edges
            incoming = sum(
                rank[e.get("source", e.get("source_id", ""))]
                / max(out_degree.get(e.get("source", e.get("source_id", "")), 1), 1)
                for e in edges
                if e.get("target", e.get("target_id", "")) == nid
            )
            new_rank[nid] = (1 - damping) / n + damping * incoming
        rank = new_rank

    # Normalise to [0, 1]
    max_r = max(rank.values()) if rank else 1.0
    return {nid: r / max_r for nid, r in rank.items()}


# ---------------------------------------------------------------------------
# Betweenness centrality (approximate, BFS-based Brandes)
# ---------------------------------------------------------------------------

def betweenness_centrality(
    nodes: list[dict],
    edges: list[dict],
    normalise: bool = True,
) -> dict[str, float]:
    from collections import deque

    node_ids = [nd["id"] for nd in nodes]
    id_set = set(node_ids)

    # Adjacency (undirected)
    adj: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        src = e.get("source", e.get("source_id", ""))
        tgt = e.get("target", e.get("target_id", ""))
        if src in id_set and tgt in id_set:
            adj[src].append(tgt)
            adj[tgt].append(src)

    bc: dict[str, float] = {nid: 0.0 for nid in node_ids}

    for s in node_ids:
        stack: list[str] = []
        pred: dict[str, list[str]] = {nid: [] for nid in node_ids}
        sigma: dict[str, float] = {nid: 0.0 for nid in node_ids}
        dist: dict[str, int] = {nid: -1 for nid in node_ids}

        sigma[s] = 1.0
        dist[s] = 0
        queue: deque[str] = deque([s])

        while queue:
            v = queue.popleft()
            stack.append(v)
            for w in adj[v]:
                if dist[w] < 0:
                    queue.append(w)
                    dist[w] = dist[v] + 1
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)

        delta: dict[str, float] = {nid: 0.0 for nid in node_ids}
        while stack:
            w = stack.pop()
            for v in pred[w]:
                if sigma[w] > 0:
                    delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
            if w != s:
                bc[w] += delta[w]

    if normalise and len(node_ids) > 2:
        norm = (len(node_ids) - 1) * (len(node_ids) - 2)
        bc = {nid: v / norm for nid, v in bc.items()}

    max_bc = max(bc.values()) if bc else 1.0
    return {nid: v / max(max_bc, 1e-9) for nid, v in bc.items()}


# ---------------------------------------------------------------------------
# Community detection — Louvain (simplified greedy modularity)
# ---------------------------------------------------------------------------

def louvain_communities(
    nodes: list[dict],
    edges: list[dict],
) -> dict[str, str]:
    """Assign each node a community label. Returns {node_id: community_id}."""
    node_ids = [nd["id"] for nd in nodes]
    id_set = set(node_ids)

    # Weighted adjacency
    adj: dict[str, dict[str, float]] = defaultdict(dict)
    total_weight = 0.0
    for e in edges:
        src = e.get("source", e.get("source_id", ""))
        tgt = e.get("target", e.get("target_id", ""))
        w = float(e.get("weight", 1.0))
        if src in id_set and tgt in id_set and src != tgt:
            adj[src][tgt] = adj[src].get(tgt, 0.0) + w
            adj[tgt][src] = adj[tgt].get(src, 0.0) + w
            total_weight += w

    if total_weight == 0:
        return {nid: nid for nid in node_ids}

    m = total_weight  # single-counted edge weight sum

    # Precompute weighted degree per node
    degree: dict[str, float] = {nid: sum(adj[nid].values()) for nid in node_ids}

    # Initialise: each node in its own community
    community: dict[str, str] = {nid: nid for nid in node_ids}

    # Sum of degrees per community (updated incrementally)
    comm_degree_sum: dict[str, float] = {nid: degree[nid] for nid in node_ids}

    # Greedy modularity improvement (Louvain phase 1)
    # ΔQ of moving node i into community c:
    #   ΔQ = kic/m  -  ki * Σk(c) / (2 * m²)
    # where kic = edge weight from i to c, Σk(c) = sum of degrees in c (excl. i)
    improved = True
    iterations = 0
    while improved and iterations < 20:
        improved = False
        iterations += 1
        for nid in node_ids:
            cur_comm = community[nid]
            ki = degree[nid]

            # Weighted edges from nid to each neighbouring community
            comm_weights: dict[str, float] = defaultdict(float)
            for nb, w in adj[nid].items():
                comm_weights[community[nb]] += w

            # Temporarily remove nid's degree from its current community
            comm_degree_sum[cur_comm] -= ki

            best_comm = cur_comm
            # Baseline: gain of re-inserting into the current community
            kic_cur = comm_weights.get(cur_comm, 0.0)
            sigma_cur = comm_degree_sum.get(cur_comm, 0.0)
            best_gain = kic_cur / m - ki * sigma_cur / (2.0 * m * m)

            for cid, kic in comm_weights.items():
                if cid == cur_comm:
                    continue
                sigma_c = comm_degree_sum.get(cid, 0.0)
                gain = kic / m - ki * sigma_c / (2.0 * m * m)
                if gain > best_gain:
                    best_gain = gain
                    best_comm = cid

            if best_comm != cur_comm:
                community[nid] = best_comm
                comm_degree_sum[best_comm] = comm_degree_sum.get(best_comm, 0.0) + ki
                improved = True
            else:
                # Restore nid's degree to its original community
                comm_degree_sum[cur_comm] += ki

    # Normalise community names to sequential integers
    unique_comms = sorted(set(community.values()))
    comm_map = {c: str(i) for i, c in enumerate(unique_comms)}
    return {nid: comm_map[community[nid]] for nid in node_ids}


# ---------------------------------------------------------------------------
# LLM cluster topic extraction
# ---------------------------------------------------------------------------

CLUSTER_COLORS = [
    "#E53935", "#8E24AA", "#1E88E5", "#00ACC1",
    "#43A047", "#F4511E", "#FB8C00", "#FDD835",
    "#6D4C41", "#546E7A", "#00897B", "#3949AB",
]


async def extract_cluster_topic(node_labels: list[str]) -> str:
    """Call Ollama Cloud to produce a 3-5 word topic name for a cluster."""
    import httpx
    from app.config import get_settings

    settings = get_settings()
    sample = node_labels[:30]
    prompt = (
        f"These entities belong to the same topic cluster:\n{', '.join(sample)}\n\n"
        "Name this cluster's topic in 3-5 words. "
        "Respond with only the topic name, nothing else."
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{settings.ollama_cloud_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {settings.ollama_cloud_api_key}"},
                json={
                    "model": settings.ollama_cloud_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 20,
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.warning(f"Cluster topic extraction failed: {exc}")
        # Fallback: most frequent non-trivial words in the labels
        from collections import Counter
        words = " ".join(sample).split()
        top = Counter(w.lower() for w in words if len(w) > 3).most_common(3)
        return " / ".join(w for w, _ in top) if top else "Unknown Topic"
