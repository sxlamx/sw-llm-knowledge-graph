"""Google Drive ingestion router."""

import uuid
import logging
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Request, Response
from pydantic import BaseModel
from typing import Optional

from app.auth.middleware import get_current_user
from app.config import get_settings
from app.db.lancedb_client import get_collection, create_ingest_job, get_drive_channel
from app.pipeline.job_manager import get_job_manager
from app.models.schemas import IngestOptions

router = APIRouter()
logger = logging.getLogger(__name__)
settings = get_settings()

# Google Drive notification resource states that indicate content changed.
_CHANGE_STATES = frozenset({"change", "update", "add", "remove", "trash", "untrash", "changeParents"})


class DriveIngestRequest(BaseModel):
    collection_id: str
    folder_id: str          # Google Drive folder ID
    access_token: str       # OAuth2 access token with Drive scope
    options: IngestOptions = IngestOptions()


@router.post("/ingest", status_code=202)
async def start_drive_ingest(
    body: DriveIngestRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    collection = await get_collection(body.collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    if collection.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    job_id = str(uuid.uuid4())
    await create_ingest_job({
        "id": job_id,
        "collection_id": body.collection_id,
        "status": "pending",
        "progress": 0.0,
        "total_docs": 0,
        "processed_docs": 0,
        "error_msg": "",
        "options": body.options.model_dump_json(),
    })

    from app.services.drive_service import run_drive_ingest_pipeline
    background_tasks.add_task(
        run_drive_ingest_pipeline,
        job_id,
        body.collection_id,
        body.folder_id,
        body.access_token,
        body.options,
    )

    return {
        "job_id": job_id,
        "status": "pending",
        "collection_id": body.collection_id,
        "stream_url": f"/api/v1/ingest/jobs/{job_id}/stream",
    }


# ---------------------------------------------------------------------------
# Drive Push Notification channel registration
# ---------------------------------------------------------------------------


class DriveWatchRequest(BaseModel):
    collection_id: str
    folder_id: str
    access_token: str


@router.post("/watch", status_code=201)
async def register_drive_watch(
    body: DriveWatchRequest,
    current_user: dict = Depends(get_current_user),
):
    """Register a Google Drive push-notification channel for a folder.

    Requires ``DRIVE_WEBHOOK_URL`` to be set to a public HTTPS URL that
    Google can reach for ``POST /api/v1/drive/webhook``.
    """
    collection = await get_collection(body.collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    if collection.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    webhook_url = settings.drive_webhook_url
    if not webhook_url:
        raise HTTPException(
            status_code=503,
            detail="Drive webhook URL not configured (set DRIVE_WEBHOOK_URL env var)",
        )

    from app.services.drive_service import register_watch_channel
    try:
        channel = await register_watch_channel(
            access_token=body.access_token,
            folder_id=body.folder_id,
            collection_id=body.collection_id,
            webhook_url=webhook_url,
        )
    except Exception as exc:
        logger.error(f"Failed to register Drive watch channel: {exc}")
        raise HTTPException(status_code=502, detail=f"Drive API error: {exc}")

    return {
        "channel_id": channel["channel_id"],
        "resource_id": channel["resource_id"],
        "collection_id": body.collection_id,
        "expiry_ms": channel["expiry_ms"],
    }


@router.delete("/watch/{channel_id}", status_code=204)
async def deregister_drive_watch(
    channel_id: str,
    access_token: str,
    current_user: dict = Depends(get_current_user),
):
    """Stop a Drive push-notification channel."""
    channel = await get_drive_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    collection = await get_collection(channel["collection_id"])
    if not collection or collection.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    from app.services.drive_service import deregister_watch_channel
    await deregister_watch_channel(channel_id, channel["resource_id"], access_token)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Drive Push Notification webhook (called by Google)
# ---------------------------------------------------------------------------


@router.post("/webhook")
async def drive_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receive Drive change notifications and trigger incremental re-sync.

    Google sends notification data as HTTP headers, not in the request body.
    Relevant headers:
      - ``X-Goog-Channel-ID``     : channel UUID we registered
      - ``X-Goog-Resource-ID``    : opaque resource identifier
      - ``X-Goog-Resource-State`` : sync | change | update | add | remove | …
    """
    channel_id = request.headers.get("X-Goog-Channel-ID", "")
    resource_state = request.headers.get("X-Goog-Resource-State", "")

    logger.info("Drive webhook: channel=%s state=%s", channel_id, resource_state)

    # "sync" is sent when the channel is first created — no action needed.
    if resource_state == "sync" or resource_state not in _CHANGE_STATES:
        return Response(status_code=200)

    if not channel_id:
        return Response(status_code=200)

    channel = await get_drive_channel(channel_id)
    if not channel:
        logger.warning("Drive webhook: unknown channel_id=%s", channel_id)
        return Response(status_code=200)

    # Trigger incremental re-ingest in the background.
    job_id = str(uuid.uuid4())
    await create_ingest_job({
        "id": job_id,
        "collection_id": channel["collection_id"],
        "status": "pending",
        "progress": 0.0,
        "total_docs": 0,
        "processed_docs": 0,
        "error_msg": "",
        "options": IngestOptions().model_dump_json(),
    })

    from app.services.drive_service import run_drive_ingest_pipeline
    background_tasks.add_task(
        run_drive_ingest_pipeline,
        job_id,
        channel["collection_id"],
        channel["folder_id"],
        channel["access_token"],
        IngestOptions(),
    )

    logger.info(
        "Drive webhook: triggered re-ingest job=%s collection=%s",
        job_id,
        channel["collection_id"],
    )
    return Response(status_code=200)
