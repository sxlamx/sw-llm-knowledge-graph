"""Ingestion pipeline — full document processing."""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import get_settings
from app.db.lancedb_client import (
    update_collection, update_ingest_job, upsert_to_table,
)
from app.llm.embedder import embed_texts
from app.llm.extractor import (
    generate_doc_summary, generate_contextual_prefix, extract_from_chunk,
)
from app.pipeline.job_manager import get_job_manager
from app.models.schemas import IngestOptions

settings = get_settings()
logger = logging.getLogger(__name__)

_limiter = asyncio.Semaphore(10)


async def run_ingest_pipeline(
    job_id: str,
    collection_id: str,
    folder_path: str,
    options: IngestOptions,
) -> None:
    """Full ingestion pipeline: scan → extract → chunk → embed → graph."""
    from app.core.rust_bridge import (
        get_ingestion_engine, get_index_manager,
        rust_init_collection_async, rust_insert_chunks_async,
    )

    engine = get_ingestion_engine()
    im = get_index_manager()

    if im is None:
        await update_ingest_job(job_id, {"status": "failed", "error_msg": "Rust core not available"})
        return

    try:
        await rust_init_collection_async(collection_id)
    except Exception as e:
        logger.warning(f"Collection init warning: {e}")

    await update_ingest_job(job_id, {"status": "running", "started_at": int(datetime.utcnow().timestamp() * 1_000_000)})

    jm = get_job_manager()
    jm.emit(job_id, {"type": "progress", "job_id": job_id, "processed": 0, "total": 0, "progress": 0.0})

    if engine is None:
        await update_ingest_job(job_id, {"status": "failed", "error_msg": "Ingestion engine not available"})
        return

    loop = asyncio.get_event_loop()
    try:
        entries_json = await loop.run_in_executor(
            None,
            lambda: engine.scan_folder(folder_path, [folder_path], options.max_depth, options.max_files),
        )
        entries = json.loads(entries_json)
    except Exception as e:
        logger.error(f"Scan failed: {e}")
        await update_ingest_job(job_id, {"status": "failed", "error_msg": str(e)})
        return

    total = len(entries)
    await update_ingest_job(job_id, {"total_docs": total, "status": "running"})

    processed = 0
    all_chunks = []

    for entry in entries:
        if await jm.is_cancelled(job_id):
            await update_ingest_job(job_id, {"status": "cancelled"})
            return

        try:
            await update_ingest_job(
                job_id,
                {
                    "current_file": entry.get("path", ""),
                    "processed_docs": processed,
                    "progress": processed / max(total, 1),
                },
            )

            extracted = await loop.run_in_executor(
                None,
                lambda: engine.extract_text(entry["path"], entry.get("file_type", "unknown")),
            )
            doc_data = json.loads(extracted)

            summary = await generate_doc_summary(doc_data.get("raw_text", ""))

            pages_json = json.dumps(doc_data.get("pages", []))
            chunks_json = await loop.run_in_executor(
                None,
                lambda: engine.chunk_text(
                    doc_data.get("raw_text", ""),
                    pages_json,
                    options.chunk_size_tokens,
                    options.chunk_overlap_tokens,
                ),
            )
            chunks = json.loads(chunks_json)

            enriched_chunks = []
            for chunk in chunks:
                contextual = await generate_contextual_prefix(summary, chunk["text"])
                enriched_chunks.append({**chunk, "contextual_text": contextual})

            if enriched_chunks:
                embeddings = await embed_texts([c["contextual_text"] for c in enriched_chunks])
                doc_uuid = str(uuid.uuid4())

                for i, chunk in enumerate(enriched_chunks):
                    chunk_record = {
                        "id": str(uuid.uuid4()),
                        "doc_id": doc_uuid,
                        "collection_id": collection_id,
                        "text": chunk["text"],
                        "contextual_text": chunk.get("contextual_text", chunk["text"]),
                        "position": chunk["position"],
                        "token_count": chunk.get("token_count"),
                        "page": chunk.get("page"),
                        "topics": json.dumps(chunk.get("topics", [])),
                        "created_at": int(datetime.utcnow().timestamp() * 1_000_000),
                    }
                    if i < len(embeddings):
                        chunk_record["embedding"] = embeddings[i]
                    else:
                        chunk_record["embedding"] = [0.0] * settings.embedding_dimension

                    all_chunks.append(chunk_record)

                if len(all_chunks) >= 100:
                    await flush_chunks(all_chunks, collection_id, job_id, im)
                    all_chunks = []

            processed += 1
            await update_ingest_job(
                job_id,
                {
                    "processed_docs": processed,
                    "progress": processed / max(total, 1),
                    "current_file": entry.get("path", ""),
                },
            )

            jm.emit(job_id, {
                "type": "progress",
                "job_id": job_id,
                "processed": processed,
                "total": total,
                "current_file": entry.get("path", ""),
                "progress": processed / max(total, 1),
            })

        except Exception as e:
            logger.error(f"Failed to process {entry.get('path')}: {e}")
            processed += 1
            continue

    if all_chunks:
        await flush_chunks(all_chunks, collection_id, job_id, im)

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

    jm.emit(job_id, {
        "type": "completed",
        "job_id": job_id,
        "processed": processed,
        "total": total,
    })


async def flush_chunks(
    chunks: list[dict],
    collection_id: str,
    job_id: str,
    im,
) -> None:
    table_name = f"{collection_id}_chunks"
    schema = {
        "id": "string",
        "doc_id": "string",
        "collection_id": "string",
        "text": "string",
        "contextual_text": "string",
        "position": "int32",
        "token_count": "int32",
        "page": "int32",
        "topics": "string",
        "created_at": "int64",
    }
    await upsert_to_table(table_name, chunks)

    chunks_json = json.dumps(chunks)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: im.insert_chunks(collection_id, chunks_json),
    )
