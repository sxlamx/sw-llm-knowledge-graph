"""Ingest router."""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse
from app.auth.middleware import get_current_user
from app.core.path_sanitizer import validate_folder_path, validate_file_path
from app.db.lancedb_client import (
    create_ingest_job, get_ingest_job, update_ingest_job,
    list_ingest_jobs, get_collection,
)
from app.pipeline.job_manager import get_job_manager
from app.models.schemas import (
    IngestFolderRequest, IngestJobResponse, IngestJobListResponse,
    FeedDocumentsRequest,
)
from app.services.template_gallery import TemplateGallery
import json
import logging
import uuid
import asyncio

logger = logging.getLogger(__name__)

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

    # Resolve template name to TemplateConfig if provided
    if body.options.template:
        gallery = TemplateGallery.get_instance()
        template_config = gallery.get(body.options.template)
        if not template_config:
            raise HTTPException(
                status_code=400,
                detail=f"Template '{body.options.template}' not found",
            )

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


@router.post("/collections/{collection_id}/feed", status_code=202)
async def feed_documents(
    collection_id: str,
    body: FeedDocumentsRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    """Add new documents to an existing collection's graph (incremental merge)."""
    collection = await get_collection(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    if collection.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    for fp in body.file_paths:
        try:
            validate_file_path(fp)
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid file_path: {fp}")

    if body.template:
        gallery = TemplateGallery.get_instance()
        template_config = gallery.get(body.template)
        if not template_config:
            raise HTTPException(
                status_code=400,
                detail=f"Template '{body.template}' not found",
            )

    job_id = str(uuid.uuid4())
    job_data = {
        "id": job_id,
        "collection_id": collection_id,
        "status": "pending",
        "progress": 0.0,
        "total_docs": len(body.file_paths),
        "processed_docs": 0,
        "error_msg": "",
        "options": json.dumps({"feed_mode": True, "template": body.template}),
    }
    await create_ingest_job(job_data)

    from app.pipeline.ingest_worker import run_feed_pipeline
    background_tasks.add_task(
        run_feed_pipeline,
        job_id, collection_id, body.file_paths, body.template,
    )

    return {
        "job_id": job_id,
        "status": "pending",
        "collection_id": collection_id,
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

        await jm.subscribe(job_id, on_event)

        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=60.0)
                yield {"event": "message", "data": json.dumps(event)}
                if event.get("type") in ("completed", "failed", "cancelled"):
                    break
        except asyncio.TimeoutError:
            yield {"event": "ping", "data": "{}"}
        finally:
            await jm.unsubscribe(job_id, on_event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/collections/{collection_id}/ner", status_code=202)
async def trigger_ner_pass(
    collection_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Trigger a background spaCy+regex NER pass on all untagged chunks.

    Returns a job_id to poll with GET /ingest/collections/{collection_id}/ner/{job_id}.
    No LLM calls — uses spaCy and regex citation detection only.
    """
    collection = await get_collection(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    if collection.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    job_id = str(uuid.uuid4())

    await create_ingest_job({
        "id": job_id,
        "collection_id": collection_id,
        "status": "ner_running",
        "progress": 0.0,
        "total_docs": 0,
        "processed_docs": 0,
        "error_msg": "",
        "options": json.dumps({"ner_pass": True}),
    })

    async def _run():
        from app.pipeline.ingest_worker import _run_ner_pass
        try:
            await update_ingest_job(job_id, {"status": "ner_running"})
            await _run_ner_pass(collection_id, job_id)
            await update_ingest_job(job_id, {"status": "ner_completed"})
        except Exception as e:
            logger.error(f"NER pass {job_id} failed: {e}")
            await update_ingest_job(job_id, {"status": "ner_failed", "error_msg": str(e)})

    asyncio.create_task(_run())
    return {"job_id": job_id, "collection_id": collection_id, "status": "started"}


_NER_STATUSES = {"ner_running", "ner_completed", "ner_failed"}


@router.get("/collections/{collection_id}/ner/{job_id}")
async def get_ner_job_status(
    collection_id: str,
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Poll the status of a NER pass job."""
    job = await get_ingest_job(job_id)
    if not job or job.get("status") not in _NER_STATUSES:
        raise HTTPException(status_code=404, detail="NER job not found")
    return {"job_id": job_id, "collection_id": collection_id, "status": job["status"]}
