"""Documents router."""

from fastapi import APIRouter, HTTPException, Depends, Query
from app.auth.middleware import get_current_user
from app.db.lancedb_client import get_collection
from app.models.schemas import DocumentResponse, DocumentListResponse
import json

router = APIRouter()


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

    return DocumentListResponse(
        documents=[
            DocumentResponse(
                id=f"doc-{i}",
                title=f"Document {i+1}",
                file_type="pdf",
                path=None,
                doc_summary=None,
                created_at=None,
            )
            for i in range(offset, min(offset + limit, 0))
        ],
        total=0,
    )


@router.get("/{doc_id}", response_model=dict)
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

    return {
        "document": {
            "id": doc_id,
            "title": "Document",
            "file_type": "pdf",
            "path": None,
            "doc_summary": None,
        },
        "chunks": [],
        "chunk_count": 0,
    }
