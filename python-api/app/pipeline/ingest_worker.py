"""Ingestion pipeline — full document processing."""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List

from app.config import get_settings
from app.db.lancedb_client import (
    get_lancedb, get_ingest_job, update_collection, update_ingest_job,
    upsert_to_table, upsert_graph_node, upsert_graph_edge,
    upsert_graph_nodes, upsert_graph_edges,
    get_outdated_ner_chunks, update_chunk_ner_tags, bulk_update_chunk_ner_tags,
)
from app.llm.embedder import embed_texts
from app.llm.extractor import (
    generate_doc_summary, generate_contextual_prefix, extract_from_chunk,
)
from app.llm.ner_tagger import tag_chunk, tags_to_json, NER_VERSION, check_ner_ready
from app.llm.two_stage_extractor import TwoStageExtractor
from app.llm.edge_pruner import EdgePruner
from app.models.template import TemplateConfig
from app.services.merge_strategy import MergeStrategy
from app.services.entity_merger import EntityMerger
from app.services.template_factory import TemplateFactory, _compile_key_pattern
from app.services.template_gallery import TemplateGallery
from app.core.pdf_extractor import extract_text_smart
from app.pipeline.job_manager import get_job_manager
from app.pipeline.topic_worker import _run_topic_extraction_pass
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
    await check_ner_ready()
    try:
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
    await jm.emit(job_id, {"type": "progress", "job_id": job_id, "processed": 0, "total": 0, "progress": 0.0})

    if engine is None:
        await update_ingest_job(job_id, {"status": "failed", "error_msg": "Ingestion engine not available"})
        return

    from app.services.cost_tracker import create_tracker, remove_tracker
    max_cost = getattr(options, "max_cost_usd", 0.0) or 0.0
    create_tracker(job_id, max_cost_usd=max_cost)

    loop = asyncio.get_running_loop()
    try:
        entries_json = await loop.run_in_executor(
            None,
            lambda: engine.scan_folder(folder_path, [folder_path], options.max_depth, options.max_files),
        )
        entries = json.loads(entries_json)
    except Exception as e:
        logger.error(f"Scan failed: {e}")
        await update_ingest_job(job_id, {"status": "failed", "error_msg": str(e)})
        remove_tracker(job_id)
        return

    total = len(entries)
    await update_ingest_job(job_id, {"total_docs": total, "status": "running"})

    processed = 0
    skipped = 0
    all_chunks = []
    all_nodes = []
    all_edges = []

    for entry in entries:
        if await jm.is_cancelled(job_id):
            await update_ingest_job(job_id, {"status": "cancelled"})
            remove_tracker(job_id)
            return

        file_path = entry.get("path", "")

        # ── hash dedup: skip files already indexed in this collection ──────
        if await _file_already_indexed(collection_id, file_path):
            logger.info(f"[ingest] skip (already indexed): {file_path}")
            skipped += 1
            processed += 1
            await jm.emit(job_id, {
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

                chunk_records = []
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

                    chunk_records.append(chunk_record)
                    all_chunks.append(chunk_record)

                # ── Entity extraction (per-document, template-aware) ──────
                if options.extract_entities:
                    validator = None
                    try:
                        from app.core.rust_bridge import get_ontology_validator
                        validator = get_ontology_validator()
                    except Exception:
                        pass

                    template_config = None
                    if options.template:
                        try:
                            gallery = TemplateGallery.get_instance()
                            template_config = gallery.get(options.template)
                        except Exception:
                            pass

                    if template_config:
                        ns, es, _ = await _extract_graph_with_template(
                            chunk_records, collection_id, template_config, job_id=job_id,
                        )
                    else:
                        ns, es, _ = await _extract_graph(
                            chunk_records, doc_uuid, collection_id, summary, validator,
                        )
                    all_nodes.extend(ns)
                    all_edges.extend(es)

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

            await jm.emit(job_id, {
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
    if all_nodes:
        await _flush_graph(all_nodes, all_edges, collection_id, im)

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

    await jm.emit(job_id, {
        "type": "completed",
        "job_id": job_id,
        "processed": processed,
        "total": total,
    })

    # ── NER pass (background, after ingest marked completed) ─────────────
    asyncio.create_task(_run_ner_pass(collection_id, job_id))
    remove_tracker(job_id)

    # ── Topic extraction pass (background, config-gated) ──────────────────
    if settings.enable_topic_extraction:
        asyncio.create_task(_run_topic_extraction_pass(collection_id, job_id))


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
        loop = asyncio.get_running_loop()
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
                        0.3,
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


async def _extract_graph_with_template(
    chunks: List[dict],
    collection_id: str,
    template: TemplateConfig,
    job_id: Optional[str] = None,
) -> Tuple[List[dict], List[dict], dict[str, str]]:
    """Extract entities and relations using template-driven two-stage extraction."""
    extractor = TwoStageExtractor(template, job_id=job_id)
    all_entities: List[dict] = []
    all_relations: List[dict] = []
    ner_tags_map: dict[str, str] = {}

    for chunk in chunks:
        async with _limiter:
            try:
                if template.extraction.mode == "two_stage":
                    entities, relations = await extractor.extract_two_stage(chunk["text"])
                else:
                    result = await extract_from_chunk(chunk["text"], job_id=job_id)
                    entities = result.get("entities", [])
                    relations = result.get("relationships", [])
            except Exception as e:
                logger.warning(f"Template extraction failed for chunk {chunk.get('id', '?')}: {e}")
                entities, relations = [], []

        chunk_nodes: List[dict] = []
        name_to_id: dict[str, str] = {}

        for e in entities:
            nid = str(uuid.uuid4())
            name = e.get("name", e.get("label", ""))
            name_to_id[name] = nid
            chunk_nodes.append({
                "id": nid,
                "collection_id": collection_id,
                "label": name,
                "entity_type": e.get("entity_type", e.get("type", "Concept")),
                "description": e.get("description", ""),
                "aliases": e.get("aliases", []),
                "confidence": float(e.get("confidence", 0.7)),
                "source_chunk_ids": [chunk["id"]],
                "topics": e.get("topics", []),
                "properties": {},
            })

        chunk_edges: List[dict] = []
        for r in relations:
            src_name = r.get("source", r.get("source_id", ""))
            tgt_name = r.get("target", r.get("target_id", ""))
            src_id = name_to_id.get(src_name)
            tgt_id = name_to_id.get(tgt_name)
            if src_id and tgt_id:
                chunk_edges.append({
                    "id": str(uuid.uuid4()),
                    "collection_id": collection_id,
                    "source": src_id,
                    "source_id": src_id,
                    "target": tgt_id,
                    "target_id": tgt_id,
                    "relation_type": r.get("predicate", r.get("relation_type", "RELATED_TO")),
                    "edge_type": r.get("predicate", r.get("relation_type", "RELATED_TO")),
                    "weight": float(r.get("confidence", r.get("weight", 0.7))),
                    "context": r.get("context", ""),
                    "chunk_id": chunk["id"],
                })

        entity_keys = set(name_to_id.values())

        chunk_edges = EdgePruner.prune(chunk_edges, entity_keys, template)

        entity_key_fn = _compile_key_pattern(template.entity_schema.key) if template.entity_schema else None
        entity_label_fn = None
        if template.entity_schema:
            from app.services.template_factory import _compile_label_pattern
            entity_label_fn = _compile_label_pattern(template.entity_schema.display_label)
        relation_key_fn = None
        relation_label_fn = None
        if template.relation_schema:
            relation_key_fn = _compile_key_pattern(template.relation_schema.key)
            relation_label_fn = _compile_label_pattern(template.relation_schema.display_label)

        for node in chunk_nodes:
            if entity_key_fn:
                raw = {k: v for k, v in node.items() if k not in ("id", "collection_id", "source_chunk_ids", "properties")}
                node["_dedup_key"] = entity_key_fn(raw)
            if entity_label_fn:
                raw = {k: v for k, v in node.items() if k not in ("id", "collection_id", "source_chunk_ids", "properties")}
                node["_display_label"] = entity_label_fn(raw)

        for edge in chunk_edges:
            if relation_key_fn:
                raw = {k: v for k, v in edge.items() if k not in ("id", "collection_id")}
                edge["_dedup_key"] = relation_key_fn(raw)
            if relation_label_fn:
                raw = {k: v for k, v in edge.items() if k not in ("id", "collection_id")}
                edge["_display_label"] = relation_label_fn(raw)

        all_entities.extend(chunk_nodes)
        all_relations.extend(chunk_edges)

        try:
            ner_tags = await tag_chunk(chunk["text"], [])
            ner_tags_map[chunk["id"]] = tags_to_json(ner_tags)
        except Exception as e:
            logger.warning(f"NER tagging failed for chunk {chunk['id']}: {e}")
            ner_tags_map[chunk["id"]] = "[]"

    return all_entities, all_relations, ner_tags_map


async def _flush_graph(
    nodes: list[dict],
    edges: list[dict],
    collection_id: str,
    im,
) -> None:
    """Persist graph nodes/edges to LanceDB and optionally load into Rust in-memory graph."""
    merged_nodes: dict[str, dict] = {}
    for n in nodes:
        merge_key = f"{n.get('entity_type', 'Concept')}::{n['label'].lower()}"
        if merge_key in merged_nodes:
            existing = merged_nodes[merge_key]
            existing["source_chunk_ids"] = list(
                set(existing.get("source_chunk_ids", []) + n.get("source_chunk_ids", []))
            )
            existing["confidence"] = (existing.get("confidence", 0.0) + n.get("confidence", 0.0)) / 2.0
        else:
            merged_nodes[merge_key] = dict(n)

    final_nodes = list(merged_nodes.values())
    final_edges = list(edges)

    await upsert_graph_nodes(collection_id, final_nodes)
    await upsert_graph_edges(collection_id, final_edges)

    if im is None:
        return

    loop = asyncio.get_running_loop()
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
                "predicate": e.get("predicate", e.get("relation_type", "")),
                "time": e.get("time"),
                "location": e.get("location"),
                "participants": e.get("participants"),
                "doc_origins": e.get("doc_origins", []),
            }
            for e in final_edges
        ]
        await loop.run_in_executor(None, lambda: im.upsert_edges(collection_id, json.dumps(rust_edges)))
    except Exception as e:
        logger.warning(f"Rust upsert_edges failed: {e}")


async def run_feed_pipeline(
    job_id: str,
    collection_id: str,
    file_paths: list[str],
    template_key: Optional[str] = None,
) -> None:
    """Incremental feeding: extract new docs, merge into existing graph.

    Unlike run_ingest_pipeline which scans a folder, this takes explicit
    file_paths and merges extracted entities/edges using the template's
    merge strategy rather than simple label-based dedup.
    """
    from app.core.rust_bridge import (
        get_ingestion_engine, get_index_manager,
        rust_insert_chunks_async,
    )

    engine = get_ingestion_engine()
    im = get_index_manager()
    loop = asyncio.get_running_loop()

    template = None
    if template_key:
        try:
            gallery = TemplateGallery.get_instance()
            template = gallery.get(template_key)
        except Exception:
            pass

    strategy_nodes = MergeStrategy(template.extraction.merge_strategy_nodes) if template else MergeStrategy.EXACT
    strategy_edges = MergeStrategy(template.extraction.merge_strategy_edges) if template else MergeStrategy.EXACT

    entity_merger = EntityMerger(template, job_id=job_id)

    await update_ingest_job(job_id, {"status": "running", "started_at": int(datetime.utcnow().timestamp() * 1_000_000)})

    from app.services.cost_tracker import create_tracker, remove_tracker
    create_tracker(job_id, max_cost_usd=0.0)

    jm = get_job_manager()
    total = len(file_paths)
    processed = 0
    all_chunks = []
    all_nodes = []
    all_edges = []

    for file_path in file_paths:
        if await jm.is_cancelled(job_id):
            await update_ingest_job(job_id, {"status": "cancelled"})
            remove_tracker(job_id)
            return

        if await _file_already_indexed(collection_id, file_path):
            logger.info(f"[feed] skip (already indexed): {file_path}")
            processed += 1
            await jm.emit(job_id, {
                "type": "progress",
                "job_id": job_id,
                "processed": processed,
                "total": total,
                "current_file": file_path,
                "progress": processed / max(total, 1),
            })
            continue

        try:
            await update_ingest_job(job_id, {
                "current_file": file_path,
                "processed_docs": processed,
                "progress": processed / max(total, 1),
            })

            doc_data = await extract_text_smart(
                file_path,
                "unknown",
                engine=engine,
            )
            raw_text = doc_data.get("raw_text", "")
            summary = await generate_doc_summary(raw_text) if settings.enable_contextual_prefix else ""

            pages_json = json.dumps(doc_data.get("pages", []))
            chunks_json = await loop.run_in_executor(
                None,
                lambda: engine.chunk_text(raw_text, pages_json, 512, 50),
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

                chunk_records = []
                for i, chunk in enumerate(enriched_chunks):
                    chunk_record = {
                        "id": str(uuid.uuid4()),
                        "doc_id": doc_uuid,
                        "collection_id": collection_id,
                        "path": file_path,
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

                    chunk_records.append(chunk_record)
                    all_chunks.append(chunk_record)

                if options.extract_entities:
                    validator = None
                    try:
                        from app.core.rust_bridge import get_ontology_validator
                        validator = get_ontology_validator()
                    except Exception:
                        pass

                    if template:
                        ns, es, _ = await _extract_graph_with_template(
                            chunk_records, collection_id, template, job_id=job_id,
                        )
                    else:
                        ns, es, _ = await _extract_graph(
                            chunk_records, doc_uuid, collection_id, summary, validator,
                        )
                    all_nodes.extend(ns)
                    all_edges.extend(es)

                if len(all_chunks) >= 100:
                    await flush_chunks(all_chunks, collection_id, job_id, im)
                    all_chunks = []

            processed += 1
            await update_ingest_job(job_id, {
                "processed_docs": processed,
                "progress": processed / max(total, 1),
            })

            await jm.emit(job_id, {
                "type": "progress",
                "job_id": job_id,
                "processed": processed,
                "total": total,
                "current_file": file_path,
                "progress": processed / max(total, 1),
            })

        except Exception as e:
            logger.error(f"Feed file {file_path}: {e}")
            processed += 1
            continue

    if all_chunks:
        await flush_chunks(all_chunks, collection_id, job_id, im)

    # ── Merge nodes/edges using the configured strategy ───────────────────
    if all_nodes:
        if strategy_nodes.is_deterministic and strategy_nodes != MergeStrategy.EXACT and im is not None:
            rust_strategy = strategy_nodes.rust_strategy_name
            if rust_strategy:
                try:
                    nodes_json = json.dumps(_build_rust_nodes(all_nodes, collection_id))
                    report_json = im.merge_nodes_into_collection(collection_id, nodes_json, rust_strategy)
                    report = json.loads(report_json)
                    logger.info(f"[feed] node merge report: {report}")
                    await upsert_graph_nodes(collection_id, all_nodes)
                except Exception as e:
                    logger.warning(f"[feed] Rust node merge failed, falling back to upsert: {e}")
                    await _flush_graph(all_nodes, all_edges, collection_id, im)
            else:
                await _flush_graph(all_nodes, all_edges, collection_id, im)
        elif strategy_nodes.is_llm and im is not None:
            await _llm_merge_nodes(all_nodes, collection_id, entity_merger, strategy_nodes, im, job_id)
            if all_edges:
                if strategy_edges.is_llm and im is not None:
                    await _llm_merge_edges(all_edges, collection_id, entity_merger, strategy_edges, im, job_id)
                elif strategy_edges.is_deterministic and strategy_edges != MergeStrategy.EXACT:
                    rust_strategy = strategy_edges.rust_strategy_name
                    if rust_strategy:
                        try:
                            edges_json = json.dumps(_build_rust_edges(all_edges, collection_id))
                            im.merge_edges_into_collection(collection_id, edges_json, rust_strategy)
                            await upsert_graph_edges(collection_id, all_edges)
                        except Exception as e:
                            logger.warning(f"[feed] Rust edge merge failed: {e}")
                            await _flush_graph([], all_edges, collection_id, im)
                    else:
                        await _flush_graph([], all_edges, collection_id, im)
                else:
                    await _flush_graph([], all_edges, collection_id, im)
            all_edges = []
        else:
            await _flush_graph(all_nodes, all_edges, collection_id, im)
            all_edges = []

    if all_edges:
        if strategy_edges.is_deterministic and strategy_edges != MergeStrategy.EXACT and im is not None:
            rust_strategy = strategy_edges.rust_strategy_name
            if rust_strategy:
                try:
                    edges_json = json.dumps(_build_rust_edges(all_edges, collection_id))
                    im.merge_edges_into_collection(collection_id, edges_json, rust_strategy)
                    await upsert_graph_edges(collection_id, all_edges)
                except Exception as e:
                    logger.warning(f"[feed] Rust edge merge failed: {e}")
                    await upsert_graph_edges(collection_id, all_edges)
        else:
            await _flush_graph([], all_edges, collection_id, im)

    if im is not None:
        try:
            im.prune_dangling_edges_pyo3(collection_id)
        except Exception:
            pass

    await update_ingest_job(job_id, {
        "status": "completed",
        "processed_docs": processed,
        "progress": 1.0,
        "completed_at": int(datetime.utcnow().timestamp() * 1_000_000),
    })
    await jm.emit(job_id, {"type": "completed", "job_id": job_id, "processed": processed, "total": total})

    asyncio.create_task(_run_ner_pass(collection_id, job_id))
    remove_tracker(job_id)


def _build_rust_nodes(nodes: list[dict], collection_id: str) -> list[dict]:
    return [
        {
            "id": n.get("id", str(uuid.uuid4())),
            "node_type": {"custom": n.get("entity_type", "Concept")},
            "label": n.get("label", ""),
            "description": n.get("description"),
            "aliases": n.get("aliases", []),
            "confidence": n.get("confidence", 0.7),
            "ontology_class": n.get("entity_type"),
            "properties": {},
            "collection_id": collection_id,
            "dedup_key": n.get("_dedup_key"),
            "display_label": n.get("_display_label"),
            "doc_origins": n.get("doc_origins", []),
            "created_at": None,
            "updated_at": None,
        }
        for n in nodes
    ]


def _build_rust_edges(edges: list[dict], collection_id: str) -> list[dict]:
    return [
        {
            "id": e.get("id", str(uuid.uuid4())),
            "source": e.get("source", ""),
            "target": e.get("target", ""),
            "edge_type": {"custom": e.get("relation_type", "RELATED_TO")},
            "weight": e.get("weight", 0.7),
            "context": e.get("context"),
            "chunk_id": e.get("chunk_id"),
            "properties": {},
            "collection_id": collection_id,
            "dedup_key": e.get("_dedup_key"),
            "display_label": e.get("_display_label"),
            "predicate": e.get("predicate", e.get("relation_type", "")),
            "time": e.get("time"),
            "location": e.get("location"),
            "participants": e.get("participants"),
            "doc_origins": e.get("doc_origins", []),
        }
        for e in edges
    ]


async def _llm_merge_nodes(
    nodes: list[dict],
    collection_id: str,
    merger: EntityMerger,
    strategy: MergeStrategy,
    im,
    job_id: Optional[str] = None,
) -> None:
    """Detect conflicts via Rust, resolve each via LLM, then upsert."""
    from app.db.lancedb_client import upsert_graph_node, upsert_graph_nodes

    nodes_json = json.dumps(_build_rust_nodes(nodes, collection_id))
    try:
        conflicts_json = im.detect_node_conflicts(collection_id, nodes_json)
        conflicts = json.loads(conflicts_json)
    except Exception:
        await _flush_graph(nodes, [], collection_id, im)
        return

    if not conflicts:
        await _flush_graph(nodes, [], collection_id, im)
        return

    conflict_ids = {c["existing_id"] for c in conflicts}
    incoming_by_id = {n.get("id"): n for n in nodes if n.get("id") not in conflict_ids}

    for conflict in conflicts:
        existing_id = conflict["existing_id"]
        incoming_id = conflict["incoming_id"]
        incoming = next((n for n in nodes if n.get("id") == incoming_id), None)
        if not incoming:
            continue

        existing = await _load_existing_node(collection_id, existing_id)
        if not existing:
            continue

        merged = await merger.merge(existing, incoming, strategy, "node")
        await upsert_graph_node(collection_id, merged)

        try:
            im.update_node(collection_id, json.dumps(_build_rust_nodes([merged], collection_id)[0]))
        except Exception:
            pass

    new_nodes = [n for n in nodes if n.get("id") not in conflict_ids]
    if new_nodes:
        await _flush_graph(new_nodes, [], collection_id, im)


async def _llm_merge_edges(
    edges: list[dict],
    collection_id: str,
    merger: EntityMerger,
    strategy: MergeStrategy,
    im,
    job_id: Optional[str] = None,
) -> None:
    from app.db.lancedb_client import upsert_graph_edge, upsert_graph_edges

    edges_json = json.dumps(_build_rust_edges(edges, collection_id))
    try:
        conflicts_json = im.detect_edge_conflicts(collection_id, edges_json)
        conflicts = json.loads(conflicts_json)
    except Exception:
        await _flush_graph([], edges, collection_id, im)
        return

    if not conflicts:
        await _flush_graph([], edges, collection_id, im)
        return

    conflict_ids = {c["existing_id"] for c in conflicts}

    for conflict in conflicts:
        existing_id = conflict["existing_id"]
        incoming_id = conflict["incoming_id"]
        incoming = next((e for e in edges if e.get("id") == incoming_id), None)
        if not incoming:
            continue

        existing = await _load_existing_edge(collection_id, existing_id)
        if not existing:
            continue

        merged = await merger.merge(existing, incoming, strategy, "edge")
        await upsert_graph_edge(collection_id, merged)

    new_edges = [e for e in edges if e.get("id") not in conflict_ids]
    if new_edges:
        await _flush_graph([], new_edges, collection_id, im)


async def _load_existing_node(collection_id: str, node_id: str) -> Optional[dict]:
    from app.db.lancedb_client import get_graph_node
    return await get_graph_node(collection_id, node_id)


async def _load_existing_edge(collection_id: str, edge_id: str) -> Optional[dict]:
    from app.db.lancedb_client import get_graph_edge
    return await get_graph_edge(collection_id, edge_id)
