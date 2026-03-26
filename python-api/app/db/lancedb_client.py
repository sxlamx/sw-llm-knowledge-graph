"""LanceDB client — all metadata and vector tables."""

import json
import logging
import lancedb
import pyarrow as pa
from typing import Optional
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)

from app.config import get_settings

settings = get_settings()

_db: Optional[lancedb.DBConnection] = None


async def get_lancedb() -> lancedb.DBConnection:
    global _db
    if _db is None:
        _db = lancedb.connect(settings.lancedb_path)
    return _db



_STR = pa.string()
_I32 = pa.int32()
_I64 = pa.int64()
_F32 = pa.float32()

_SYSTEM_SCHEMAS: dict[str, pa.Schema] = {
    "collections": pa.schema([
        pa.field("id", _STR), pa.field("user_id", _STR), pa.field("name", _STR),
        pa.field("description", _STR), pa.field("folder_path", _STR),
        pa.field("status", _STR), pa.field("doc_count", _I32),
        pa.field("created_at", _I64), pa.field("updated_at", _I64),
    ]),
    "users": pa.schema([
        pa.field("id", _STR), pa.field("google_sub", _STR), pa.field("email", _STR),
        pa.field("name", _STR), pa.field("avatar_url", _STR),
        pa.field("role", _STR), pa.field("status", _STR),
        pa.field("created_at", _I64), pa.field("last_login", _I64),
    ]),
    "ingest_jobs": pa.schema([
        pa.field("id", _STR), pa.field("collection_id", _STR), pa.field("status", _STR),
        pa.field("progress", _F32), pa.field("total_docs", _I32),
        pa.field("processed_docs", _I32), pa.field("error_msg", _STR),
        pa.field("started_at", _I64), pa.field("completed_at", _I64),
        pa.field("created_at", _I64), pa.field("options", _STR),
        pa.field("last_completed_file", _STR),  # checkpoint: last successfully flushed doc
    ]),
    "revoked_tokens": pa.schema([
        pa.field("jti", _STR), pa.field("revoked_at", _I64), pa.field("expires_at", _I64),
    ]),
    "drive_watch_channels": pa.schema([
        pa.field("channel_id", _STR), pa.field("resource_id", _STR),
        pa.field("collection_id", _STR), pa.field("folder_id", _STR),
        pa.field("access_token", _STR), pa.field("expiry_ms", _I64),
        pa.field("created_at", _I64),
    ]),
}


async def init_system_tables() -> None:
    db = await get_lancedb()
    for table_name, schema in _SYSTEM_SCHEMAS.items():
        try:
            db.create_table(table_name, schema=schema, exist_ok=True)
        except Exception:
            pass


async def get_collection_table(collection_id: str, table_suffix: str, schema: dict):
    db = await get_lancedb()
    table_name = f"{collection_id}_{table_suffix}"
    try:
        return db.open_table(table_name)
    except Exception:
        return db.create_table(table_name, schema=schema, exist_ok=True)


async def upsert_to_table(
    table_name: str,
    records: list[dict],
    pkey: str = "id",
) -> int:
    if not records:
        return 0
    db = await get_lancedb()
    try:
        tbl = db.open_table(table_name)
    except Exception:
        tbl = db.create_table(table_name, data=records, exist_ok=True)
        return len(records)

    for rec in records:
        try:
            tbl.update([f"={pkey}"], [rec])
        except Exception:
            pass

    return len(records)


async def get_collection(collection_id: str) -> Optional[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table("collections")
        result = tbl.search().where(f'id = "{collection_id}"', prefilter=True).limit(1).to_list()
        return result[0] if result else None
    except Exception:
        return None


async def get_user_by_google_sub(google_sub: str) -> Optional[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table("users")
        result = tbl.search().where(f'google_sub = "{google_sub}"', prefilter=True).limit(1).to_list()
        return result[0] if result else None
    except Exception:
        return None


async def get_user_by_email(email: str) -> Optional[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table("users")
        result = tbl.search().where(f'email = "{email}"', prefilter=True).limit(1).to_list()
        return result[0] if result else None
    except Exception:
        return None


async def list_users() -> list[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table("users")
        return tbl.search().to_list()
    except Exception:
        return []


async def update_user(user_id: str, updates: dict) -> Optional[dict]:
    existing = await get_user_by_id(user_id)
    if not existing:
        return None
    db = await get_lancedb()
    merged = {**existing, **updates}
    try:
        tbl = db.open_table("users")
        tbl.delete(f'id = "{user_id}"')
        tbl.add([merged])
    except Exception:
        return None
    return merged


async def get_user_by_id(user_id: str) -> Optional[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table("users")
        result = tbl.search().where(f'id = "{user_id}"', prefilter=True).limit(1).to_list()
        return result[0] if result else None
    except Exception:
        return None


async def _user_count() -> int:
    db = await get_lancedb()
    try:
        tbl = db.open_table("users")
        return len(tbl.search().to_list())
    except Exception:
        return 0


async def create_or_update_user(user_data: dict) -> str:
    db = await get_lancedb()
    now = datetime.utcnow()
    user_data["last_login"] = int(now.timestamp() * 1_000_000)

    try:
        tbl = db.open_table("users")
    except Exception:
        tbl = db.create_table("users", schema=_SYSTEM_SCHEMAS["users"], exist_ok=True)

    # Look up by google_sub first; fall back to email (catches pre-seeded admins)
    existing = await get_user_by_google_sub(user_data["google_sub"])
    if not existing:
        existing = await get_user_by_email(user_data.get("email", ""))

    if existing:
        # Preserve existing role/status — only update mutable profile fields
        user_data.setdefault("role",   existing.get("role",   "user"))
        user_data.setdefault("status", existing.get("status", "active"))
        tbl.delete(f'id = "{existing["id"]}"')
        user_data["id"] = existing["id"]
        user_data.setdefault("created_at", existing.get("created_at", int(now.timestamp() * 1_000_000)))
        tbl.add([user_data])
        return user_data["id"]
    else:
        user_data["created_at"] = int(now.timestamp() * 1_000_000)
        # First user ever → admin + active; subsequent new users → pending
        if await _user_count() == 0:
            user_data.setdefault("role",   "admin")
            user_data.setdefault("status", "active")
        else:
            user_data.setdefault("role",   "user")
            user_data.setdefault("status", "pending")
        tbl.add([user_data])
        return user_data["id"]


async def create_collection(collection_data: dict) -> str:
    db = await get_lancedb()
    now = datetime.utcnow()
    collection_data.setdefault("status", "active")
    collection_data.setdefault("doc_count", 0)
    collection_data["created_at"] = int(now.timestamp() * 1_000_000)
    collection_data["updated_at"] = int(now.timestamp() * 1_000_000)

    try:
        tbl = db.open_table("collections")
    except Exception:
        tbl = db.create_table("collections", schema=_SYSTEM_SCHEMAS["collections"], exist_ok=True)

    tbl.add([collection_data])
    return collection_data["id"]


async def update_collection(collection_id: str, updates: dict) -> None:
    db = await get_lancedb()
    updates["updated_at"] = int(datetime.utcnow().timestamp() * 1_000_000)
    try:
        tbl = db.open_table("collections")
        tbl.update([f'=id'], [updates])
    except Exception:
        pass


async def delete_collection(collection_id: str) -> None:
    db = await get_lancedb()
    try:
        tbl = db.open_table("collections")
        tbl.delete(f'id = "{collection_id}"')
    except Exception:
        pass


async def list_collections(user_id: str) -> list[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table("collections")
        return tbl.search().where(f'user_id = "{user_id}"', prefilter=True).to_list()
    except Exception:
        return []


async def create_ingest_job(job_data: dict) -> str:
    db = await get_lancedb()
    job_data["created_at"] = int(datetime.utcnow().timestamp() * 1_000_000)
    job_data["status"] = "pending"
    job_data["progress"] = 0.0
    job_data["processed_docs"] = 0
    job_data.setdefault("last_completed_file", "")

    try:
        tbl = db.open_table("ingest_jobs")
        # Migrate: add last_completed_file if the table predates this column
        if "last_completed_file" not in tbl.schema.names:
            tbl.add_columns({"last_completed_file": "cast('' as string)"})
    except Exception:
        tbl = db.create_table("ingest_jobs", schema=_SYSTEM_SCHEMAS["ingest_jobs"], exist_ok=True)

    tbl.add([job_data])
    return job_data["id"]


async def update_ingest_job(job_id: str, updates: dict) -> None:
    db = await get_lancedb()
    try:
        tbl = db.open_table("ingest_jobs")
        tbl.update([f'=id'], [updates])
    except Exception:
        pass


async def get_ingest_job(job_id: str) -> Optional[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table("ingest_jobs")
        result = tbl.search().where(f'id = "{job_id}"', prefilter=True).limit(1).to_list()
        return result[0] if result else None
    except Exception:
        return None


async def list_ingest_jobs(collection_id: Optional[str] = None) -> list[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table("ingest_jobs")
        if collection_id:
            return tbl.search().where(f'collection_id = "{collection_id}"', prefilter=True).to_list()
        return tbl.search().to_list()
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Drive watch channels
# ---------------------------------------------------------------------------


async def upsert_drive_channel(channel: dict) -> None:
    db = await get_lancedb()
    channel.setdefault("created_at", int(datetime.utcnow().timestamp() * 1_000_000))
    try:
        tbl = db.open_table("drive_watch_channels")
        tbl.delete(f'channel_id = "{channel["channel_id"]}"')
    except Exception:
        pass
    try:
        db.create_table("drive_watch_channels", data=[channel], exist_ok=True)
    except Exception:
        pass


async def get_drive_channel(channel_id: str) -> Optional[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table("drive_watch_channels")
        results = tbl.search().where(f'channel_id = "{channel_id}"', prefilter=True).limit(1).to_list()
        return results[0] if results else None
    except Exception:
        return None


async def delete_drive_channel(channel_id: str) -> None:
    db = await get_lancedb()
    try:
        tbl = db.open_table("drive_watch_channels")
        tbl.delete(f'channel_id = "{channel_id}"')
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

async def upsert_document(doc_data: dict) -> str:
    db = await get_lancedb()
    now = int(datetime.utcnow().timestamp() * 1_000_000)
    doc_data.setdefault("created_at", now)
    doc_data.setdefault("updated_at", now)
    doc_data.setdefault("file_hash", "")  # SHA-256 of the source file
    table_name = f"{doc_data['collection_id']}_documents"

    try:
        tbl = db.open_table(table_name)
        # Migrate: add file_hash column if the table predates this field
        schema = tbl.schema
        if "file_hash" not in schema.names:
            tbl.add_columns({"file_hash": "cast('' as string)"})
        # Deduplicate by explicit id (update in-place)
        existing = tbl.search().where(f'id = "{doc_data["id"]}"', prefilter=True).limit(1).to_list()
        if existing:
            tbl.delete(f'id = "{doc_data["id"]}"')
    except Exception:
        pass
    try:
        tbl = db.open_table(table_name)
    except Exception:
        tbl = db.create_table(table_name, data=[doc_data], exist_ok=True)
        return doc_data["id"]
    tbl.add([doc_data])
    return doc_data["id"]


async def get_document(doc_id: str, collection_id: str) -> Optional[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table(f"{collection_id}_documents")
        result = tbl.search().where(f'id = "{doc_id}"', prefilter=True).limit(1).to_list()
        return result[0] if result else None
    except Exception:
        return None


async def get_document_by_drive_file_id(drive_file_id: str, collection_id: str) -> Optional[dict]:
    """Return the first document whose metadata contains the given Drive file ID."""
    db = await get_lancedb()
    try:
        tbl = db.open_table(f"{collection_id}_documents")
        rows = tbl.search().to_list()
        for row in rows:
            meta_raw = row.get("metadata")
            if not meta_raw:
                continue
            try:
                meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
            except Exception:
                continue
            if meta.get("drive_file_id") == drive_file_id:
                return row
    except Exception:
        pass
    return None


async def list_documents(collection_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table(f"{collection_id}_documents")
        return tbl.search().limit(limit).offset(offset).to_list()
    except Exception:
        return []


async def get_document_by_file_path(collection_id: str, file_path: str) -> Optional[dict]:
    """Return the existing document record matching file_path within a collection, or None."""
    db = await get_lancedb()
    try:
        tbl = db.open_table(f"{collection_id}_documents")
        escaped = file_path.replace("\\", "\\\\").replace('"', '\\"')
        rows = tbl.search().where(f'file_path = "{escaped}"', prefilter=True).limit(1).to_list()
        return rows[0] if rows else None
    except Exception:
        return None


async def delete_document(doc_id: str, collection_id: str) -> None:
    db = await get_lancedb()
    try:
        tbl = db.open_table(f"{collection_id}_documents")
        tbl.delete(f'id = "{doc_id}"')
        # Also delete associated chunks
        try:
            chunks_tbl = db.open_table(f"{collection_id}_chunks")
            chunks_tbl.delete(f'doc_id = "{doc_id}"')
        except Exception:
            pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Chunk NER helpers
# ---------------------------------------------------------------------------

async def get_chunks_for_collection(collection_id: str) -> list[dict]:
    """Return id + text + ner_tags for all chunks (no embeddings)."""
    db = await get_lancedb()
    try:
        tbl = db.open_table(f"{collection_id}_chunks")
        schema_names = set(tbl.schema.names)
        cols = [c for c in ["id", "text", "ner_tags", "ner_version"] if c in schema_names]
        return (
            tbl.search()
            .select(cols)
            .limit(1_000_000)
            .to_list()
        )
    except Exception:
        return []


def _migrate_chunks_ner_columns(tbl) -> None:
    """Add NER tracking columns to an existing chunks table if absent."""
    schema_names = tbl.schema.names
    if "ner_tags" not in schema_names:
        tbl.add_columns({"ner_tags": "cast('' as string)"})
    if "ner_tagged" not in schema_names:
        tbl.add_columns({"ner_tagged": "cast(false as boolean)"})
    if "ner_tagged_at" not in schema_names:
        tbl.add_columns({"ner_tagged_at": "cast(0 as bigint)"})
    if "ner_version" not in schema_names:
        tbl.add_columns({"ner_version": "cast(0 as int)"})


async def update_chunk_ner_tags(collection_id: str, chunk_id: str, ner_tags_json: str, ner_version: int = 0) -> None:
    """Update ner_tags, ner_tagged, ner_tagged_at, and ner_version for a single chunk."""
    db = await get_lancedb()
    table_name = f"{collection_id}_chunks"
    now = int(datetime.utcnow().timestamp() * 1_000_000)
    try:
        tbl = db.open_table(table_name)
        _migrate_chunks_ner_columns(tbl)
        tbl.update(
            where=f'id = "{chunk_id}"',
            values={
                "ner_tags": ner_tags_json,
                "ner_tagged": True,
                "ner_tagged_at": now,
                "ner_version": ner_version,
            },
        )
    except Exception as e:
        logger.warning(f"update_chunk_ner_tags failed for {chunk_id}: {e}")
        raise  # re-raise so callers can count real errors


async def bulk_update_chunk_ner_tags(
    collection_id: str,
    updates: list[dict],  # each: {"id", "ner_tags", "ner_version"}
) -> int:
    """Batch-update NER fields for many chunks in one merge_insert call.

    Returns the number of successfully written rows.
    """
    if not updates:
        return 0
    db = await get_lancedb()
    table_name = f"{collection_id}_chunks"
    now = int(datetime.utcnow().timestamp() * 1_000_000)
    try:
        tbl = db.open_table(table_name)
        _migrate_chunks_ner_columns(tbl)

        # Build a minimal PyArrow table with only the NER columns + id for the merge key.
        import pyarrow as _pa
        ids       = [u["id"]         for u in updates]
        tags      = [u["ner_tags"]   for u in updates]
        versions  = [u["ner_version"] for u in updates]
        nows      = [now] * len(updates)
        trues     = [True] * len(updates)

        batch = _pa.table({
            "id":           _pa.array(ids,      type=_pa.string()),
            "ner_tags":     _pa.array(tags,     type=_pa.string()),
            "ner_tagged":   _pa.array(trues,    type=_pa.bool_()),
            "ner_tagged_at":_pa.array(nows,     type=_pa.int64()),
            "ner_version":  _pa.array(versions, type=_pa.int32()),
        })

        # merge_insert on "id": update matching rows, ignore non-matching
        (
            tbl.merge_insert("id")
            .when_matched_update_all()
            .execute(batch)
        )
        return len(updates)
    except Exception as e:
        logger.warning(f"bulk_update_chunk_ner_tags failed for {collection_id}: {e}")
        return 0


async def get_outdated_ner_chunks(collection_id: str, current_version: int) -> list[dict]:
    """Return chunks whose ner_version is below current_version (includes untagged chunks at v0)."""
    db = await get_lancedb()
    try:
        tbl = db.open_table(f"{collection_id}_chunks")
        _migrate_chunks_ner_columns(tbl)
        return (
            tbl.search()
            .where(f"ner_version < {current_version}", prefilter=True)
            .select(["id", "text"])
            .limit(1_000_000)
            .to_list()
        )
    except Exception:
        return []


async def get_chunk_ids_with_ner_labels(
    collection_id: str,
    labels: list[str],
) -> set[str]:
    """Return chunk IDs whose ner_tags JSON contains at least one of the given labels."""
    import json as _json
    chunks = await get_chunks_for_collection(collection_id)
    matched: set[str] = set()
    label_set = set(labels)
    for chunk in chunks:
        raw = chunk.get("ner_tags") or "[]"
        try:
            tags = _json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            continue
        for tag in tags:
            if tag.get("label") in label_set:
                matched.add(chunk["id"])
                break
    return matched


async def get_chunk_ids_with_ner_keywords(
    collection_id: str,
    keywords: list[str],
) -> set[str]:
    """Return chunk IDs whose ner_tags contain any of the given keyword texts (case-insensitive)."""
    import json as _json
    chunks = await get_chunks_for_collection(collection_id)
    matched: set[str] = set()
    kw_lower = {k.lower() for k in keywords}
    for chunk in chunks:
        raw = chunk.get("ner_tags") or "[]"
        try:
            tags = _json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            continue
        for tag in tags:
            if tag.get("text", "").lower() in kw_lower:
                matched.add(chunk["id"])
                break
    return matched


# ---------------------------------------------------------------------------
# Vector search
# ---------------------------------------------------------------------------

async def vector_search(
    collection_id: str,
    embedding: list[float],
    limit: int = 20,
    topics: Optional[list[str]] = None,
) -> list[dict]:
    """ANN vector search against the collection's chunk table."""
    db = await get_lancedb()
    try:
        tbl = db.open_table(f"{collection_id}_chunks")
        q = tbl.search(embedding, vector_column_name="embedding").limit(limit)
        results = q.to_list()
        # Attach score field name normalisation
        normalised = []
        for r in results:
            score = r.get("_distance", r.get("score", 0.0))
            # Convert distance to similarity (cosine distance → cosine sim)
            normalised.append({**r, "vector_score": max(0.0, 1.0 - float(score))})
        return normalised
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Graph nodes / edges
# ---------------------------------------------------------------------------

_NODE_SCHEMA = pa.schema([
    pa.field("id", pa.string()),
    pa.field("collection_id", pa.string()),
    pa.field("label", pa.string()),
    pa.field("entity_type", pa.string()),
    pa.field("description", pa.string()),
    pa.field("aliases", pa.list_(pa.string())),
    pa.field("confidence", pa.float32()),
    pa.field("source_chunk_ids", pa.list_(pa.string())),
    pa.field("topics", pa.list_(pa.string())),
    pa.field("properties", pa.string()),
    pa.field("created_at", pa.int64()),
    pa.field("updated_at", pa.int64()),
])


def _normalize_node(node: dict, now: int) -> dict:
    """Ensure list fields are always list[str] to prevent List(Null) schema inference."""
    return {
        "id": node.get("id", ""),
        "collection_id": node.get("collection_id", ""),
        "label": node.get("label", ""),
        "entity_type": node.get("entity_type", "Concept"),
        "description": node.get("description", "") or "",
        "aliases": [str(a) for a in (node.get("aliases") or [])],
        "confidence": float(node.get("confidence", 0.7)),
        "source_chunk_ids": [str(s) for s in (node.get("source_chunk_ids") or [])],
        "topics": [str(t) for t in (node.get("topics") or [])],
        "properties": json.dumps(node.get("properties") or {}),
        "created_at": node.get("created_at", now),
        "updated_at": now,
    }


async def upsert_graph_node(collection_id: str, node: dict) -> None:
    db = await get_lancedb()
    table_name = f"{collection_id}_nodes"
    now = int(datetime.utcnow().timestamp() * 1_000_000)
    record = _normalize_node(node, now)
    try:
        tbl = db.open_table(table_name)
        tbl.delete(f'id = "{record["id"]}"')
    except Exception:
        pass
    try:
        tbl = db.open_table(table_name)
    except Exception:
        tbl = db.create_table(table_name, schema=_NODE_SCHEMA, exist_ok=True)
    tbl.add([record])


async def list_graph_nodes(collection_id: str) -> list[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table(f"{collection_id}_nodes")
        return tbl.search().to_list()
    except Exception:
        return []


async def get_graph_node(collection_id: str, node_id: str) -> Optional[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table(f"{collection_id}_nodes")
        result = tbl.search().where(f'id = "{node_id}"', prefilter=True).limit(1).to_list()
        return result[0] if result else None
    except Exception:
        return None


async def update_graph_node(collection_id: str, node_id: str, updates: dict) -> Optional[dict]:
    existing = await get_graph_node(collection_id, node_id)
    if not existing:
        return None
    merged = {**existing, **updates}
    await upsert_graph_node(collection_id, merged)
    return merged


async def upsert_graph_edge(collection_id: str, edge: dict) -> None:
    db = await get_lancedb()
    table_name = f"{collection_id}_edges"
    edge.setdefault("created_at", int(datetime.utcnow().timestamp() * 1_000_000))
    try:
        tbl = db.open_table(table_name)
        tbl.delete(f'id = "{edge["id"]}"')
    except Exception:
        pass
    try:
        tbl = db.open_table(table_name)
    except Exception:
        tbl = db.create_table(table_name, data=[edge], exist_ok=True)
        return
    tbl.add([edge])


async def list_graph_edges(collection_id: str) -> list[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table(f"{collection_id}_edges")
        return tbl.search().to_list()
    except Exception:
        return []


async def get_graph_edge(collection_id: str, edge_id: str) -> Optional[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table(f"{collection_id}_edges")
        result = tbl.search().where(f'id = "{edge_id}"', prefilter=True).limit(1).to_list()
        return result[0] if result else None
    except Exception:
        return None


async def delete_graph_edge(collection_id: str, edge_id: str) -> None:
    db = await get_lancedb()
    try:
        tbl = db.open_table(f"{collection_id}_edges")
        tbl.delete(f'id = "{edge_id}"')
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Ontology
# ---------------------------------------------------------------------------

async def get_ontology(collection_id: str) -> Optional[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table("ontologies")
        result = tbl.search().where(f'collection_id = "{collection_id}"', prefilter=True).limit(1).to_list()
        return result[0] if result else None
    except Exception:
        return None


async def upsert_ontology(ontology: dict) -> None:
    db = await get_lancedb()
    ontology["updated_at"] = int(datetime.utcnow().timestamp() * 1_000_000)
    try:
        tbl = db.open_table("ontologies")
        tbl.delete(f'collection_id = "{ontology["collection_id"]}"')
    except Exception:
        pass
    try:
        tbl = db.open_table("ontologies")
    except Exception:
        db.create_table("ontologies", data=[ontology], exist_ok=True)
        return
    tbl.add([ontology])


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------

async def upsert_topic(collection_id: str, topic: dict) -> None:
    db = await get_lancedb()
    table_name = f"{collection_id}_topics"
    try:
        tbl = db.open_table(table_name)
        tbl.delete(f'id = "{topic["id"]}"')
    except Exception:
        pass
    try:
        tbl = db.open_table(table_name)
    except Exception:
        db.create_table(table_name, data=[topic], exist_ok=True)
        return
    tbl.add([topic])


async def list_topics(collection_id: str) -> list[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table(f"{collection_id}_topics")
        return tbl.search().to_list()
    except Exception:
        return []


# ---------------------------------------------------------------------------
# User feedback
# ---------------------------------------------------------------------------

async def insert_user_feedback(feedback: dict) -> None:
    db = await get_lancedb()
    feedback.setdefault("created_at", int(datetime.utcnow().timestamp() * 1_000_000))
    try:
        tbl = db.open_table("user_feedback")
    except Exception:
        db.create_table("user_feedback", data=[feedback], exist_ok=True)
        return
    tbl.add([feedback])


# ---------------------------------------------------------------------------
# Token revocation blocklist
# ---------------------------------------------------------------------------


async def revoke_token_db(jti: str, expires_at_us: int) -> None:
    """Persist a revoked JWT ID to the database blocklist."""
    db = await get_lancedb()
    record = {
        "jti": jti,
        "revoked_at": int(datetime.utcnow().timestamp() * 1_000_000),
        "expires_at": expires_at_us,
    }
    try:
        tbl = db.open_table("revoked_tokens")
    except Exception:
        db.create_table("revoked_tokens", data=[record], exist_ok=True)
        return
    tbl.add([record])


async def is_token_revoked(jti: str) -> bool:
    """Return True if the given JTI appears in the revocation blocklist."""
    db = await get_lancedb()
    try:
        tbl = db.open_table("revoked_tokens")
        results = tbl.search().where(f"jti = '{jti}'").limit(1).to_list()
        return len(results) > 0
    except Exception:
        return False


async def get_node_summary(collection_id: str, node_id: str) -> Optional[dict]:
    """Return cached summary dict for a node, or None if not found."""
    db = await get_lancedb()
    try:
        tbl = db.open_table(f"{collection_id}_node_summaries")
        rows = tbl.search().where(f'node_id = "{node_id}"', prefilter=True).limit(1).to_list()
        return rows[0] if rows else None
    except Exception:
        return None


async def upsert_node_summary(collection_id: str, node_id: str, summary: str, chunk_hash: str) -> None:
    """Insert or replace the summary for a node."""
    db = await get_lancedb()
    now = int(datetime.utcnow().timestamp() * 1_000_000)
    table_name = f"{collection_id}_node_summaries"
    record = {
        "node_id": node_id,
        "collection_id": collection_id,
        "summary": summary,
        "chunk_hash": chunk_hash,
        "created_at": now,
        "updated_at": now,
    }
    try:
        tbl = db.open_table(table_name)
        tbl.delete(f'node_id = "{node_id}"')
        tbl.add([record])
    except Exception:
        try:
            db.create_table(table_name, data=[record], exist_ok=True)
        except Exception:
            pass


async def purge_expired_revocations() -> int:
    """Delete blocklist entries whose tokens have already expired (housekeeping)."""
    db = await get_lancedb()
    now_us = int(datetime.utcnow().timestamp() * 1_000_000)
    try:
        tbl = db.open_table("revoked_tokens")
        tbl.delete(f"expires_at < {now_us}")
        return 1
    except Exception:
        return 0
