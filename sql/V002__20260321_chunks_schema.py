"""
Migration V002 — 2026-03-21 — Per-collection chunks and documents tables
Chunks are created dynamically per collection as {collection_id}_chunks.
Documents are created dynamically per collection as {collection_id}_documents.

This migration documents the canonical schema for these tables.
The runner validates existing tables have the required columns.
"""
import pyarrow as pa

DESCRIPTION = "Document per-collection chunk and document table schemas; add path column to existing chunk tables"

# Canonical chunk schema — columns listed in creation order.
# Embedding dimension must match HF_EMBED_MODEL (default Qwen3-Embedding-0.6B = 1024 dims).
CHUNK_SCHEMA = pa.schema([
    pa.field("id", pa.string()),
    pa.field("doc_id", pa.string()),
    pa.field("collection_id", pa.string()),
    pa.field("path", pa.string()),                          # source file path
    pa.field("text", pa.string()),                          # raw chunk text
    pa.field("contextual_text", pa.string()),               # prefixed text for embedding
    pa.field("position", pa.int64()),                       # chunk index within doc
    pa.field("token_count", pa.int64()),
    pa.field("page", pa.int64()),
    pa.field("topics", pa.string()),                        # JSON list of topic strings
    pa.field("created_at", pa.int64()),
    pa.field("embedding", pa.list_(pa.float32(), 1024)),    # vector embedding
])

# Canonical document schema
DOCUMENT_SCHEMA = pa.schema([
    pa.field("id", pa.string()),
    pa.field("collection_id", pa.string()),
    pa.field("path", pa.string()),
    pa.field("title", pa.string()),
    pa.field("file_type", pa.string()),
    pa.field("file_hash", pa.string()),                     # V002: added for dedup
    pa.field("doc_summary", pa.string()),
    pa.field("page_count", pa.int32()),
    pa.field("chunk_count", pa.int32()),
    pa.field("status", pa.string()),
    pa.field("created_at", pa.int64()),
    pa.field("updated_at", pa.int64()),
])


def up(db) -> None:
    """Add missing `path` column to any existing chunk tables."""
    import lancedb as _ldb
    table_names = db.table_names()
    for name in table_names:
        if name.endswith("_chunks"):
            tbl = db.open_table(name)
            if "path" not in tbl.schema.names:
                tbl.add_columns({"path": "cast('' as string)"})
                print(f"  + added path column to {name}")
