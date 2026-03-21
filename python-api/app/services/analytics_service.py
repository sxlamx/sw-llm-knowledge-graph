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

    # Initialise: each node in its own community
    community: dict[str, str] = {nid: nid for nid in node_ids}

    # Greedy modularity improvement
    improved = True
    iterations = 0
    while improved and iterations < 20:
        improved = False
        iterations += 1
        for nid in node_ids:
            cur_comm = community[nid]
            # Community weights
            comm_weights: dict[str, float] = defaultdict(float)
            for nb, w in adj[nid].items():
                comm_weights[community[nb]] += w

            # Current modularity gain if we move nid out
            best_comm = cur_comm
            best_gain = 0.0
            ki = sum(adj[nid].values())

            for cid, kic in comm_weights.items():
                if cid == cur_comm:
                    continue
                # Simplified modularity delta
                gain = kic / total_weight - ki / (2 * total_weight)
                if gain > best_gain:
                    best_gain = gain
                    best_comm = cid

            if best_comm != cur_comm:
                community[nid] = best_comm
                improved = True

    # Normalise community names to integers
    unique_comms = sorted(set(community.values()))
    comm_map = {c: str(i) for i, c in enumerate(unique_comms)}
    return {nid: comm_map[community[nid]] for nid in node_ids}
