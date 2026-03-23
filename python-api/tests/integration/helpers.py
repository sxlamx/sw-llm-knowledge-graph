"""
Reusable assertion helpers for integration tests.

These helpers encode the invariants that must hold at every stage of the
production pipeline:  extraction → chunking → embedding → graph → ontology.
Import and call them from any test file to avoid duplicating assertion logic.
"""

from __future__ import annotations

from typing import Any

# Embedding dimension used throughout integration tests (matches conftest.EMBEDDING_DIM)
EMBEDDING_DIM = 4


# ---------------------------------------------------------------------------
# Default ontology values (matches app/routers/ontology.py defaults)
# ---------------------------------------------------------------------------

DEFAULT_ENTITY_TYPES = {"Person", "Organization", "Location", "Concept", "Event"}
DEFAULT_RELATION_TYPES = {"WORKS_AT", "FOUNDED", "LOCATED_IN", "RELATED_TO", "PART_OF", "LED_BY"}

VALID_ENTITY_TYPES = DEFAULT_ENTITY_TYPES | {"Document", "Topic"}


# ---------------------------------------------------------------------------
# Chunk assertions
# ---------------------------------------------------------------------------

def assert_chunk_integrity(chunk: dict, collection_id: str, embedding_dim: int) -> None:
    """
    Assert that a persisted chunk record satisfies all structural invariants:
    - Required fields present
    - Text is non-empty
    - Position is a non-negative integer
    - Embedding vector has the correct dimension and contains finite floats
    - Token count is positive
    - Page is non-negative
    - collection_id matches
    """
    required = {"id", "doc_id", "collection_id", "text", "position", "token_count", "page", "embedding"}
    missing = required - set(chunk.keys())
    assert not missing, f"Chunk {chunk.get('id', '?')} missing fields: {missing}"

    assert chunk["collection_id"] == collection_id, (
        f"Chunk collection_id mismatch: {chunk['collection_id']} != {collection_id}"
    )
    assert isinstance(chunk["text"], str) and chunk["text"].strip(), (
        f"Chunk {chunk['id']} has empty text"
    )
    assert isinstance(chunk["position"], int) and chunk["position"] >= 0, (
        f"Chunk {chunk['id']} has invalid position: {chunk['position']}"
    )
    assert isinstance(chunk["token_count"], int) and chunk["token_count"] > 0, (
        f"Chunk {chunk['id']} has non-positive token_count: {chunk['token_count']}"
    )
    assert isinstance(chunk["page"], int) and chunk["page"] >= 0, (
        f"Chunk {chunk['id']} has invalid page: {chunk['page']}"
    )

    emb = chunk["embedding"]
    assert isinstance(emb, list) and len(emb) == embedding_dim, (
        f"Chunk {chunk['id']} embedding dim {len(emb)} != expected {embedding_dim}"
    )
    assert all(isinstance(v, float) for v in emb), (
        f"Chunk {chunk['id']} embedding contains non-float values"
    )
    import math
    assert all(math.isfinite(v) for v in emb), (
        f"Chunk {chunk['id']} embedding contains NaN/Inf"
    )


def assert_chunks_are_ordered(chunks: list[dict]) -> None:
    """Assert that chunks are in ascending position order (sequential coverage)."""
    positions = [c["position"] for c in chunks]
    assert positions == sorted(positions), (
        f"Chunks are not in position order: {positions[:10]}…"
    )


def assert_chunks_cover_pages(chunks: list[dict], expected_min_pages: int = 1) -> None:
    """Assert that chunks reference at least the expected number of distinct pages."""
    pages = {c["page"] for c in chunks}
    assert len(pages) >= expected_min_pages, (
        f"Only {len(pages)} unique pages referenced; expected >= {expected_min_pages}"
    )


def assert_no_duplicate_chunk_ids(chunks: list[dict]) -> None:
    """Assert all chunk IDs are unique."""
    ids = [c["id"] for c in chunks]
    assert len(ids) == len(set(ids)), (
        f"Duplicate chunk IDs detected: {len(ids) - len(set(ids))} duplicates"
    )


# ---------------------------------------------------------------------------
# Document assertions
# ---------------------------------------------------------------------------

def assert_document_integrity(doc: dict, collection_id: str, pdf_path: str) -> None:
    """Assert that a persisted document record is complete and points to the right file."""
    required = {"id", "collection_id", "title", "file_path", "file_type"}
    missing = required - set(doc.keys())
    assert not missing, f"Document {doc.get('id', '?')} missing fields: {missing}"

    assert doc["collection_id"] == collection_id
    assert isinstance(doc["title"], str) and doc["title"].strip(), (
        "Document title must be non-empty"
    )
    assert doc["file_path"] == pdf_path, (
        f"Document file_path mismatch: {doc['file_path']} != {pdf_path}"
    )
    assert doc["file_type"].lower() in {"pdf", "application/pdf", "unknown"}, (
        f"Unexpected file_type: {doc['file_type']}"
    )


# ---------------------------------------------------------------------------
# Graph node assertions
# ---------------------------------------------------------------------------

def assert_node_integrity(
    node: dict,
    chunk_ids: set[str],
    collection_id: str,
) -> None:
    """
    Assert that a graph node has valid structure:
    - Required fields present
    - Label is non-empty
    - Entity type is from the recognised set
    - Confidence is in [0, 1]
    - source_chunk_ids all reference real persisted chunks
    """
    required = {"id", "label", "entity_type", "collection_id"}
    missing = required - set(node.keys())
    assert not missing, f"Node {node.get('id', '?')} missing fields: {missing}"

    assert node["collection_id"] == collection_id
    label = node.get("label", "")
    assert isinstance(label, str) and label.strip(), (
        f"Node {node['id']} has empty label"
    )

    etype = node.get("entity_type", "")
    assert etype in VALID_ENTITY_TYPES, (
        f"Node '{label}' has invalid entity_type: '{etype}'. "
        f"Valid types: {VALID_ENTITY_TYPES}"
    )

    confidence = float(node.get("confidence", 0.5))
    assert 0.0 <= confidence <= 1.0, (
        f"Node '{label}' confidence out of range: {confidence}"
    )

    for cid in node.get("source_chunk_ids") or []:
        assert cid in chunk_ids, (
            f"Node '{label}' references non-existent chunk: {cid}"
        )


def assert_no_duplicate_node_labels(nodes: list[dict]) -> None:
    """
    Assert that the pipeline's entity-merge step deduplicated case-insensitive labels.
    Duplicate labels would indicate the merge in _flush_graph didn't fire correctly.
    """
    labels_lower = [n["label"].strip().lower() for n in nodes]
    from collections import Counter
    dupes = {l: c for l, c in Counter(labels_lower).items() if c > 1}
    assert not dupes, f"Duplicate node labels after merge: {dupes}"


# ---------------------------------------------------------------------------
# Graph edge assertions
# ---------------------------------------------------------------------------

def assert_edge_integrity(
    edge: dict,
    node_ids: set[str],
    collection_id: str,
) -> None:
    """
    Assert that a graph edge:
    - Has source and target fields (either source/target or source_id/target_id)
    - Both endpoints exist in the node set
    - Has no self-loops
    - Weight is in [0, 1]
    """
    src = edge.get("source") or edge.get("source_id")
    tgt = edge.get("target") or edge.get("target_id")

    assert src, f"Edge {edge.get('id', '?')} missing source"
    assert tgt, f"Edge {edge.get('id', '?')} missing target"
    assert edge.get("collection_id") == collection_id, (
        f"Edge collection_id mismatch: {edge.get('collection_id')} != {collection_id}"
    )
    assert src != tgt, f"Self-loop edge detected on node {src}"
    assert src in node_ids, f"Edge source {src} not in node set"
    assert tgt in node_ids, f"Edge target {tgt} not in node set"

    weight = float(edge.get("weight", 0.5))
    assert 0.0 <= weight <= 1.0, f"Edge weight out of range: {weight}"

    relation = edge.get("relation_type") or edge.get("edge_type") or ""
    assert isinstance(relation, str) and relation.strip(), (
        f"Edge {edge.get('id', '?')} has empty relation_type"
    )


def assert_graph_is_connected(
    nodes: list[dict],
    edges: list[dict],
    min_connected_fraction: float = 0.5,
) -> None:
    """
    Assert that at least `min_connected_fraction` of nodes participate in at least one edge.
    This detects pipeline failures where extraction ran but graph construction silently dropped edges.
    """
    if not nodes or not edges:
        return

    node_ids = {n["id"] for n in nodes}
    connected = set()
    for e in edges:
        src = e.get("source") or e.get("source_id")
        tgt = e.get("target") or e.get("target_id")
        if src in node_ids:
            connected.add(src)
        if tgt in node_ids:
            connected.add(tgt)

    fraction = len(connected) / len(node_ids)
    assert fraction >= min_connected_fraction, (
        f"Only {fraction:.0%} of nodes are connected; expected >= {min_connected_fraction:.0%}"
    )


# ---------------------------------------------------------------------------
# Ontology assertions
# ---------------------------------------------------------------------------

def assert_ontology_integrity(ontology: dict) -> None:
    """
    Assert that an ontology dict contains all required default entity and relation types
    and that each type has a non-empty description.
    """
    entity_types: dict = ontology.get("entity_types") or {}
    relation_types: dict = ontology.get("relation_types") or {}

    missing_entities = DEFAULT_ENTITY_TYPES - set(entity_types.keys())
    assert not missing_entities, (
        f"Ontology missing default entity types: {missing_entities}"
    )

    missing_relations = DEFAULT_RELATION_TYPES - set(relation_types.keys())
    assert not missing_relations, (
        f"Ontology missing default relation types: {missing_relations}"
    )

    for name, defn in entity_types.items():
        desc = defn.get("description", "") if isinstance(defn, dict) else str(defn)
        assert desc.strip(), f"Entity type '{name}' has empty description"

    for name, defn in relation_types.items():
        desc = defn.get("description", "") if isinstance(defn, dict) else str(defn)
        assert desc.strip(), f"Relation type '{name}' has empty description"


def assert_ontology_covers_extracted_types(
    ontology: dict,
    nodes: list[dict],
) -> None:
    """
    Assert that every entity_type seen in extracted nodes is defined in the ontology.
    Catches cases where the LLM hallucinated an entity type not in the schema.
    """
    entity_types: set[str] = set((ontology.get("entity_types") or {}).keys())
    for node in nodes:
        etype = node.get("entity_type", "")
        assert etype in entity_types, (
            f"Node '{node.get('label')}' has entity_type '{etype}' "
            f"not present in ontology: {entity_types}"
        )


# ---------------------------------------------------------------------------
# Pipeline result summary assertions
# ---------------------------------------------------------------------------

def assert_pipeline_completed(result: dict) -> None:
    """Assert a pipeline result dict shows a successful completion."""
    assert result["status"] == "completed", (
        f"Pipeline did not complete; status={result['status']!r}, "
        f"error={result.get('error')!r}"
    )
    assert result["chunk_count"] > 0, "Pipeline produced zero chunks"
    assert result["doc_count"] > 0, "Pipeline recorded zero documents"
