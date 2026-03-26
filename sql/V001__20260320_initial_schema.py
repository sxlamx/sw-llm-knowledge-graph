"""
Migration V001 — 2026-03-20 — Initial schema
Creates all system tables if they don't exist.
These are idempotent (exist_ok=True / exist checks).
"""
import pyarrow as pa

DESCRIPTION = "Create system tables: users, collections, ingest_jobs, revoked_tokens, drive_watch_channels"

def _create_if_absent(db, name: str, schema: pa.Schema) -> None:
    """Create table only if it doesn't exist. Skip if already present."""
    try:
        db.open_table(name)
        return  # already exists
    except Exception:
        pass
    db.create_table(name, schema=schema)


def up(db) -> None:
    _STR = pa.string()
    _I32 = pa.int32()
    _I64 = pa.int64()
    _F32 = pa.float32()

    # users
    _create_if_absent(db, "users", pa.schema([
        pa.field("id", _STR),
        pa.field("google_sub", _STR),
        pa.field("email", _STR),
        pa.field("name", _STR),
        pa.field("avatar_url", _STR),
        pa.field("role", _STR),       # "admin" | "user"
        pa.field("status", _STR),     # "active" | "pending" | "blocked"
        pa.field("created_at", _I64),
        pa.field("last_login", _I64),
    ]))

    # collections
    _create_if_absent(db, "collections", pa.schema([
        pa.field("id", _STR),
        pa.field("user_id", _STR),
        pa.field("name", _STR),
        pa.field("description", _STR),
        pa.field("folder_path", _STR),
        pa.field("status", _STR),
        pa.field("doc_count", _I32),
        pa.field("created_at", _I64),
        pa.field("updated_at", _I64),
    ]))

    # ingest_jobs
    _create_if_absent(db, "ingest_jobs", pa.schema([
        pa.field("id", _STR),
        pa.field("collection_id", _STR),
        pa.field("status", _STR),
        pa.field("progress", _F32),
        pa.field("total_docs", _I32),
        pa.field("processed_docs", _I32),
        pa.field("error_msg", _STR),
        pa.field("started_at", _I64),
        pa.field("completed_at", _I64),
        pa.field("created_at", _I64),
        pa.field("options", _STR),
    ]))

    # revoked_tokens
    _create_if_absent(db, "revoked_tokens", pa.schema([
        pa.field("jti", _STR),
        pa.field("revoked_at", _I64),
        pa.field("expires_at", _I64),
    ]))

    # drive_watch_channels
    _create_if_absent(db, "drive_watch_channels", pa.schema([
        pa.field("channel_id", _STR),
        pa.field("resource_id", _STR),
        pa.field("collection_id", _STR),
        pa.field("folder_id", _STR),
        pa.field("access_token", _STR),
        pa.field("expiry_ms", _I64),
        pa.field("created_at", _I64),
    ]))
