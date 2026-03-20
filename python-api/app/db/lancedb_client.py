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

    for table_name, schema in [
        ("collections", collections_schema),
        ("users", users_schema),
        ("ingest_jobs", ingest_jobs_schema),
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
