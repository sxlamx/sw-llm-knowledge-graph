"""Documents router."""

import json
import re
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, Query
from app.auth.middleware import get_current_user
from app.db.lancedb_client import (
    get_collection,
    list_documents as db_list_documents,
    get_document as db_get_document,
    delete_document as db_delete_document,
    get_lancedb,
)
from app.models.schemas import DocumentResponse, DocumentListResponse

router = APIRouter()

_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
    re.IGNORECASE,
)


def _resolve_title(d: dict) -> str:
    """Return a human-readable title, falling back to filename if the stored title is a UUID."""
    raw = d.get("title", "")
    if raw and not _UUID_RE.match(raw):
        return raw
    file_path = d.get("file_path") or d.get("path") or ""
    return Path(file_path).name if file_path else (raw or "Untitled")


def _doc_to_response(d: dict) -> DocumentResponse:
    return DocumentResponse(
        id=d.get("id", ""),
        title=_resolve_title(d),
        file_type=d.get("file_type", "unknown"),
        path=d.get("file_path") or d.get("path"),
        doc_summary=d.get("doc_summary"),
        created_at=d.get("created_at"),
        metadata=json.loads(d["metadata"]) if isinstance(d.get("metadata"), str) else d.get("metadata"),
    )


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    collection_id: str,
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    collection = await get_collection(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    if collection.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    docs = await db_list_documents(collection_id, limit=limit, offset=offset)
    return DocumentListResponse(
        documents=[_doc_to_response(d) for d in docs],
        total=len(docs),
    )


@router.get("/{doc_id}")
async def get_document(
    doc_id: str,
    collection_id: str,
    current_user: dict = Depends(get_current_user),
):
    collection = await get_collection(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    if collection.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    doc = await db_get_document(doc_id, collection_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Fetch associated chunks
    chunks = []
    chunk_count = 0
    db = await get_lancedb()
    try:
        tbl = db.open_table(f"{collection_id}_chunks")
        chunk_rows = tbl.search().where(f'doc_id = "{doc_id}"', prefilter=True).to_list()
        chunks = [
            {"id": c.get("id"), "text": c.get("text"), "page": c.get("page"), "position": c.get("position")}
            for c in chunk_rows
        ]
        chunk_count = len(chunks)
    except Exception:
        pass

    return {
        "document": _doc_to_response(doc).model_dump(),
        "chunks": chunks,
        "chunk_count": chunk_count,
    }


@router.post("/dedup", status_code=200)
async def dedup_documents(
    collection_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Remove duplicate documents (same file_path), keeping the oldest by created_at."""
    collection = await get_collection(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    if collection.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    docs = await db_list_documents(collection_id, limit=10000)

    # Group by file_path, keep the one with the lowest created_at
    seen: dict[str, dict] = {}
    duplicates: list[str] = []
    for d in docs:
        key = d.get("file_path") or d.get("id")
        if key not in seen:
            seen[key] = d
        else:
            # Keep the older one
            existing = seen[key]
            if (d.get("created_at") or 0) < (existing.get("created_at") or 0):
                duplicates.append(existing["id"])
                seen[key] = d
            else:
                duplicates.append(d["id"])

    for doc_id in duplicates:
        await db_delete_document(doc_id, collection_id)

    return {"removed": len(duplicates), "remaining": len(seen)}


@router.delete("/{doc_id}", status_code=204)
async def delete_document(
    doc_id: str,
    collection_id: str,
    current_user: dict = Depends(get_current_user),
):
    collection = await get_collection(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    if collection.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    doc = await db_get_document(doc_id, collection_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    await db_delete_document(doc_id, collection_id)
