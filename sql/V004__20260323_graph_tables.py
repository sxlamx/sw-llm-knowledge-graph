"""
Migration V004 — 2026-03-23 — Graph node and edge tables
Per-collection {collection_id}_nodes and {collection_id}_edges tables.
These are created on-demand by the graph extraction pipeline.

This migration documents their canonical schemas.
"""
import pyarrow as pa

DESCRIPTION = "Document per-collection graph node and edge table schemas"

NODE_SCHEMA = pa.schema([
    pa.field("id", pa.string()),
    pa.field("collection_id", pa.string()),
    pa.field("label", pa.string()),
    pa.field("entity_type", pa.string()),       # PERSON | ORG | LEGISLATION | CONCEPT | …
    pa.field("description", pa.string()),
    pa.field("aliases", pa.list_(pa.string())),
    pa.field("confidence", pa.float32()),
    pa.field("source_chunk_ids", pa.list_(pa.string())),
    pa.field("topics", pa.list_(pa.string())),
    pa.field("properties", pa.string()),        # JSON blob for extra attributes
    pa.field("created_at", pa.int64()),
    pa.field("updated_at", pa.int64()),
])

# Edge schema is inferred at insert time (no fixed schema enforced yet).
# Required fields: id, collection_id, source_id, target_id, relation_type,
#                  weight(float), properties(JSON str), created_at(int64)
EDGE_REQUIRED_FIELDS = [
    "id", "collection_id", "source_id", "target_id",
    "relation_type", "weight", "properties", "created_at",
]


def up(db) -> None:
    """No-op: node/edge tables are created on-demand. This migration only records the schema."""
    pass
