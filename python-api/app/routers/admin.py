"""Admin router — user management and collection maintenance (admin-only)."""

import asyncio
import json
import logging
import uuid
from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from app.auth.middleware import require_admin
from app.db.lancedb_client import (
    list_users, update_user, get_user_by_id, get_user_by_email,
    get_chunks_for_collection, update_chunk_ner_tags,
)
from app.llm.ner_tagger import tag_chunk, tags_to_json, ALL_NER_LABELS

logger = logging.getLogger(__name__)

router = APIRouter()


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    avatar_url: Optional[str] = None
    role: str
    status: str
    created_at: Optional[int] = None
    last_login: Optional[int] = None


class UpdateUserRequest(BaseModel):
    role: Optional[str] = None    # "admin" | "user"
    status: Optional[str] = None  # "active" | "pending" | "blocked"


def _fmt(u: dict) -> UserOut:
    return UserOut(
        id=u.get("id", ""),
        email=u.get("email", ""),
        name=u.get("name", ""),
        avatar_url=u.get("avatar_url"),
        role=u.get("role", "user"),
        status=u.get("status", "pending"),
        created_at=u.get("created_at"),
        last_login=u.get("last_login"),
    )


@router.get("/users", response_model=list[UserOut])
async def admin_list_users(_admin=Depends(require_admin)):
    """Return all registered users with their role and status."""
    users = await list_users()
    return [_fmt(u) for u in users]


@router.patch("/users/{user_id}", response_model=UserOut)
async def admin_update_user(
    user_id: str,
    body: UpdateUserRequest,
    _admin=Depends(require_admin),
):
    """Update a user's role or status. Use status='active' to grant access."""
    if body.role and body.role not in ("admin", "user"):
        raise HTTPException(status_code=422, detail="role must be 'admin' or 'user'")
    if body.status and body.status not in ("active", "pending", "blocked"):
        raise HTTPException(status_code=422, detail="status must be 'active', 'pending', or 'blocked'")

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    updated = await update_user(user_id, updates)
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    return _fmt(updated)


@router.get("/users/by-email/{email:path}", response_model=UserOut)
async def admin_get_user_by_email(email: str, _admin=Depends(require_admin)):
    """Look up a user by email address."""
    user = await get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _fmt(user)


# ---------------------------------------------------------------------------
# NER backprocessing
# ---------------------------------------------------------------------------

# In-memory job state for NER re-tag jobs (keyed by job_id)
_ner_jobs: dict[str, dict] = {}


async def _run_ner_retag_job(
    job_id: str,
    collection_id: str,
    use_llm: bool,
    use_regex: bool = True,
) -> None:
    """Background task: tag all existing chunks in a collection with NER."""
    from app.llm.extractor import extract_from_chunk

    _ner_jobs[job_id] = {"status": "running", "processed": 0, "total": 0, "errors": 0}

    try:
        chunks = await get_chunks_for_collection(collection_id)
        total = len(chunks)
        _ner_jobs[job_id]["total"] = total

        if total == 0:
            _ner_jobs[job_id]["status"] = "completed"
            return

        semaphore = asyncio.Semaphore(5)  # lower concurrency for background job

        async def _process_chunk(chunk: dict) -> None:
            async with semaphore:
                chunk_id = chunk.get("id", "")
                chunk_text = chunk.get("text", "")
                if not chunk_text:
                    return
                try:
                    llm_ner_spans: list[dict] = []
                    if use_llm:
                        try:
                            result = await extract_from_chunk(chunk_text)
                            llm_ner_spans = result.get("ner_spans", [])
                        except Exception as llm_err:
                            logger.warning(f"LLM extraction failed for chunk {chunk_id}: {llm_err}")

                    tags = await tag_chunk(chunk_text, llm_ner_spans, use_regex_citations=use_regex)
                    await update_chunk_ner_tags(collection_id, chunk_id, tags_to_json(tags))
                except Exception as e:
                    logger.error(f"NER retag failed for chunk {chunk_id}: {e}")
                    _ner_jobs[job_id]["errors"] = _ner_jobs[job_id].get("errors", 0) + 1
                finally:
                    _ner_jobs[job_id]["processed"] += 1

        await asyncio.gather(*[_process_chunk(c) for c in chunks])
        _ner_jobs[job_id]["status"] = "completed"

    except Exception as e:
        logger.error(f"NER retag job {job_id} failed: {e}")
        _ner_jobs[job_id]["status"] = "failed"
        _ner_jobs[job_id]["error"] = str(e)


@router.post("/collections/{collection_id}/ner-tag")
async def start_ner_retag(
    collection_id: str,
    use_llm: bool = Query(default=True, description="Also call LLM for legal NER labels (slower but complete). Set false for spaCy-only pass."),
    use_regex: bool = Query(default=True, description="Run regex citation detector for CASE_CITATION labels (fast, no LLM cost)."),
    _admin=Depends(require_admin),
):
    """Start a background NER re-tagging job for all chunks in a collection.

    - use_llm=true (default): spaCy + LLM legal NER (full tags, slower, uses LLM credits)
    - use_llm=false: spaCy-only (PERSON, ORG, LOC, DATE, LAW — fast, no LLM cost)
    - use_regex=true (default): also run regex citation detector (zero cost, high precision)

    Returns a job_id to poll with GET /admin/collections/{collection_id}/ner-tag/{job_id}.
    """
    job_id = str(uuid.uuid4())
    asyncio.create_task(_run_ner_retag_job(job_id, collection_id, use_llm, use_regex))
    return {"job_id": job_id, "collection_id": collection_id, "status": "started", "use_llm": use_llm, "use_regex": use_regex}


@router.get("/collections/{collection_id}/ner-tag/{job_id}")
async def get_ner_retag_status(
    collection_id: str,
    job_id: str,
    _admin=Depends(require_admin),
):
    """Poll the status of a NER re-tagging job."""
    job = _ner_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="NER job not found")
    total = job.get("total", 0)
    processed = job.get("processed", 0)
    return {
        "job_id": job_id,
        "collection_id": collection_id,
        "status": job["status"],
        "processed": processed,
        "total": total,
        "progress": processed / max(total, 1),
        "errors": job.get("errors", 0),
        "error": job.get("error"),
    }


@router.get("/collections/{collection_id}/ner-stats")
async def get_ner_stats(
    collection_id: str,
    _admin=Depends(require_admin),
):
    """Return NER label distribution across all chunks in a collection.

    Example response:
      {"PERSON": 312, "LEGISLATION_TITLE": 89, "ORGANIZATION": 201, ...}
    """
    chunks = await get_chunks_for_collection(collection_id)
    label_counter: Counter = Counter()
    tagged_count = 0
    untagged_count = 0

    for chunk in chunks:
        raw = chunk.get("ner_tags") or "[]"
        try:
            tags = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            tags = []

        if tags:
            tagged_count += 1
            for tag in tags:
                label_counter[tag.get("label", "UNKNOWN")] += 1
        else:
            untagged_count += 1

    return {
        "collection_id": collection_id,
        "total_chunks": len(chunks),
        "tagged_chunks": tagged_count,
        "untagged_chunks": untagged_count,
        "label_counts": dict(label_counter),
        "known_labels": ALL_NER_LABELS,
    }


@router.get("/collections/{collection_id}/ner-labels")
async def get_collection_ner_labels(
    collection_id: str,
    _admin=Depends(require_admin),
):
    """Return the set of NER labels actually present in this collection's chunks (for UI filter population)."""
    chunks = await get_chunks_for_collection(collection_id)
    present: set[str] = set()
    for chunk in chunks:
        raw = chunk.get("ner_tags") or "[]"
        try:
            tags = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            tags = []
        for tag in tags:
            lbl = tag.get("label")
            if lbl:
                present.add(lbl)
    return {"collection_id": collection_id, "labels": sorted(present)}
