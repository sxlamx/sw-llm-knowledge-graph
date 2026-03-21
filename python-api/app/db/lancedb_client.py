"""LanceDB client — all metadata and vector tables."""

import lancedb
from typing import Optional
import uuid
from datetime import datetime

from app.config import get_settings

settings = get_settings()

_db: Optional[lancedb.LanceDB] = None


async def get_lancedb() -> lancedb.LanceDB:
    global _db
    if _db is None:
        _db = lancedb.LanceDB.connect(settings.lancedb_path)
    return _db


async def init_system_tables() -> None:
    db = await get_lancedb()

    collections_schema = {
        "id": "string",
        "user_id": "string",
        "name": "string",
        "description": "string",
        "folder_path": "string",
        "status": "string",
        "doc_count": "int32",
        "created_at": "int64",
        "updated_at": "int64",
    }

    users_schema = {
        "id": "string",
        "google_sub": "string",
        "email": "string",
        "name": "string",
        "avatar_url": "string",
        "created_at": "int64",
        "last_login": "int64",
    }

    ingest_jobs_schema = {
        "id": "string",
        "collection_id": "string",
        "status": "string",
        "progress": "float32",
        "total_docs": "int32",
        "processed_docs": "int32",
        "error_msg": "string",
        "started_at": "int64",
        "completed_at": "int64",
        "created_at": "int64",
        "options": "string",
    }

    revoked_tokens_schema = {
        "jti": "string",        # JWT ID (unique per token)
        "revoked_at": "int64",  # epoch microseconds
        "expires_at": "int64",  # epoch microseconds; for cleanup
    }

    drive_watch_channels_schema = {
        "channel_id": "string",     # Google Drive channel ID (primary key)
        "resource_id": "string",    # Google Drive resource ID
        "collection_id": "string",  # our collection
        "folder_id": "string",      # Drive folder being watched
        "access_token": "string",   # OAuth token for re-sync calls
        "expiry_ms": "int64",       # channel expiry (epoch ms)
        "created_at": "int64",
    }

    for table_name, schema in [
        ("collections", collections_schema),
        ("users", users_schema),
        ("ingest_jobs", ingest_jobs_schema),
        ("revoked_tokens", revoked_tokens_schema),
        ("drive_watch_channels", drive_watch_channels_schema),
    ]:
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
        result = tbl.query().where(f'id = "{collection_id}"').to_list()
        return result[0] if result else None
    except Exception:
        return None


async def get_user_by_google_sub(google_sub: str) -> Optional[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table("users")
        result = tbl.query().where(f'google_sub = "{google_sub}"').to_list()
        return result[0] if result else None
    except Exception:
        return None


async def get_user_by_id(user_id: str) -> Optional[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table("users")
        result = tbl.query().where(f'id = "{user_id}"').to_list()
        return result[0] if result else None
    except Exception:
        return None


async def create_or_update_user(user_data: dict) -> str:
    db = await get_lancedb()
    now = datetime.utcnow()
    user_data["last_login"] = int(now.timestamp() * 1_000_000)

    try:
        tbl = db.open_table("users")
    except Exception:
        tbl = db.create_table("users", schema={
            "id": "string", "google_sub": "string", "email": "string",
            "name": "string", "avatar_url": "string",
            "created_at": "int64", "last_login": "int64",
        }, exist_ok=True)

    existing = await get_user_by_google_sub(user_data["google_sub"])
    if existing:
        tbl.update([f'=id'], [user_data])
        return user_data["id"]
    else:
        user_data["created_at"] = int(now.timestamp() * 1_000_000)
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
        tbl = db.create_table("collections", schema={
            "id": "string", "user_id": "string", "name": "string",
            "description": "string", "folder_path": "string",
            "status": "string", "doc_count": "int32",
            "created_at": "int64", "updated_at": "int64",
        }, exist_ok=True)

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
        return tbl.query().where(f'user_id = "{user_id}"').to_list()
    except Exception:
        return []


async def create_ingest_job(job_data: dict) -> str:
    db = await get_lancedb()
    job_data["created_at"] = int(datetime.utcnow().timestamp() * 1_000_000)
    job_data["status"] = "pending"
    job_data["progress"] = 0.0
    job_data["processed_docs"] = 0

    try:
        tbl = db.open_table("ingest_jobs")
    except Exception:
        tbl = db.create_table("ingest_jobs", schema={
            "id": "string", "collection_id": "string", "status": "string",
            "progress": "float32", "total_docs": "int32", "processed_docs": "int32",
            "error_msg": "string", "started_at": "int64", "completed_at": "int64",
            "created_at": "int64", "options": "string",
        }, exist_ok=True)

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
        result = tbl.query().where(f'id = "{job_id}"').to_list()
        return result[0] if result else None
    except Exception:
        return None


async def list_ingest_jobs(collection_id: Optional[str] = None) -> list[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table("ingest_jobs")
        if collection_id:
            return tbl.query().where(f'collection_id = "{collection_id}"').to_list()
        return tbl.query().to_list()
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
        results = tbl.query().where(f'channel_id = "{channel_id}"').to_list()
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
    table_name = f"{doc_data['collection_id']}_documents"
    try:
        tbl = db.open_table(table_name)
        existing = tbl.query().where(f'id = "{doc_data["id"]}"').to_list()
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
        result = tbl.query().where(f'id = "{doc_id}"').to_list()
        return result[0] if result else None
    except Exception:
        return None


async def list_documents(collection_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table(f"{collection_id}_documents")
        return tbl.query().limit(limit).offset(offset).to_list()
    except Exception:
        return []


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
        q = tbl.query().nearest_to(embedding).limit(limit)
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

async def upsert_graph_node(collection_id: str, node: dict) -> None:
    db = await get_lancedb()
    table_name = f"{collection_id}_nodes"
    now = int(datetime.utcnow().timestamp() * 1_000_000)
    node.setdefault("created_at", now)
    node["updated_at"] = now
    try:
        tbl = db.open_table(table_name)
        tbl.delete(f'id = "{node["id"]}"')
    except Exception:
        pass
    try:
        tbl = db.open_table(table_name)
    except Exception:
        tbl = db.create_table(table_name, data=[node], exist_ok=True)
        return
    tbl.add([node])


async def list_graph_nodes(collection_id: str) -> list[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table(f"{collection_id}_nodes")
        return tbl.query().to_list()
    except Exception:
        return []


async def get_graph_node(collection_id: str, node_id: str) -> Optional[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table(f"{collection_id}_nodes")
        result = tbl.query().where(f'id = "{node_id}"').to_list()
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
        return tbl.query().to_list()
    except Exception:
        return []


async def get_graph_edge(collection_id: str, edge_id: str) -> Optional[dict]:
    db = await get_lancedb()
    try:
        tbl = db.open_table(f"{collection_id}_edges")
        result = tbl.query().where(f'id = "{edge_id}"').to_list()
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
        result = tbl.query().where(f'collection_id = "{collection_id}"').to_list()
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
        return tbl.query().to_list()
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
