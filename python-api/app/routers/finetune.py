"""Fine-tuning router — export user feedback as training data and start OpenAI fine-tuning."""

import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth.middleware import get_current_user
from app.db.lancedb_client import get_collection

router = APIRouter()
logger = logging.getLogger(__name__)


class FineTuneRequest(BaseModel):
    collection_id: str
    base_model: str = Field("gpt-4o-mini-2024-07-18", description="OpenAI base model to fine-tune")
    suffix: str = Field("kg-extraction", max_length=40)
    n_epochs: int = Field(3, ge=1, le=10)
    max_examples: int = Field(5_000, ge=10, le=50_000)


class ExportRequest(BaseModel):
    collection_id: str
    max_examples: int = Field(5_000, ge=1, le=50_000)


@router.post("/export")
async def export_dataset(
    body: ExportRequest,
    current_user: dict = Depends(get_current_user),
):
    """Build and return the fine-tuning dataset (JSONL lines as JSON array).

    Use this endpoint to preview or download the training examples before
    committing to a fine-tuning job.
    """
    collection = await get_collection(body.collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    if collection.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    from app.services.finetune_service import build_training_dataset

    examples = await build_training_dataset(
        collection_id=body.collection_id,
        max_examples=body.max_examples,
    )

    return {
        "collection_id": body.collection_id,
        "example_count": len(examples),
        "examples": examples[:50],  # preview only — full dataset is in the upload
        "total": len(examples),
    }


@router.post("/start")
async def start_finetune(
    body: FineTuneRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    """Upload training data to OpenAI and start a fine-tuning job.

    Runs synchronously (OpenAI upload is fast; actual training happens server-side).
    Returns the fine-tuning job ID for polling via ``GET /finetune/jobs/{job_id}``.
    """
    collection = await get_collection(body.collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    if collection.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    from app.services.finetune_service import export_and_finetune

    try:
        result = await export_and_finetune(
            collection_id=body.collection_id,
            base_model=body.base_model,
            suffix=body.suffix,
            n_epochs=body.n_epochs,
            max_examples=body.max_examples,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"Fine-tuning start failed: {exc}")
        raise HTTPException(status_code=502, detail=f"OpenAI API error: {exc}")

    return result


@router.get("/jobs/{job_id}")
async def get_finetune_status(
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Poll the status of an OpenAI fine-tuning job."""
    from app.services.finetune_service import get_finetune_job_status

    try:
        return await get_finetune_job_status(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"Fine-tuning status check failed: {exc}")
        raise HTTPException(status_code=502, detail=f"OpenAI API error: {exc}")
