#!/usr/bin/env python3
"""
Directly ingest specific files into a collection, bypassing HTTP auth.

Pipeline (per document):
  1. SHA-256 hash check  — skip unchanged docs; reindex changed docs (same doc ID).
  2. Text extraction     — PyMuPDF for PDFs; Rust engine for other formats.
  3. Doc summary         — LLM-generated, used for contextual chunk prefixes.
  4. Chunking            — Rust sentence-aware chunker (default 512 tokens).
  5. Contextual prefix   — LLM prepends document context to each chunk (parallel).
  6. Embedding           — OpenAI/Ollama embeddings, stored in LanceDB vector index.
  7. Entity extraction   — LLM extracts nodes + edges; also returns LLM NER spans.
  8. NER tagging         — Hybrid spaCy + LLM spans → ner_tags JSON per chunk.
                           Tracks ner_version so tags can be backfilled when NER
                           logic is expanded without re-chunking or re-embedding.

Usage:
    python scripts/ingest_files.py <file1> [<file2> ...] [options]

Options:
    --collection-id ID        Add to existing collection
    --collection-name NAME    Name for new collection (default: "Singapore Acts")
    --user-email EMAIL        Owner email (default: kamparboy@gmail.com)
    --concurrency N           Parallel LLM calls for contextual enrichment (default: 20)
    --no-contextual           Skip contextual prefix generation (faster, slightly lower quality)
    --no-entities             Skip entity/graph extraction (spaCy NER still runs)
    --chunk-size N            Chunk size in tokens (default: 512)
    --reindex-ner             Backfill NER tags on existing chunks below current NER_VERSION.
                              Does NOT re-chunk, re-embed, or re-extract entities.

Examples:
    python scripts/ingest_files.py doc1.pdf doc2.pdf
    python scripts/ingest_files.py doc1.pdf --no-contextual --concurrency 30
    python scripts/ingest_files.py --reindex-ner --collection-id <id>
"""

import asyncio
import argparse
import hashlib
import json
import sys
import os
import uuid
import logging
from pathlib import Path
from datetime import datetime


def compute_file_hash(file_path: str) -> str:
    """Return SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../python-api"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class _SkipFile(Exception):
    """Sentinel raised to skip a file that is already up-to-date (hash unchanged)."""


async def get_or_create_collection(collection_name: str, collection_id: str | None, user_id: str) -> str:
    from app.db.lancedb_client import get_lancedb, create_collection, _SYSTEM_SCHEMAS, get_collection

    db = await get_lancedb()
    for table_name, schema in _SYSTEM_SCHEMAS.items():
        try:
            db.create_table(table_name, schema=schema, exist_ok=True)
        except Exception:
            pass

    if collection_id:
        col = await get_collection(collection_id)
        if col:
            logger.info(f"Using existing collection: {col['name']} ({collection_id})")
            return collection_id
        logger.warning(f"Collection {collection_id} not found, creating new one.")

    cid = collection_id or str(uuid.uuid4())
    await create_collection({
        "id": cid,
        "user_id": user_id,
        "name": collection_name,
        "description": "Ingested via script",
        "folder_path": "",
        "status": "active",
        "doc_count": 0,
    })
    logger.info(f"Created collection: {collection_name} ({cid})")
    return cid


async def _enrich_chunks_parallel(
    chunks: list[dict],
    summary: str,
    concurrency: int,
    skip_contextual: bool,
) -> list[dict]:
    """Enrich chunks with contextual prefix, parallelized with semaphore."""
    from app.llm.extractor import generate_contextual_prefix

    if skip_contextual:
        return [{**c, "contextual_text": c["text"]} for c in chunks]

    sem = asyncio.Semaphore(concurrency)

    async def _one(chunk: dict) -> dict:
        async with sem:
            contextual = await generate_contextual_prefix(summary, chunk["text"])
            return {**chunk, "contextual_text": contextual}

    tasks = [asyncio.create_task(_one(c)) for c in chunks]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    enriched = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            enriched.append({**chunks[i], "contextual_text": chunks[i]["text"]})
        else:
            enriched.append(r)
    return enriched


async def run(
    files: list[str],
    collection_name: str,
    collection_id: str | None,
    user_email: str,
    concurrency: int,
    skip_contextual: bool,
    skip_entities: bool,
    chunk_size: int,
):
    from app.db.lancedb_client import get_user_by_email, create_ingest_job

    valid_files = []
    for f in files:
        p = Path(f)
        if not p.exists():
            logger.error(f"File not found: {f}")
            sys.exit(1)
        valid_files.append(str(p.resolve()))

    user = await get_user_by_email(user_email)
    if not user:
        logger.error(f"User {user_email!r} not found — run: python scripts/seed_user.py {user_email}")
        sys.exit(1)
    user_id = user["id"]
    logger.info(f"Running as user: {user_email} ({user_id})")

    cid = await get_or_create_collection(collection_name, collection_id, user_id)

    job_id = str(uuid.uuid4())
    now = int(datetime.utcnow().timestamp() * 1_000_000)
    await create_ingest_job({
        "id": job_id,
        "collection_id": cid,
        "status": "pending",
        "progress": 0.0,
        "total_docs": len(valid_files),
        "processed_docs": 0,
        "error_msg": "",
        "started_at": now,
        "completed_at": 0,
        "options": "{}",
        "last_completed_file": "",
    })

    logger.info(
        f"Starting ingestion: {len(valid_files)} file(s), concurrency={concurrency}, "
        f"contextual={'off' if skip_contextual else 'on'}, entities={'off' if skip_entities else 'on'}"
    )

    await _run_pipeline(job_id, cid, valid_files, concurrency, skip_contextual, skip_entities, chunk_size)


async def _run_pipeline(
    job_id: str,
    collection_id: str,
    file_paths: list[str],
    concurrency: int,
    skip_contextual: bool,
    skip_entities: bool,
    chunk_size: int,
):
    from app.db.lancedb_client import (
        update_ingest_job, upsert_document, upsert_topic, upsert_to_table, update_collection,
    )
    from app.core.rust_bridge import get_ingestion_engine, get_index_manager, rust_init_collection_async
    from app.llm.embedder import embed_texts
    from app.llm.extractor import generate_doc_summary
    from app.models.schemas import IngestOptions
    from app.config import get_settings

    engine = get_ingestion_engine()
    im = get_index_manager()

    if engine is None:
        logger.error("Rust ingestion engine not available — rebuild rust-core")
        await update_ingest_job(job_id, {"status": "failed", "error_msg": "Rust engine not available"})
        sys.exit(1)

    try:
        await rust_init_collection_async(collection_id)
    except Exception as e:
        logger.warning(f"Collection init: {e}")

    await update_ingest_job(job_id, {
        "status": "running",
        "started_at": int(datetime.utcnow().timestamp() * 1_000_000),
    })

    settings = get_settings()
    options = IngestOptions(chunk_size_tokens=chunk_size)
    loop = asyncio.get_event_loop()
    all_topics: set[str] = set()
    processed = 0
    total = len(file_paths)
    total_nodes = 0
    total_edges = 0

    for file_path in file_paths:
        p = Path(file_path)
        ext = p.suffix.lower().lstrip(".")
        logger.info(f"[{processed+1}/{total}] {p.name}")

        try:
            await update_ingest_job(job_id, {
                "current_file": file_path,
                "processed_docs": processed,
                "progress": processed / max(total, 1),
            })

            # --- Hash-based dedup / reindex check ---
            from app.db.lancedb_client import get_document_by_file_path, delete_document
            file_hash = compute_file_hash(file_path)
            existing_doc = await get_document_by_file_path(collection_id, file_path)

            if existing_doc:
                existing_hash = existing_doc.get("file_hash", "")
                if existing_hash == file_hash:
                    logger.info(f"  SKIP — unchanged (hash={file_hash[:12]}…)")
                    raise _SkipFile()
                else:
                    logger.info(f"  REINDEX — hash changed ({existing_hash[:12]}… → {file_hash[:12]}…)")
                    await delete_document(existing_doc["id"], collection_id)
                    doc_uuid = existing_doc["id"]  # reuse same doc ID to preserve node provenance
            else:
                doc_uuid = str(uuid.uuid4())

            # Extract text — pymupdf for PDFs (handles CID/Identity-H encoding), Rust for others
            from app.core.pdf_extractor import extract_text_smart
            doc_data = await extract_text_smart(file_path, ext, engine)
            raw_text = doc_data.get("raw_text", "")
            logger.info(f"  {len(raw_text):,} chars extracted")

            # Summary
            summary = await generate_doc_summary(raw_text)
            logger.info(f"  Summary: {summary[:100].strip()}...")

            # Chunk
            pages_json = json.dumps(doc_data.get("pages", []))
            chunks_json = await loop.run_in_executor(
                None,
                lambda: engine.chunk_text(raw_text, pages_json, options.chunk_size_tokens, options.chunk_overlap_tokens),
            )
            chunks = json.loads(chunks_json)
            logger.info(f"  {len(chunks)} chunks (size={chunk_size} tokens)")

            # Persist document (includes file_hash for future runs)
            doc_record = {
                "id": doc_uuid,
                "collection_id": collection_id,
                "title": doc_data.get("title") or p.name,
                "file_path": file_path,
                "file_type": ext,
                "file_hash": file_hash,
                "doc_summary": summary,
                "metadata": json.dumps(doc_data.get("metadata", {})),
            }
            await upsert_document(doc_record)

            if not chunks:
                processed += 1
                await update_ingest_job(job_id, {"processed_docs": processed, "progress": processed / max(total, 1)})
                continue

            # Parallel contextual enrichment
            logger.info(f"  Enriching {len(chunks)} chunks (concurrency={concurrency})...")
            enriched = await _enrich_chunks_parallel(chunks, summary, concurrency, skip_contextual)

            # Embed
            texts_to_embed = [c["contextual_text"] for c in enriched]
            logger.info(f"  Embedding {len(texts_to_embed)} chunks...")
            embeddings = await embed_texts(texts_to_embed)

            chunk_records: list[dict] = []
            for i, chunk in enumerate(enriched):
                chunk_id = str(uuid.uuid4())
                topics_list: list[str] = chunk.get("topics") or []
                if isinstance(topics_list, str):
                    try:
                        topics_list = json.loads(topics_list)
                    except Exception:
                        topics_list = []

                chunk_records.append({
                    "id": chunk_id,
                    "doc_id": doc_uuid,
                    "collection_id": collection_id,
                    "text": chunk["text"],
                    "contextual_text": chunk.get("contextual_text", chunk["text"]),
                    "position": chunk["position"],
                    "token_count": chunk.get("token_count", 0),
                    "page": chunk.get("page") or 0,
                    "topics": topics_list,
                    "embedding": embeddings[i] if i < len(embeddings) else [0.0] * settings.embedding_dimension,
                    "created_at": int(datetime.utcnow().timestamp() * 1_000_000),
                    "ner_tags": "[]",
                    "ner_tagged": False,
                    "ner_tagged_at": 0,
                    "ner_version": 0,
                })
                for t in topics_list:
                    all_topics.add(t)

            # Flush chunks to LanceDB (skip Rust index — backend holds the Tantivy lock)
            logger.info(f"  Saving {len(chunk_records)} chunks to DB...")
            await upsert_to_table(f"{collection_id}_chunks", chunk_records)

            # Entity extraction + NER tagging
            from app.llm.ner_tagger import NER_VERSION
            now_us = int(datetime.utcnow().timestamp() * 1_000_000)
            if not skip_entities:
                logger.info(f"  Extracting entities + NER from {len(chunk_records)} chunks...")
                from app.pipeline.ingest_worker import _extract_graph, _flush_graph
                extracted_nodes, extracted_edges, ner_tags_map = await _extract_graph(
                    chunk_records, doc_uuid, collection_id, summary, None
                )
                logger.info(f"  +{len(extracted_nodes)} entities, +{len(extracted_edges)} relations — flushing...")
                await _flush_graph(extracted_nodes, extracted_edges, collection_id, im)
                total_nodes += len(extracted_nodes)
                total_edges += len(extracted_edges)
            else:
                logger.info(f"  Running spaCy NER on {len(chunk_records)} chunks...")
                from app.pipeline.ingest_worker import _run_ner_only
                ner_tags_map = await _run_ner_only(chunk_records)

            # Write NER tags + version back to chunk records, then flush
            for cr in chunk_records:
                cr["ner_tags"] = ner_tags_map.get(cr["id"], "[]")
                cr["ner_tagged"] = True
                cr["ner_tagged_at"] = now_us
                cr["ner_version"] = NER_VERSION
            await upsert_to_table(f"{collection_id}_chunks", chunk_records)

        except _SkipFile:
            processed += 1
            await update_ingest_job(job_id, {"processed_docs": processed, "progress": processed / max(total, 1)})
            continue
        except Exception as e:
            logger.error(f"  Failed: {e}", exc_info=True)
            processed += 1
            await update_ingest_job(job_id, {
                "processed_docs": processed,
                "progress": processed / max(total, 1),
            })
            continue
        processed += 1
        # Checkpoint: record the last successfully completed file so a resumed run
        # can confirm progress even if hash dedup is the primary resume mechanism.
        await update_ingest_job(job_id, {
            "processed_docs": processed,
            "progress": processed / max(total, 1),
            "last_completed_file": file_path,
        })
        logger.info(f"  [checkpoint] {p.name} done ({processed}/{total})")

    # Persist topics
    for topic_name in all_topics:
        await upsert_topic(collection_id, {
            "id": topic_name.lower().replace(" ", "_"),
            "collection_id": collection_id,
            "name": topic_name,
            "node_count": 0,
            "chunk_count": 0,
        })

    await update_collection(collection_id, {"doc_count": total, "status": "active"})
    await update_ingest_job(job_id, {
        "status": "completed",
        "processed_docs": processed,
        "progress": 1.0,
        "completed_at": int(datetime.utcnow().timestamp() * 1_000_000),
    })

    logger.info(f"Done: {processed}/{total} docs | {total_nodes} entities | {total_edges} relations | {len(all_topics)} topics")


async def backfill_ner(collection_id: str, concurrency: int) -> None:
    """Retag all chunks whose ner_version is below the current NER_VERSION.

    Does NOT re-chunk, re-embed, or re-extract entities — only reruns NER tagging
    and updates ner_tags, ner_tagged, ner_tagged_at, and ner_version in place.
    """
    from app.llm.ner_tagger import NER_VERSION
    from app.db.lancedb_client import get_outdated_ner_chunks, update_chunk_ner_tags
    from app.pipeline.ingest_worker import _run_ner_only

    chunks = await get_outdated_ner_chunks(collection_id, NER_VERSION)
    if not chunks:
        logger.info(f"All chunks already at NER_VERSION={NER_VERSION}. Nothing to do.")
        return

    logger.info(f"Backfilling NER v{NER_VERSION} on {len(chunks)} chunks (concurrency={concurrency})...")

    sem = asyncio.Semaphore(concurrency)

    async def _retag_one(chunk: dict) -> None:
        async with sem:
            result = await _run_ner_only([chunk])
            ner_json = result.get(chunk["id"], "[]")
            await update_chunk_ner_tags(collection_id, chunk["id"], ner_json, NER_VERSION)

    tasks = [asyncio.create_task(_retag_one(c)) for c in chunks]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    errors = sum(1 for r in results if isinstance(r, Exception))
    ok = len(results) - errors
    logger.info(f"Backfill done: {ok} tagged, {errors} errors")


def main():
    parser = argparse.ArgumentParser(description="Ingest files into the knowledge graph.")
    parser.add_argument("files", nargs="*", help="File paths to ingest")
    parser.add_argument("--collection-id", default=None)
    parser.add_argument("--collection-name", default="Singapore Acts")
    parser.add_argument("--user-email", default="kamparboy@gmail.com")
    parser.add_argument("--concurrency", type=int, default=20, help="Parallel LLM calls for contextual enrichment")
    parser.add_argument("--no-contextual", action="store_true", help="Skip contextual prefix (faster)")
    parser.add_argument("--no-entities", action="store_true", help="Skip entity extraction")
    parser.add_argument("--chunk-size", type=int, default=512, help="Chunk size in tokens")
    parser.add_argument("--reindex-ner", action="store_true",
                        help="Backfill NER tags on existing chunks below current NER_VERSION")
    args = parser.parse_args()

    if args.reindex_ner:
        if not args.collection_id:
            parser.error("--reindex-ner requires --collection-id")
        asyncio.run(backfill_ner(
            collection_id=args.collection_id,
            concurrency=args.concurrency,
        ))
    else:
        if not args.files:
            parser.error("Provide file paths to ingest, or use --reindex-ner")
        asyncio.run(run(
            files=args.files,
            collection_name=args.collection_name,
            collection_id=args.collection_id,
            user_email=args.user_email,
            concurrency=args.concurrency,
            skip_contextual=args.no_contextual,
            skip_entities=args.no_entities,
            chunk_size=args.chunk_size,
        ))


if __name__ == "__main__":
    main()
