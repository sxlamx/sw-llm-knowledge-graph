"""Collections router."""

from fastapi import APIRouter, HTTPException, Depends, Request
from app.auth.middleware import get_current_user
from app.db.lancedb_client import (
    create_collection, get_collection, update_collection,
    delete_collection, list_collections,
)
from app.models.schemas import (
    CollectionCreate, CollectionResponse, CollectionListResponse,
)
import uuid

router = APIRouter()


@router.get("", response_model=CollectionListResponse)
async def list_user_collections(current_user: dict = Depends(get_current_user)):
    collections = await list_collections(current_user["id"])
    return CollectionListResponse(
        collections=[
            CollectionResponse(
                id=c.get("id", ""),
                name=c.get("name", ""),
                description=c.get("description"),
                folder_path=c.get("folder_path"),
                status=c.get("status", "active"),
                doc_count=c.get("doc_count", 0),
                created_at=c.get("created_at"),
                updated_at=c.get("updated_at"),
            )
            for c in collections
        ]
    )


@router.post("", status_code=201, response_model=CollectionResponse)
async def create_new_collection(
    body: CollectionCreate,
    current_user: dict = Depends(get_current_user),
):
    collection_id = str(uuid.uuid4())
    data = {
        "id": collection_id,
        "user_id": current_user["id"],
        "name": body.name,
        "description": body.description or "",
        "folder_path": body.folder_path or "",
        "status": "active",
        "doc_count": 0,
    }
    await create_collection(data)
    return CollectionResponse(
        id=collection_id,
        name=body.name,
        description=body.description,
        folder_path=body.folder_path,
        status="active",
        doc_count=0,
    )


@router.get("/{collection_id}", response_model=CollectionResponse)
async def get_collection_detail(
    collection_id: str,
    current_user: dict = Depends(get_current_user),
):
    collection = await get_collection(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    if collection.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    return CollectionResponse(
        id=collection.get("id", ""),
        name=collection.get("name", ""),
        description=collection.get("description"),
        folder_path=collection.get("folder_path"),
        status=collection.get("status", "active"),
        doc_count=collection.get("doc_count", 0),
        created_at=collection.get("created_at"),
        updated_at=collection.get("updated_at"),
    )


@router.delete("/{collection_id}", status_code=204)
async def delete_collection_route(
    collection_id: str,
    current_user: dict = Depends(get_current_user),
):
    collection = await get_collection(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    if collection.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    await delete_collection(collection_id)
