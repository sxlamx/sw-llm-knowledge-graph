"""
Integration tests — Knowledge Graph Construction

Verifies that after running the production pipeline the resulting graph:
1. Contains nodes for entities extracted from real PDF text
2. All nodes reference chunks that actually exist in LanceDB
3. All edges connect nodes that exist in the collection
4. No self-loop edges exist
5. Entity types conform to the ontology
6. Duplicate entity labels were merged (entity deduplication)
7. Graph has a minimum connected component size (not isolated singletons)
8. Node confidence scores are valid probabilities
9. Edge weights are in [0, 1]
10. At least some legal-domain entities were identified per Act

Run with:
    pytest tests/integration/test_graph.py -v
"""

from __future__ import annotations

import pytest

from .helpers import (
    assert_edge_integrity,
    assert_graph_is_connected,
    assert_no_duplicate_node_labels,
    assert_node_integrity,
)
from .conftest import ACT_DOMAIN_HINTS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chunk_ids(result: dict) -> set[str]:
    return {c["id"] for c in result["chunks"]}

def _node_ids(result: dict) -> set[str]:
    return {n["id"] for n in result["nodes"]}


# ---------------------------------------------------------------------------
# 1. Node existence and basic counts
# ---------------------------------------------------------------------------

class TestNodeExistence:
    """At least some nodes must be extracted from every Act."""

    def test_nodes_created(self, single_pdf_result):
        """The pipeline must produce at least 2 distinct graph nodes."""
        assert single_pdf_result["node_count"] >= 2, (
            f"Only {single_pdf_result['node_count']} nodes extracted; "
            "entity extraction may have failed silently"
        )

    def test_node_count_reasonable(self, single_pdf_result):
        """
        Node count should scale with chunk count.  Each chunk can produce
        at most 6 entities (mock extractor limit).  A very high count would
        indicate deduplication is broken.
        """
        chunks = single_pdf_result["chunk_count"]
        nodes = single_pdf_result["node_count"]
        # After deduplication, nodes must be fewer than total raw extractions
        max_raw = chunks * 6
        assert nodes <= max_raw, (
            f"node_count={nodes} exceeds theoretical max raw extractions "
            f"({max_raw} = {chunks} chunks × 6 entities/chunk)"
        )


# ---------------------------------------------------------------------------
# 2. Node structural integrity
# ---------------------------------------------------------------------------

class TestNodeIntegrity:
    """Every persisted node must satisfy structural invariants."""

    def test_every_node_has_valid_structure(self, single_pdf_result):
        collection_id = single_pdf_result["collection_id"]
        chunk_ids = _chunk_ids(single_pdf_result)
        for node in single_pdf_result["nodes"]:
            assert_node_integrity(node, chunk_ids, collection_id)

    def test_no_blank_labels(self, single_pdf_result):
        """Blank labels indicate the extractor returned an empty 'name' field."""
        blank = [n["id"] for n in single_pdf_result["nodes"] if not n.get("label", "").strip()]
        assert not blank, f"Nodes with blank labels: {blank}"

    def test_confidence_in_range(self, single_pdf_result):
        out_of_range = [
            (n["label"], n.get("confidence"))
            for n in single_pdf_result["nodes"]
            if not (0.0 <= float(n.get("confidence", 0.5)) <= 1.0)
        ]
        assert not out_of_range, f"Nodes with invalid confidence: {out_of_range}"

    def test_source_chunk_ids_are_non_empty(self, single_pdf_result):
        """
        Every node must trace back to at least one source chunk.
        A node with no source_chunk_ids is an orphan and breaks provenance.
        """
        orphan_nodes = [
            n["label"]
            for n in single_pdf_result["nodes"]
            if not (n.get("source_chunk_ids") or [])
        ]
        assert not orphan_nodes, (
            f"Nodes with no source_chunk_ids: {orphan_nodes[:10]}"
        )

    def test_source_chunk_ids_reference_real_chunks(self, single_pdf_result):
        """All source_chunk_ids must point to chunks that were persisted."""
        chunk_ids = _chunk_ids(single_pdf_result)
        bad: list[tuple] = []
        for node in single_pdf_result["nodes"]:
            for cid in node.get("source_chunk_ids") or []:
                if cid not in chunk_ids:
                    bad.append((node["label"], cid))
        assert not bad, (
            f"Nodes reference non-existent chunks: {bad[:5]}"
        )


# ---------------------------------------------------------------------------
# 3. Entity deduplication
# ---------------------------------------------------------------------------

class TestEntityDeduplication:
    """
    The _flush_graph merge step must deduplicate nodes by case-insensitive label.
    If the same entity appears in multiple chunks it should be ONE node with
    merged source_chunk_ids.
    """

    def test_no_duplicate_labels(self, single_pdf_result):
        assert_no_duplicate_node_labels(single_pdf_result["nodes"])

    def test_merged_nodes_have_multiple_source_chunks(self, single_pdf_result):
        """
        At least one node should reference more than one chunk — this is direct
        evidence that entity merging occurred across chunk boundaries.
        """
        multi_chunk_nodes = [
            n for n in single_pdf_result["nodes"]
            if len(n.get("source_chunk_ids") or []) > 1
        ]
        # For short documents this may be 0; only assert for larger corpora
        chunk_count = single_pdf_result["chunk_count"]
        if chunk_count >= 20:
            assert len(multi_chunk_nodes) >= 1, (
                f"No nodes with >1 source chunks in a {chunk_count}-chunk document; "
                "entity merging may not be working"
            )


# ---------------------------------------------------------------------------
# 4. Entity type distribution
# ---------------------------------------------------------------------------

class TestEntityTypeDistribution:
    """The pipeline should extract a variety of entity types, not only Concept."""

    def test_entity_types_are_valid(self, single_pdf_result):
        """All entity_type values must be from the canonical set."""
        from .helpers import VALID_ENTITY_TYPES
        invalid = [
            (n["label"], n.get("entity_type"))
            for n in single_pdf_result["nodes"]
            if n.get("entity_type") not in VALID_ENTITY_TYPES
        ]
        assert not invalid, f"Nodes with unknown entity_type: {invalid[:5]}"

    def test_more_than_one_entity_type_present(self, single_pdf_result):
        """
        Acts contain persons (Minister, Registrar), organizations, and concepts.
        Having only one entity type suggests the classifier is degenerate.
        """
        types = {n.get("entity_type") for n in single_pdf_result["nodes"]}
        # Allow single-type for tiny documents; require diversity for bigger ones
        if single_pdf_result["node_count"] >= 10:
            assert len(types) >= 2, (
                f"Only one entity type extracted: {types}. "
                "Expected Person, Organization, Concept or similar mix."
            )

    def test_legal_domain_entities_identified(self, pipeline_results):
        """
        Each Act should yield at least one node whose label contains a
        domain-specific keyword.  Parameterised over (Act, keywords).
        """
        for key, keywords in ACT_DOMAIN_HINTS.items():
            result = pipeline_results[key]
            all_labels = " ".join(n.get("label", "") for n in result["nodes"]).lower()
            found = [kw for kw in keywords if kw.lower() in all_labels]
            assert found, (
                f"Act '{key}': none of {keywords} found in node labels. "
                f"Labels (first 5): {[n['label'] for n in result['nodes'][:5]]}"
            )


# ---------------------------------------------------------------------------
# 5. Edge integrity
# ---------------------------------------------------------------------------

class TestEdgeIntegrity:
    """Edges must connect real nodes with valid structure."""

    def test_edges_reference_real_nodes(self, single_pdf_result):
        node_ids = _node_ids(single_pdf_result)
        collection_id = single_pdf_result["collection_id"]
        for edge in single_pdf_result["edges"]:
            assert_edge_integrity(edge, node_ids, collection_id)

    def test_no_self_loops(self, single_pdf_result):
        """A node must not be connected to itself."""
        self_loops = [
            e for e in single_pdf_result["edges"]
            if (e.get("source") or e.get("source_id")) ==
               (e.get("target") or e.get("target_id"))
        ]
        assert not self_loops, f"Self-loop edges found: {self_loops[:3]}"

    def test_edge_weights_in_range(self, single_pdf_result):
        bad = [
            e for e in single_pdf_result["edges"]
            if not (0.0 <= float(e.get("weight", 0.5)) <= 1.0)
        ]
        assert not bad, f"Edges with weight outside [0,1]: {bad[:3]}"

    def test_relation_type_is_non_empty(self, single_pdf_result):
        empty_rel = [
            e["id"]
            for e in single_pdf_result["edges"]
            if not (e.get("relation_type") or e.get("edge_type") or "").strip()
        ]
        assert not empty_rel, (
            f"Edges with empty relation_type: {empty_rel[:5]}"
        )


# ---------------------------------------------------------------------------
# 6. Graph connectivity
# ---------------------------------------------------------------------------

class TestGraphConnectivity:
    """The resulting graph should not be a sea of isolated singletons."""

    def test_at_least_one_edge_created(self, single_pdf_result):
        """
        With extract_entities=True and ≥2 entities per chunk, at least one
        edge should be produced.
        """
        if single_pdf_result["node_count"] >= 2:
            assert single_pdf_result["edge_count"] >= 1, (
                f"No edges created despite {single_pdf_result['node_count']} nodes; "
                "relationship extraction may have failed"
            )

    def test_minimum_connectivity(self, single_pdf_result):
        """At least 30 % of nodes should participate in an edge."""
        if single_pdf_result["node_count"] < 4:
            return  # too few nodes to make meaningful assertions
        assert_graph_is_connected(
            single_pdf_result["nodes"],
            single_pdf_result["edges"],
            min_connected_fraction=0.30,
        )

    def test_no_dangling_edge_endpoints(self, single_pdf_result):
        """
        Every edge endpoint must resolve to a node in the same collection.
        Dangling edges break graph traversal.
        """
        node_ids = _node_ids(single_pdf_result)
        dangling: list[str] = []
        for e in single_pdf_result["edges"]:
            src = e.get("source") or e.get("source_id", "")
            tgt = e.get("target") or e.get("target_id", "")
            if src not in node_ids or tgt not in node_ids:
                dangling.append(e.get("id", "?"))
        assert not dangling, f"Edges with dangling endpoints: {dangling[:5]}"


# ---------------------------------------------------------------------------
# 7. Cross-document isolation
# ---------------------------------------------------------------------------

class TestCrossDocumentIsolation:
    """Nodes from one collection must not appear in another collection's graph."""

    def test_collections_do_not_share_nodes(self, pipeline_results):
        """
        Each collection was indexed separately.  Node IDs must not overlap
        across collections (UUIDs are unique by construction, but entity labels
        can legitimately repeat — this tests ID isolation).
        """
        all_node_ids: dict[str, str] = {}  # node_id → collection_id
        for key, result in pipeline_results.items():
            for node in result["nodes"]:
                nid = node["id"]
                if nid in all_node_ids:
                    pytest.fail(
                        f"Node {nid} appears in both collection "
                        f"'{all_node_ids[nid]}' and '{key}'"
                    )
                all_node_ids[nid] = key

    def test_collections_do_not_share_chunks(self, pipeline_results):
        """Chunk IDs must be globally unique across all three collections."""
        all_chunk_ids: dict[str, str] = {}
        for key, result in pipeline_results.items():
            for chunk in result["chunks"]:
                cid = chunk["id"]
                if cid in all_chunk_ids:
                    pytest.fail(
                        f"Chunk {cid} appears in both '{all_chunk_ids[cid]}' and '{key}'"
                    )
                all_chunk_ids[cid] = key
