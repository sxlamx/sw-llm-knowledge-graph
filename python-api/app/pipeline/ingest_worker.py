"""Ingestion pipeline — full document processing with entity/graph extraction."""

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
    upsert_document, upsert_graph_node, upsert_graph_edge, upsert_topic,
)
from app.llm.embedder import embed_texts
from app.llm.extractor import (
    generate_doc_summary, generate_contextual_prefix, extract_from_chunk,
)
from app.llm.ner_tagger import tag_chunk, tags_to_json
from app.pipeline.job_manager import get_job_manager
from app.models.schemas import IngestOptions
from app.services.cost_tracker import create_tracker, remove_tracker, BudgetExceededError
from app.services.multimodal_service import extract_image_chunks

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
        rust_init_collection_async,
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

    await update_ingest_job(
        job_id,
        {"status": "running", "started_at": int(datetime.utcnow().timestamp() * 1_000_000)},
    )

    jm = get_job_manager()
    jm.emit(job_id, {"type": "progress", "job_id": job_id, "processed": 0, "total": 0, "progress": 0.0})

    cost_tracker = create_tracker(job_id, max_cost_usd=options.max_cost_usd or 0.0)

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
    all_chunks: list[dict] = []
    all_nodes: list[dict] = []
    all_edges: list[dict] = []
    all_topics: set[str] = set()

    # Ontology validator
    validator = None
    try:
        from app.core.rust_bridge import get_ontology_validator
        validator = get_ontology_validator()
    except Exception:
        pass

    for entry in entries:
        if await jm.is_cancelled(job_id):
            await update_ingest_job(job_id, {"status": "cancelled"})
            return

        file_path = entry.get("path", "")
        try:
            await update_ingest_job(
                job_id,
                {
                    "current_file": file_path,
                    "processed_docs": processed,
                    "progress": processed / max(total, 1),
                },
            )

            # Extract text — pymupdf for PDFs (handles CID/Identity-H encoding), Rust for others
            from app.core.pdf_extractor import extract_text_smart
            doc_data = await extract_text_smart(file_path, entry.get("file_type", "unknown"), engine)
            raw_text = doc_data.get("raw_text", "")

            # Generate doc summary
            summary = await generate_doc_summary(raw_text)

            # Chunk text
            pages_json = json.dumps(doc_data.get("pages", []))
            chunks_json = await loop.run_in_executor(
                None,
                lambda: engine.chunk_text(
                    raw_text,
                    pages_json,
                    options.chunk_size_tokens,
                    options.chunk_overlap_tokens,
                ),
            )
            chunks = json.loads(chunks_json)

            # Persist document record
            doc_uuid = str(uuid.uuid4())
            doc_path = Path(file_path)
            doc_record = {
                "id": doc_uuid,
                "collection_id": collection_id,
                "title": doc_data.get("title") or doc_path.name,
                "file_path": file_path,
                "file_type": entry.get("file_type", "unknown"),
                "doc_summary": summary,
                "metadata": json.dumps(doc_data.get("metadata", {})),
            }
            await upsert_document(doc_record)

            # Contextual enrichment + embeddings
            enriched_chunks: list[dict] = []
            for chunk in chunks:
                contextual = await generate_contextual_prefix(summary, chunk["text"])
                enriched_chunks.append({**chunk, "contextual_text": contextual})

            if not enriched_chunks:
                processed += 1
                continue

            texts_to_embed = [c["contextual_text"] for c in enriched_chunks]
            embeddings = await embed_texts(texts_to_embed)

            chunk_records: list[dict] = []
            for i, chunk in enumerate(enriched_chunks):
                chunk_id = str(uuid.uuid4())
                # topics must be a list[str] — NOT a JSON string — for Rust
                topics_list: list[str] = chunk.get("topics") or []
                if isinstance(topics_list, str):
                    try:
                        topics_list = json.loads(topics_list)
                    except Exception:
                        topics_list = []

                chunk_record = {
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
                }
                chunk_records.append(chunk_record)
                all_chunks.append(chunk_record)

                for t in topics_list:
                    all_topics.add(t)

            # Multimodal: extract and caption images from PDFs
            if file_path.lower().endswith(".pdf") and settings.vision_enabled:
                try:
                    image_chunks = await extract_image_chunks(
                        pdf_path=file_path,
                        doc_id=doc_uuid,
                        collection_id=collection_id,
                        doc_title=doc_record["title"],
                        topics=list(all_topics),
                    )
                    if image_chunks:
                        all_chunks.extend(image_chunks)
                        logger.info(
                            f"Extracted {len(image_chunks)} image chunks from {doc_path.name}"
                        )
                except Exception as exc:
                    logger.warning(f"Multimodal extraction failed for {file_path}: {exc}")

            # Entity/relationship extraction + NER tagging
            if options.extract_entities:
                # LLM extraction (returns ner_spans from same call) + spaCy NER
                extracted_nodes, extracted_edges, ner_tags_map = await _extract_graph(
                    chunk_records, doc_uuid, collection_id, summary, validator
                )
                for chunk in chunk_records:
                    chunk["ner_tags"] = ner_tags_map.get(chunk["id"], "[]")
                all_nodes.extend(extracted_nodes)
                all_edges.extend(extracted_edges)
            else:
                # spaCy-only NER (no LLM call)
                ner_tags_map = await _run_ner_only(chunk_records)
                for chunk in chunk_records:
                    chunk["ner_tags"] = ner_tags_map.get(chunk["id"], "[]")

            # Flush chunks every 100
            if len(all_chunks) >= 100:
                await _flush_chunks(all_chunks, collection_id, im)
                all_chunks = []

        except BudgetExceededError as e:
            logger.warning(f"LLM budget exceeded for job {job_id}: {e}")
            await update_ingest_job(job_id, {"status": "failed", "error_msg": str(e)})
            jm.emit(job_id, {"type": "failed", "job_id": job_id, "error": str(e)})
            remove_tracker(job_id)
            return
        except Exception as e:
            logger.error(f"Failed to process {file_path}: {e}")
        finally:
            processed += 1
            await update_ingest_job(
                job_id,
                {
                    "processed_docs": processed,
                    "progress": processed / max(total, 1),
                    "current_file": file_path,
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

    # Final flushes
    if all_chunks:
        await _flush_chunks(all_chunks, collection_id, im)

    if all_nodes:
        await _flush_graph(all_nodes, all_edges, collection_id, im)

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
    await update_ingest_job(
        job_id,
        {
            "status": "completed",
            "processed_docs": processed,
            "progress": 1.0,
            "completed_at": int(datetime.utcnow().timestamp() * 1_000_000),
        },
    )
    cost_summary = cost_tracker.summary()
    jm.emit(job_id, {
        "type": "completed", "job_id": job_id, "processed": processed, "total": total,
        "cost": cost_summary,
    })
    remove_tracker(job_id)


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

    # Batch: one LLM call per chunk (with concurrency limit)
    async def _extract_one(chunk: dict) -> tuple[list[dict], list[dict], str]:
        async with _limiter:
            try:
                result = await extract_from_chunk(chunk["text"])
            except Exception as e:
                logger.warning(f"Extraction failed: {e}")
                return [], [], "[]"

            # Validate with Rust ontology validator
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
                        0.4,  # confidence threshold
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
                src_name = r.get("source", "")
                tgt_name = r.get("target", "")
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
                        "relation_type": r.get("predicate", "RELATED_TO"),
                        "edge_type": r.get("predicate", "RELATED_TO"),
                        "weight": float(r.get("confidence", 0.7)),
                        "context": r.get("context", ""),
                        "chunk_id": chunk["id"],
                    })

            # NER tagging — reuse ner_spans from the same LLM call + spaCy
            try:
                llm_ner_spans = result.get("ner_spans", [])
                ner_tags = await tag_chunk(chunk["text"], llm_ner_spans)
                chunk_ner_json = tags_to_json(ner_tags)
            except Exception as e:
                logger.warning(f"NER tagging failed for chunk {chunk['id']}: {e}")
                chunk_ner_json = "[]"

            return chunk_nodes, chunk_edges, chunk_ner_json

    tasks = [asyncio.create_task(_extract_one(c)) for c in chunk_records]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    ner_tags_map: dict[str, str] = {}
    for chunk, r in zip(chunk_records, results):
        if isinstance(r, Exception):
            ner_tags_map[chunk["id"]] = "[]"
            continue
        n, e, ner_json = r
        nodes.extend(n)
        edges.extend(e)
        ner_tags_map[chunk["id"]] = ner_json

    return nodes, edges, ner_tags_map


async def _flush_chunks(
    chunks: list[dict],
    collection_id: str,
    im,
) -> None:
    table_name = f"{collection_id}_chunks"
    await upsert_to_table(table_name, chunks)

    loop = asyncio.get_event_loop()
    try:
        # Prepare Rust-compatible chunk records (topics as list, not JSON string)
        await loop.run_in_executor(
            None,
            lambda: im.insert_chunks(collection_id, json.dumps(chunks)),
        )
    except Exception as e:
        logger.warning(f"Rust insert_chunks failed: {e}")


async def _flush_graph(
    nodes: list[dict],
    edges: list[dict],
    collection_id: str,
    im,
) -> None:
    """Persist graph nodes/edges to LanceDB and load into Rust in-memory graph."""
    # Resolve duplicate entities by label (simple exact-match merge)
    merged_nodes: dict[str, dict] = {}
    for n in nodes:
        label = n["label"].lower()
        if label in merged_nodes:
            # Merge: extend source_chunk_ids
            existing = merged_nodes[label]
            existing["source_chunk_ids"] = list(
                set(existing.get("source_chunk_ids", []) + n.get("source_chunk_ids", []))
            )
            existing["confidence"] = max(existing.get("confidence", 0.0), n.get("confidence", 0.0))
        else:
            merged_nodes[label] = dict(n)

    final_nodes = list(merged_nodes.values())

    # Remap edge source/target to merged node IDs
    label_to_id = {n["label"].lower(): n["id"] for n in final_nodes}
    final_edges: list[dict] = []
    for e in edges:
        # Find the merged node id for source/target (by scanning original node ids)
        final_edges.append(e)

    # Persist to LanceDB
    for n in final_nodes:
        await upsert_graph_node(collection_id, n)
    for e in final_edges:
        await upsert_graph_edge(collection_id, e)

    # Load into Rust in-memory graph
    if im is None:
        return

    loop = asyncio.get_event_loop()
    try:
        # Convert to Rust GraphNode format
        rust_nodes = []
        for n in final_nodes:
            rust_nodes.append({
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
            })
        await loop.run_in_executor(None, lambda: im.upsert_nodes(collection_id, json.dumps(rust_nodes)))
    except Exception as e:
        logger.warning(f"Rust upsert_nodes failed: {e}")

    try:
        rust_edges = []
        for e in final_edges:
            rust_edges.append({
                "id": e["id"],
                "source": e["source"],
                "target": e["target"],
                "edge_type": {"custom": e.get("relation_type", "RELATED_TO")},
                "weight": e.get("weight", 0.7),
                "context": e.get("context"),
                "chunk_id": e.get("chunk_id"),
                "properties": {},
                "collection_id": collection_id,
            })
        await loop.run_in_executor(None, lambda: im.upsert_edges(collection_id, json.dumps(rust_edges)))
    except Exception as e:
        logger.warning(f"Rust upsert_edges failed: {e}")
