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
    get_lancedb, get_ingest_job, update_collection, update_ingest_job,
    upsert_to_table, upsert_graph_node, upsert_graph_edge,
    get_outdated_ner_chunks, update_chunk_ner_tags, bulk_update_chunk_ner_tags,
)
from app.llm.embedder import embed_texts
from app.llm.extractor import (
    generate_doc_summary, generate_contextual_prefix, extract_from_chunk,
)
from app.llm.ner_tagger import tag_chunk, tags_to_json, NER_VERSION, check_ner_ready
from app.core.pdf_extractor import extract_text_smart
from app.pipeline.job_manager import get_job_manager
from app.models.schemas import IngestOptions

settings = get_settings()
logger = logging.getLogger(__name__)

_limiter = asyncio.Semaphore(10)


async def _file_already_indexed(collection_id: str, file_path: str) -> bool:
    """Return True if this file_path already has chunks in the collection."""
    try:
        db = await get_lancedb()
        tbl = db.open_table(f"{collection_id}_chunks")
        if "path" not in tbl.schema.names:
            return False  # Old table without path column — can't dedup
        escaped = file_path.replace("\\", "\\\\").replace('"', '\\"')
        rows = tbl.search().where(f'path = "{escaped}"', prefilter=True).limit(1).to_list()
        return len(rows) > 0
    except Exception:
        return False  # Table doesn't exist yet — nothing indexed


_NER_BATCH_SIZE = 200   # flush to LanceDB every N results
_NER_CONCURRENCY = 16  # parallel spaCy workers


async def _run_ner_pass(collection_id: str, job_id: str) -> None:
    """Tag all untagged (or outdated) chunks in the collection with NER.

    Uses batched LanceDB writes (_NER_BATCH_SIZE) to avoid per-row update overhead.
    Raises immediately if en_core_web_trf is not installed.
    """
    try:
        await check_ner_ready()  # fail loudly if spaCy trf not installed
        chunks = await get_outdated_ner_chunks(collection_id, NER_VERSION)
        if not chunks:
            return

        total = len(chunks)
        logger.info(f"[ner] tagging {total} chunks for collection {collection_id}")

        tagged = 0
        errors = 0
        semaphore = asyncio.Semaphore(_NER_CONCURRENCY)
        pending_batch: list[dict] = []
        batch_lock = asyncio.Lock()

        async def _flush_batch(force: bool = False) -> None:
            nonlocal tagged
            async with batch_lock:
                if not pending_batch:
                    return
                if not force and len(pending_batch) < _NER_BATCH_SIZE:
                    return
                batch = pending_batch[:]
                pending_batch.clear()
            written = await bulk_update_chunk_ner_tags(collection_id, batch)
            tagged += written
            if tagged % 1000 < _NER_BATCH_SIZE or force:
                logger.info(f"[ner] {tagged}/{total} tagged for {collection_id}")

        async def _tag_one(chunk: dict) -> None:
            nonlocal errors
            chunk_id = chunk.get("id", "")
            text = chunk.get("text", "")
            if not chunk_id or not text:
                return
            async with semaphore:
                try:
                    tags = await tag_chunk(text, llm_ner_spans=None, use_regex_citations=True)
                    async with batch_lock:
                        pending_batch.append({
                            "id": chunk_id,
                            "ner_tags": tags_to_json(tags),
                            "ner_version": NER_VERSION,
                        })
                    await _flush_batch()
                except Exception as e:
                    logger.warning(f"[ner] chunk {chunk_id} failed: {e}")
                    errors += 1

        await asyncio.gather(*[_tag_one(c) for c in chunks])
        await _flush_batch(force=True)  # write final partial batch
        logger.info(f"[ner] done: {tagged} tagged, {errors} errors for collection {collection_id}")
    except Exception as e:
        logger.error(f"[ner] NER pass failed for collection {collection_id}: {e}")


async def run_ingest_pipeline(
    job_id: str,
    collection_id: str,
    folder_path: str,
    options: IngestOptions,
) -> None:
    """Full ingestion pipeline: scan → extract → chunk → embed → NER."""
    from app.core.rust_bridge import (
        get_ingestion_engine, get_index_manager,
        rust_init_collection_async, rust_insert_chunks_async,
    )

    engine = get_ingestion_engine()
    im = get_index_manager()

    if im is None:
        logger.warning("[ingest] Rust core not available — BM25 indexing disabled, vector-only mode")
    else:
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
    skipped = 0
    all_chunks = []

    for entry in entries:
        if await jm.is_cancelled(job_id):
            await update_ingest_job(job_id, {"status": "cancelled"})
            return

        file_path = entry.get("path", "")

        # ── hash dedup: skip files already indexed in this collection ──────
        if await _file_already_indexed(collection_id, file_path):
            logger.info(f"[ingest] skip (already indexed): {file_path}")
            skipped += 1
            processed += 1
            jm.emit(job_id, {
                "type": "progress",
                "job_id": job_id,
                "processed": processed,
                "total": total,
                "current_file": file_path,
                "progress": processed / max(total, 1),
                "skipped": skipped,
            })
            continue

        try:
            await update_ingest_job(
                job_id,
                {
                    "current_file": file_path,
                    "processed_docs": processed,
                    "progress": processed / max(total, 1),
                },
            )

            doc_data = await extract_text_smart(
                entry["path"],
                entry.get("file_type", "unknown"),
                engine=engine,
            )

            summary = await generate_doc_summary(doc_data.get("raw_text", "")) if settings.enable_contextual_prefix else ""

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
                if settings.enable_contextual_prefix and summary:
                    contextual = await generate_contextual_prefix(summary, chunk["text"])
                else:
                    contextual = chunk["text"]
                enriched_chunks.append({**chunk, "contextual_text": contextual})

            if enriched_chunks:
                embeddings = await embed_texts([c["contextual_text"] for c in enriched_chunks])
                doc_uuid = str(uuid.uuid4())
                file_name = Path(file_path).name if file_path else f"doc-{doc_uuid[:8]}"

                for i, chunk in enumerate(enriched_chunks):
                    chunk_record = {
                        "id": str(uuid.uuid4()),
                        "doc_id": doc_uuid,
                        "collection_id": collection_id,
                        "path": file_path,            # stored for dedup + document listing
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

            # ── checkpoint: record last successfully completed file ────────
            await update_ingest_job(
                job_id,
                {
                    "processed_docs": processed,
                    "progress": processed / max(total, 1),
                    "current_file": file_path,
                    "last_completed_file": file_path,
                },
            )

            jm.emit(job_id, {
                "type": "progress",
                "job_id": job_id,
                "processed": processed,
                "total": total,
                "current_file": file_path,
                "progress": processed / max(total, 1),
            })

        except Exception as e:
            logger.error(f"Failed to process {file_path}: {e}")
            processed += 1
            continue

    if all_chunks:
        await flush_chunks(all_chunks, collection_id, job_id, im)

    await update_collection(collection_id, {"doc_count": total - skipped, "status": "active"})
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

    # ── NER pass (background, after ingest marked completed) ─────────────
    asyncio.create_task(_run_ner_pass(collection_id, job_id))


async def flush_chunks(
    chunks: list[dict],
    collection_id: str,
    job_id: str,
    im,
) -> None:
    """Persist a batch of chunks to LanceDB and the Rust BM25 index."""
    table_name = f"{collection_id}_chunks"
    db = await get_lancedb()

    # Add default NER columns so chunks are compatible with migrated table schema
    for chunk in chunks:
        chunk.setdefault("ner_tags", "")
        chunk.setdefault("ner_tagged", False)
        chunk.setdefault("ner_tagged_at", 0)
        chunk.setdefault("ner_version", 0)

    try:
        tbl = db.open_table(table_name)
        # Migrate: add columns if the table predates these fields
        schema_names = tbl.schema.names
        if "path" not in schema_names:
            tbl.add_columns({"path": "cast('' as string)"})
        if "ner_tags" not in schema_names:
            tbl.add_columns({"ner_tags": "cast('' as string)"})
        if "ner_tagged" not in schema_names:
            tbl.add_columns({"ner_tagged": "cast(false as boolean)"})
        if "ner_tagged_at" not in schema_names:
            tbl.add_columns({"ner_tagged_at": "cast(0 as bigint)"})
        if "ner_version" not in schema_names:
            tbl.add_columns({"ner_version": "cast(0 as int)"})
        tbl.add(chunks)
    except Exception:
        # Table doesn't exist yet — create from first batch
        try:
            db.create_table(table_name, data=chunks, exist_ok=True)
        except Exception as e:
            logger.error(f"[flush] failed to create/write chunks table {table_name}: {e}")
            return

    if im is not None:
        chunks_json = json.dumps(chunks)
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: im.insert_chunks(collection_id, chunks_json),
            )
        except Exception as e:
            logger.warning(f"[flush] BM25 insert_chunks failed (non-fatal): {e}")


async def _run_ner_only(chunk_records: list[dict]) -> dict[str, str]:
    """Run spaCy NER on chunks without a LLM call (used when extract_entities=False)."""
    async def _tag_one(chunk: dict) -> tuple[str, str]:
        tags = await tag_chunk(chunk["text"], [])
        return chunk["id"], tags_to_json(tags)

    ner_map: dict[str, str] = {}
    results = await asyncio.gather(*[_tag_one(c) for c in chunk_records], return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            continue
        cid, tags_json = r
        ner_map[cid] = tags_json
    return ner_map


async def _extract_graph(
    chunk_records: list[dict],
    doc_id: str,
    collection_id: str,
    doc_summary: str,
    validator,
) -> tuple[list[dict], list[dict], dict[str, str]]:
    """Run LLM entity extraction + NER tagging on a batch of chunks.

    Returns (nodes, edges, ner_tags_map) where ner_tags_map maps chunk_id → ner_tags JSON string.
    """
    nodes: list[dict] = []
    edges: list[dict] = []

    async def _extract_one(chunk: dict) -> tuple[list[dict], list[dict], str]:
        async with _limiter:
            try:
                result = await extract_from_chunk(chunk["text"])
            except Exception as e:
                logger.warning(f"Extraction failed: {e}")
                return [], [], "[]"

            raw_entities = result.get("entities", [])
            raw_relations = result.get("relationships", [])

            if validator:
                try:
                    report_json = validator.validate(
                        json.dumps(raw_entities),
                        json.dumps([
                            {
                                "source": r.get("source", ""),
                                "target": r.get("target", ""),
                                "predicate": r.get("predicate", ""),
                                "context": r.get("context", ""),
                                "confidence": r.get("confidence", 0.5),
                            }
                            for r in raw_relations
                        ]),
                        0.4,
                    )
                    report = json.loads(report_json)
                    raw_entities = [
                        e for e in raw_entities
                        if e.get("name") in (report.get("valid_entities") or [e.get("name")])
                    ]
                except Exception:
                    pass

            chunk_nodes: list[dict] = []
            name_to_id: dict[str, str] = {}

            for e in raw_entities:
                nid = str(uuid.uuid4())
                name_to_id[e.get("name", "")] = nid
                chunk_nodes.append({
                    "id": nid,
                    "collection_id": collection_id,
                    "label": e.get("name", ""),
                    "entity_type": e.get("entity_type", "Concept"),
                    "description": e.get("description", ""),
                    "aliases": e.get("aliases", []),
                    "confidence": float(e.get("confidence", 0.7)),
                    "source_chunk_ids": [chunk["id"]],
                    "topics": result.get("topics", []),
                    "properties": {},
                })

            chunk_edges: list[dict] = []
            for r in raw_relations:
                src_id = name_to_id.get(r.get("source", ""))
                tgt_id = name_to_id.get(r.get("target", ""))
                if src_id and tgt_id:
                    chunk_edges.append({
                        "id": str(uuid.uuid4()),
                        "collection_id": collection_id,
                        "source": src_id,
                        "source_id": src_id,
                        "target": tgt_id,
                        "target_id": tgt_id,
                        "relation_type": r.get("predicate", "RELATED_TO"),
                        "edge_type": r.get("predicate", "RELATED_TO"),
                        "weight": float(r.get("confidence", 0.7)),
                        "context": r.get("context", ""),
                        "chunk_id": chunk["id"],
                    })

            try:
                llm_ner_spans = result.get("ner_spans", [])
                ner_tags = await tag_chunk(chunk["text"], llm_ner_spans)
                chunk_ner_json = tags_to_json(ner_tags)
            except Exception as e:
                logger.warning(f"NER tagging failed for chunk {chunk['id']}: {e}")
                chunk_ner_json = "[]"

            return chunk_nodes, chunk_edges, chunk_ner_json

    tasks = [asyncio.create_task(_extract_one(c)) for c in chunk_records]
    task_results = await asyncio.gather(*tasks, return_exceptions=True)

    ner_tags_map: dict[str, str] = {}
    for chunk, r in zip(chunk_records, task_results):
        if isinstance(r, Exception):
            ner_tags_map[chunk["id"]] = "[]"
            continue
        n, e, ner_json = r
        nodes.extend(n)
        edges.extend(e)
        ner_tags_map[chunk["id"]] = ner_json

    return nodes, edges, ner_tags_map


async def _flush_graph(
    nodes: list[dict],
    edges: list[dict],
    collection_id: str,
    im,
) -> None:
    """Persist graph nodes/edges to LanceDB and optionally load into Rust in-memory graph."""
    merged_nodes: dict[str, dict] = {}
    for n in nodes:
        label = n["label"].lower()
        if label in merged_nodes:
            existing = merged_nodes[label]
            existing["source_chunk_ids"] = list(
                set(existing.get("source_chunk_ids", []) + n.get("source_chunk_ids", []))
            )
            existing["confidence"] = max(existing.get("confidence", 0.0), n.get("confidence", 0.0))
        else:
            merged_nodes[label] = dict(n)

    final_nodes = list(merged_nodes.values())
    final_edges = list(edges)

    for n in final_nodes:
        await upsert_graph_node(collection_id, n)
    for e in final_edges:
        await upsert_graph_edge(collection_id, e)

    if im is None:
        return

    loop = asyncio.get_event_loop()
    try:
        rust_nodes = [
            {
                "id": n["id"],
                "node_type": {"custom": n.get("entity_type", "Concept")},
                "label": n["label"],
                "description": n.get("description"),
                "aliases": n.get("aliases") or [],
                "confidence": n.get("confidence", 0.7),
                "ontology_class": n.get("entity_type"),
                "properties": {},
                "collection_id": collection_id,
                "created_at": None,
                "updated_at": None,
            }
            for n in final_nodes
        ]
        await loop.run_in_executor(None, lambda: im.upsert_nodes(collection_id, json.dumps(rust_nodes)))
    except Exception as e:
        logger.warning(f"Rust upsert_nodes failed: {e}")

    try:
        rust_edges = [
            {
                "id": e["id"],
                "source": e["source"],
                "target": e["target"],
                "edge_type": {"custom": e.get("relation_type", "RELATED_TO")},
                "weight": e.get("weight", 0.7),
                "context": e.get("context"),
                "chunk_id": e.get("chunk_id"),
                "properties": {},
                "collection_id": collection_id,
            }
            for e in final_edges
        ]
        await loop.run_in_executor(None, lambda: im.upsert_edges(collection_id, json.dumps(rust_edges)))
    except Exception as e:
        logger.warning(f"Rust upsert_edges failed: {e}")
