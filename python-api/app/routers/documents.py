"""Documents router."""

from fastapi import APIRouter, HTTPException, Depends, Query
from app.auth.middleware import get_current_user
from app.db.lancedb_client import get_collection, get_lancedb
from app.models.schemas import DocumentResponse, DocumentListResponse, DocumentDetailResponse
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


async def _get_chunks_for_doc(collection_id: str, doc_id: str) -> list[dict]:
    """Return all full-column chunk rows for a specific document."""
    try:
        db = await get_lancedb()
        tbl = db.open_table(f"{collection_id}_chunks")
        escaped = doc_id.replace('"', '\\"')
        return (
            tbl.search()
            .where(f'doc_id = "{escaped}"', prefilter=True)
            .select(["id", "doc_id", "path", "text", "position", "page", "created_at", "doc_summary"])
            .limit(50_000)
            .to_list()
        )
    except Exception as e:
        logger.warning(f"_get_chunks_for_doc failed: {e}")
        return []


async def _get_chunks_for_collection(collection_id: str) -> list[dict]:
    """Return lightweight metadata rows for all chunks in a collection."""
    try:
        db = await get_lancedb()
        tbl = db.open_table(f"{collection_id}_chunks")
        schema_names = set(tbl.schema.names)
        select_cols = [c for c in ["doc_id", "path", "created_at", "doc_summary"] if c in schema_names]
        return (
            tbl.search()
            .where("doc_id IS NOT NULL", prefilter=True)
            .select(select_cols)
            .limit(1_000_000)
            .to_list()
        )
    except Exception as e:
        logger.warning(f"_get_chunks_for_collection failed: {e}")
        return []


def _chunks_to_documents(chunks: list[dict]) -> list[DocumentResponse]:
    """Aggregate chunks by doc_id and return one DocumentResponse per document."""
    seen: dict[str, DocumentResponse] = {}
    for chunk in chunks:
        doc_id = chunk.get("doc_id", "")
        if not doc_id or doc_id in seen:
            continue
        path = chunk.get("path") or chunk.get("source_path")
        title = (path.split("/")[-1] if path else None) or f"Document {doc_id[:8]}"
        file_ext = title.rsplit(".", 1)[-1].lower() if "." in title else "unknown"
        seen[doc_id] = DocumentResponse(
            id=doc_id,
            title=title,
            file_type=file_ext,
            path=path,
            doc_summary=chunk.get("doc_summary"),
            created_at=chunk.get("created_at"),
        )
    return list(seen.values())


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

    chunks = await _get_chunks_for_collection(collection_id)
    documents = _chunks_to_documents(chunks)
    total = len(documents)
    page = documents[offset:offset + limit]

    return DocumentListResponse(documents=page, total=total)


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

    doc_chunks = await _get_chunks_for_doc(collection_id, doc_id)

    if not doc_chunks:
        raise HTTPException(status_code=404, detail="Document not found")

    first = doc_chunks[0]
    path = first.get("path") or first.get("source_path")
    title = (path.split("/")[-1] if path else None) or f"Document {doc_id[:8]}"
    file_ext = title.rsplit(".", 1)[-1].lower() if "." in title else "unknown"

    doc = DocumentResponse(
        id=doc_id,
        title=title,
        file_type=file_ext,
        path=path,
        doc_summary=first.get("doc_summary"),
        created_at=first.get("created_at"),
    )

    safe_chunks = [
        {"id": c.get("id"), "text": c.get("text"), "position": c.get("position"), "page": c.get("page")}
        for c in doc_chunks
    ]

    return {"document": doc.model_dump(), "chunks": safe_chunks, "chunk_count": len(safe_chunks)}
