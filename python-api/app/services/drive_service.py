"""Google Drive ingestion service."""

import asyncio
import hashlib
import json
import logging
from typing import Optional

import httpx

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_EXPORT = "https://www.googleapis.com/drive/v3/files/{file_id}/export"
DRIVE_DOWNLOAD = "https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"

EXPORTABLE_MIME = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}

SUPPORTED_MIME = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/html",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


async def list_drive_files(
    access_token: str,
    folder_id: str,
    page_token: Optional[str] = None,
) -> dict:
    """List files in a Drive folder (one page)."""
    params = {
        "q": f"'{folder_id}' in parents and trashed = false",
        "fields": "nextPageToken, files(id, name, mimeType, md5Checksum, modifiedTime, size)",
        "pageSize": 100,
    }
    if page_token:
        params["pageToken"] = page_token

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{DRIVE_API}/files",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def list_all_drive_files(access_token: str, folder_id: str) -> list[dict]:
    """Paginate through all files in a Drive folder."""
    files = []
    page_token = None
    while True:
        page = await list_drive_files(access_token, folder_id, page_token)
        files.extend(page.get("files", []))
        page_token = page.get("nextPageToken")
        if not page_token:
            break
    return files


async def download_drive_file(access_token: str, file_id: str, mime_type: str) -> bytes:
    """Download or export a Drive file as bytes."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        if mime_type in EXPORTABLE_MIME:
            export_mime = EXPORTABLE_MIME[mime_type]
            resp = await client.get(
                DRIVE_EXPORT.format(file_id=file_id),
                params={"mimeType": export_mime},
                headers={"Authorization": f"Bearer {access_token}"},
            )
        else:
            resp = await client.get(
                DRIVE_DOWNLOAD.format(file_id=file_id),
                headers={"Authorization": f"Bearer {access_token}"},
            )
        resp.raise_for_status()
        return resp.content


def drive_hash(file_meta: dict) -> str:
    """Return a stable hash for a Drive file (uses md5Checksum if available)."""
    return file_meta.get("md5Checksum") or hashlib.md5(
        f"{file_meta['id']}:{file_meta.get('modifiedTime', '')}".encode()
    ).hexdigest()


def is_supported_file(file_meta: dict) -> bool:
    mime = file_meta.get("mimeType", "")
    return mime in SUPPORTED_MIME or mime in EXPORTABLE_MIME


async def run_drive_ingest_pipeline(
    job_id: str,
    collection_id: str,
    folder_id: str,
    access_token: str,
    options,
) -> None:
    """Drive-specific ingest: list → download → write to temp → call core pipeline."""
    import tempfile
    import os
    from pathlib import Path
    from datetime import datetime

    from app.db.lancedb_client import update_ingest_job, update_collection, upsert_document
    from app.llm.embedder import embed_texts
    from app.llm.extractor import generate_doc_summary, generate_contextual_prefix, extract_from_chunk
    from app.pipeline.job_manager import get_job_manager
    from app.pipeline.ingest_worker import _extract_graph, _flush_chunks, _flush_graph
    from app.core.rust_bridge import get_index_manager, get_ingestion_engine, rust_init_collection_async

    jm = get_job_manager()
    im = get_index_manager()
    engine = get_ingestion_engine()

    await update_ingest_job(
        job_id,
        {"status": "running", "started_at": int(datetime.utcnow().timestamp() * 1_000_000)},
    )

    try:
        await rust_init_collection_async(collection_id)
    except Exception:
        pass

    try:
        files = await list_all_drive_files(access_token, folder_id)
    except Exception as e:
        await update_ingest_job(job_id, {"status": "failed", "error_msg": f"Drive listing failed: {e}"})
        return

    supported = [f for f in files if is_supported_file(f)]
    total = len(supported)
    await update_ingest_job(job_id, {"total_docs": total})

    all_chunks: list[dict] = []
    all_nodes: list[dict] = []
    all_edges: list[dict] = []
    processed = 0
    loop = asyncio.get_event_loop()

    with tempfile.TemporaryDirectory() as tmpdir:
        for file_meta in supported:
            if await jm.is_cancelled(job_id):
                await update_ingest_job(job_id, {"status": "cancelled"})
                return

            fid = file_meta["id"]
            fname = file_meta.get("name", fid)
            mime = file_meta.get("mimeType", "")
            current_hash = drive_hash(file_meta)

            jm.emit(job_id, {"type": "progress", "job_id": job_id, "processed": processed,
                              "total": total, "current_file": fname, "progress": processed / max(total, 1)})

            # Incremental sync: skip file if it hasn't changed since last ingest
            try:
                from app.db.lancedb_client import get_document_by_drive_file_id
                existing_doc = await get_document_by_drive_file_id(fid, collection_id)
                if existing_doc:
                    existing_meta = json.loads(existing_doc.get("metadata", "{}")) if isinstance(existing_doc.get("metadata"), str) else {}
                    if existing_meta.get("drive_hash") == current_hash:
                        logger.debug(f"Drive file unchanged, skipping: {fname} ({fid})")
                        processed += 1
                        await update_ingest_job(
                            job_id,
                            {"processed_docs": processed, "progress": processed / max(total, 1)},
                        )
                        continue
            except Exception as e:
                logger.debug(f"Hash check skipped for {fname}: {e}")

            try:
                content = await download_drive_file(access_token, fid, mime)
                ext = _mime_to_ext(mime)
                tmp_path = Path(tmpdir) / f"{fid}{ext}"
                tmp_path.write_bytes(content)

                if engine:
                    extracted = await loop.run_in_executor(
                        None,
                        lambda p=str(tmp_path), ft=_mime_to_filetype(mime): engine.extract_text(p, ft),
                    )
                    doc_data = json.loads(extracted)
                else:
                    doc_data = {"raw_text": content.decode("utf-8", errors="replace"), "pages": [], "metadata": {}}

                raw_text = doc_data.get("raw_text", "")
                summary = await generate_doc_summary(raw_text)

                import uuid as _uuid
                doc_uuid = str(_uuid.uuid4())
                await upsert_document({
                    "id": doc_uuid,
                    "collection_id": collection_id,
                    "title": fname,
                    "file_path": f"gdrive://{fid}",
                    "file_type": _mime_to_filetype(mime),
                    "doc_summary": summary,
                    "metadata": json.dumps({
                        "drive_file_id": fid,
                        "drive_hash": current_hash,
                        "modified_time": file_meta.get("modifiedTime"),
                    }),
                })

                if engine:
                    chunks_json = await loop.run_in_executor(
                        None,
                        lambda: engine.chunk_text(
                            raw_text,
                            json.dumps(doc_data.get("pages", [])),
                            options.chunk_size_tokens,
                            options.chunk_overlap_tokens,
                        ),
                    )
                    chunks = json.loads(chunks_json)
                else:
                    # Simple fallback chunker
                    words = raw_text.split()
                    chunks = []
                    for i in range(0, len(words), options.chunk_size_tokens):
                        chunks.append({
                            "text": " ".join(words[i: i + options.chunk_size_tokens]),
                            "position": i // options.chunk_size_tokens,
                            "page": None,
                            "topics": [],
                        })

                enriched = []
                for chunk in chunks:
                    ctx = await generate_contextual_prefix(summary, chunk["text"])
                    enriched.append({**chunk, "contextual_text": ctx})

                embeddings = await embed_texts([c["contextual_text"] for c in enriched])
                import uuid as _uuid2
                from datetime import datetime as dt
                chunk_records = []
                for i, chunk in enumerate(enriched):
                    cid = str(_uuid2.uuid4())
                    topics_list = chunk.get("topics") or []
                    cr = {
                        "id": cid,
                        "doc_id": doc_uuid,
                        "collection_id": collection_id,
                        "text": chunk["text"],
                        "contextual_text": chunk.get("contextual_text", chunk["text"]),
                        "position": chunk["position"],
                        "token_count": chunk.get("token_count", 0),
                        "page": chunk.get("page") or 0,
                        "topics": topics_list,
                        "embedding": embeddings[i] if i < len(embeddings) else [0.0] * settings.embedding_dimension,
                        "created_at": int(dt.utcnow().timestamp() * 1_000_000),
                    }
                    chunk_records.append(cr)
                    all_chunks.append(cr)

                if options.extract_entities:
                    validator = None
                    try:
                        from app.core.rust_bridge import get_ontology_validator
                        validator = get_ontology_validator()
                    except Exception:
                        pass
                    ns, es = await _extract_graph(chunk_records, doc_uuid, collection_id, summary, validator)
                    all_nodes.extend(ns)
                    all_edges.extend(es)

                if len(all_chunks) >= 100:
                    await _flush_chunks(all_chunks, collection_id, im)
                    all_chunks = []

            except Exception as e:
                logger.error(f"Drive file {fname}: {e}")
            finally:
                processed += 1
                await update_ingest_job(
                    job_id,
                    {"processed_docs": processed, "progress": processed / max(total, 1)},
                )

    if all_chunks:
        await _flush_chunks(all_chunks, collection_id, im)
    if all_nodes:
        await _flush_graph(all_nodes, all_edges, collection_id, im)

    await update_collection(collection_id, {"doc_count": total, "status": "active"})
    await update_ingest_job(
        job_id,
        {
            "status": "completed",
            "processed_docs": processed,
            "progress": 1.0,
            "completed_at": int(datetime.utcnow().timestamp() * 1_000_000),
        },
    )
    jm.emit(job_id, {"type": "completed", "job_id": job_id, "processed": processed, "total": total})


async def register_watch_channel(
    access_token: str,
    folder_id: str,
    collection_id: str,
    webhook_url: str,
) -> dict:
    """Register a Google Drive push-notification channel for a folder.

    Returns the channel dict as stored (includes ``channel_id`` and ``expiry_ms``).
    """
    import uuid as _uuid

    channel_id = str(_uuid.uuid4())

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{DRIVE_API}/files/{folder_id}/watch",
            json={
                "kind": "api#channel",
                "id": channel_id,
                "type": "web_hook",
                "address": webhook_url,
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        data = resp.json()

    resource_id = data.get("resourceId", "")
    expiry_ms = int(data.get("expiration", 0))

    channel = {
        "channel_id": channel_id,
        "resource_id": resource_id,
        "collection_id": collection_id,
        "folder_id": folder_id,
        "access_token": access_token,
        "expiry_ms": expiry_ms,
    }

    from app.db.lancedb_client import upsert_drive_channel
    await upsert_drive_channel(channel)
    return channel


async def deregister_watch_channel(channel_id: str, resource_id: str, access_token: str) -> None:
    """Stop a Drive push-notification channel."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                f"{DRIVE_API}/channels/stop",
                json={"id": channel_id, "resourceId": resource_id},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
        except Exception:
            pass

    from app.db.lancedb_client import delete_drive_channel
    await delete_drive_channel(channel_id)


def _mime_to_ext(mime: str) -> str:
    return {
        "application/pdf": ".pdf",
        "text/plain": ".txt",
        "text/markdown": ".md",
        "text/html": ".html",
        "text/csv": ".csv",
        "application/vnd.google-apps.document": ".txt",
        "application/vnd.google-apps.spreadsheet": ".csv",
        "application/vnd.google-apps.presentation": ".txt",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    }.get(mime, ".bin")


def _mime_to_filetype(mime: str) -> str:
    return {
        "application/pdf": "pdf",
        "text/plain": "text",
        "text/markdown": "markdown",
        "text/html": "html",
        "text/csv": "text",
        "application/vnd.google-apps.document": "text",
        "application/vnd.google-apps.spreadsheet": "text",
        "application/vnd.google-apps.presentation": "text",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    }.get(mime, "unknown")
