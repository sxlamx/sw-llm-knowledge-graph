"""Documents router."""

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


def _doc_to_response(d: dict) -> DocumentResponse:
    return DocumentResponse(
        id=d.get("id", ""),
        title=d.get("title", d.get("file_path", "Untitled")),
        file_type=d.get("file_type", "unknown"),
        path=d.get("file_path") or d.get("path"),
        doc_summary=d.get("doc_summary"),
        created_at=d.get("created_at"),
        metadata=d.get("metadata"),
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
        chunk_rows = tbl.query().where(f'doc_id = "{doc_id}"').to_list()
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
