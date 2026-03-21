"""Tests for the pure-Python graph analytics service."""

import pytest
from app.services.analytics_service import pagerank, betweenness_centrality, louvain_communities


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _node(nid: str) -> dict:
    return {"id": nid, "label": nid}


def _edge(src: str, tgt: str, weight: float = 1.0) -> dict:
    return {"source": src, "target": tgt, "weight": weight}


# ---------------------------------------------------------------------------
# PageRank
# ---------------------------------------------------------------------------

class TestPageRank:
    def test_empty_graph_returns_empty(self):
        assert pagerank([], []) == {}

    def test_single_node_no_edges(self):
        scores = pagerank([_node("a")], [])
        assert "a" in scores
        assert scores["a"] == pytest.approx(1.0)

    def test_two_nodes_one_direction(self):
        nodes = [_node("a"), _node("b")]
        edges = [_edge("a", "b")]
        scores = pagerank(nodes, edges)
        # b receives all rank from a → b should score higher
        assert scores["b"] > scores["a"]

    def test_all_scores_normalised(self):
        nodes = [_node(str(i)) for i in range(5)]
        edges = [_edge(str(i), str((i + 1) % 5)) for i in range(5)]
        scores = pagerank(nodes, edges)
        assert max(scores.values()) == pytest.approx(1.0)

    def test_star_topology_centre_highest(self):
        # Centre node "hub" points to all spokes; spokes point back
        nodes = [_node("hub")] + [_node(f"s{i}") for i in range(4)]
        edges = (
            [_edge(f"s{i}", "hub") for i in range(4)]  # spokes → hub
            + [_edge("hub", f"s{i}") for i in range(4)]  # hub → spokes
        )
        scores = pagerank(nodes, edges)
        assert scores["hub"] == pytest.approx(1.0)

    def test_handles_source_id_alias(self):
        nodes = [_node("x"), _node("y")]
        edges = [{"source_id": "x", "target_id": "y"}]
        scores = pagerank(nodes, edges)
        assert "x" in scores and "y" in scores


# ---------------------------------------------------------------------------
# Betweenness Centrality
# ---------------------------------------------------------------------------

class TestBetweennessCentrality:
    def test_empty(self):
        assert betweenness_centrality([], []) == {}

    def test_single_node(self):
        scores = betweenness_centrality([_node("a")], [])
        assert scores == {"a": 0.0}

    def test_bridge_node_highest(self):
        # a — b — c  (b is the only bridge)
        nodes = [_node("a"), _node("b"), _node("c")]
        edges = [_edge("a", "b"), _edge("b", "c")]
        scores = betweenness_centrality(nodes, edges)
        # b must have the highest betweenness
        assert scores["b"] == pytest.approx(1.0)
        assert scores["a"] == scores["c"]

    def test_fully_connected_triangle(self):
        nodes = [_node("a"), _node("b"), _node("c")]
        edges = [_edge("a", "b"), _edge("b", "c"), _edge("a", "c")]
        scores = betweenness_centrality(nodes, edges)
        # All nodes equally central in a triangle
        assert scores["a"] == pytest.approx(scores["b"])
        assert scores["b"] == pytest.approx(scores["c"])

    def test_all_normalised_to_one(self):
        nodes = [_node(str(i)) for i in range(4)]
        # Linear chain: 0-1-2-3 → node 1 and 2 are bridges
        edges = [_edge(str(i), str(i + 1)) for i in range(3)]
        scores = betweenness_centrality(nodes, edges)
        assert max(scores.values()) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Louvain Community Detection
# ---------------------------------------------------------------------------

class TestLouvainCommunities:
    def test_empty(self):
        assert louvain_communities([], []) == {}

    def test_no_edges_each_own_community(self):
        nodes = [_node("a"), _node("b"), _node("c")]
        comms = louvain_communities(nodes, [])
        # No edges → each node is its own community
        assert len(set(comms.values())) == 3

    def test_two_clusters(self):
        # Cluster 1: a-b-c  Cluster 2: d-e-f  with one weak bridge c-d
        nodes = [_node(n) for n in "abcdef"]
        edges = (
            [_edge("a", "b"), _edge("b", "c"), _edge("a", "c")]   # dense cluster 1
            + [_edge("d", "e"), _edge("e", "f"), _edge("d", "f")] # dense cluster 2
            + [_edge("c", "d", weight=0.1)]                        # weak bridge
        )
        comms = louvain_communities(nodes, edges)
        # a, b, c should share a community; d, e, f another
        assert comms["a"] == comms["b"] == comms["c"]
        assert comms["d"] == comms["e"] == comms["f"]
        assert comms["a"] != comms["d"]

    def test_community_ids_are_sequential_strings(self):
        nodes = [_node("x"), _node("y")]
        edges = [_edge("x", "y")]
        comms = louvain_communities(nodes, edges)
        for v in comms.values():
            assert isinstance(v, str)
            int(v)  # must be parseable as integer

    def test_returns_all_nodes(self):
        nodes = [_node(str(i)) for i in range(10)]
        edges = [_edge(str(i), str(i + 1)) for i in range(9)]
        comms = louvain_communities(nodes, edges)
        assert set(comms.keys()) == {str(i) for i in range(10)}
