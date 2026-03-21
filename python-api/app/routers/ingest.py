"""Ingest router."""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse
from app.auth.middleware import get_current_user
from app.core.path_sanitizer import validate_folder_path
from app.db.lancedb_client import (
    create_ingest_job, get_ingest_job, update_ingest_job,
    list_ingest_jobs, get_collection,
)
from app.pipeline.job_manager import get_job_manager
from app.models.schemas import (
    IngestFolderRequest, IngestJobResponse, IngestJobListResponse,
)
import json
import uuid
import asyncio

router = APIRouter()


@router.post("/folder", status_code=202)
async def start_ingest_job(
    body: IngestFolderRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    collection = await get_collection(body.collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    if collection.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Sanitize and validate the folder path before starting any work
    safe_path = validate_folder_path(body.folder_path)
    body = body.model_copy(update={"folder_path": str(safe_path)})

    job_id = str(uuid.uuid4())
    job_data = {
        "id": job_id,
        "collection_id": body.collection_id,
        "status": "pending",
        "progress": 0.0,
        "total_docs": 0,
        "processed_docs": 0,
        "error_msg": "",
        "options": body.options.model_dump_json(),
    }
    await create_ingest_job(job_data)

    jm = get_job_manager()
    background_tasks.add_task(jm.start_job, job_id, body.collection_id, body.folder_path, body.options)

    return {
        "job_id": job_id,
        "status": "pending",
        "collection_id": body.collection_id,
        "created_at": job_data["created_at"],
        "stream_url": f"/api/v1/ingest/jobs/{job_id}/stream",
    }


@router.get("/jobs", response_model=IngestJobListResponse)
async def list_jobs(
    collection_id: str | None = None,
    current_user: dict = Depends(get_current_user),
):
    jobs = await list_ingest_jobs(collection_id)
    return IngestJobListResponse(
        jobs=[
            IngestJobResponse(
                id=j.get("id", ""),
                collection_id=j.get("collection_id", ""),
                status=j.get("status", "pending"),
                progress=j.get("progress", 0.0),
                total_docs=j.get("total_docs", 0),
                processed_docs=j.get("processed_docs", 0),
                started_at=j.get("started_at"),
                completed_at=j.get("completed_at"),
                created_at=j.get("created_at"),
            )
            for j in jobs
        ],
        total=len(jobs),
    )


@router.get("/jobs/{job_id}", response_model=IngestJobResponse)
async def get_job(
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    job = await get_ingest_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return IngestJobResponse(
        id=job.get("id", ""),
        collection_id=job.get("collection_id", ""),
        status=job.get("status", "pending"),
        progress=job.get("progress", 0.0),
        total_docs=job.get("total_docs", 0),
        processed_docs=job.get("processed_docs", 0),
        current_file=job.get("current_file"),
        error_msg=job.get("error_msg"),
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
        created_at=job.get("created_at"),
    )


@router.delete("/jobs/{job_id}", status_code=202)
async def cancel_job(
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    jm = get_job_manager()
    await jm.cancel_job(job_id)
    await update_ingest_job(job_id, {"status": "cancelled"})
    return {"status": "cancelled"}


@router.get("/jobs/{job_id}/stream")
async def stream_job_progress(job_id: str, current_user: dict = Depends(get_current_user)):
    async def event_generator():
        jm = get_job_manager()
        queue = asyncio.Queue()

        async def on_event(event: dict):
            await queue.put(event)

        jm.subscribe(job_id, on_event)

        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=60.0)
                yield {"event": "message", "data": json.dumps(event)}
                if event.get("type") in ("completed", "failed", "cancelled"):
                    break
        except asyncio.TimeoutError:
            yield {"event": "ping", "data": "{}"}
        finally:
            jm.unsubscribe(job_id, on_event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
