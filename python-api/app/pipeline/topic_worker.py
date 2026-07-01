"""Topic extraction worker — separate background pipeline for thematic topics.

Runs after the NER pass and populates:
  - chunks.topics
  - {collection_id}_topics table (centroids, counts, keywords)
  - nodes.topics (via later propagation)

It is config-gated by settings.enable_topic_extraction and follows the same
batched, bounded-concurrency pattern as the NER pipeline.
"""

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from app.config import get_settings
from app.db.lancedb_client import (
    get_lancedb,
    get_outdated_topic_chunks,
    bulk_update_chunk_topics,
    upsert_topics,
    list_graph_nodes,
    upsert_graph_nodes,
)
from app.llm.topic_extractor import (
    TOPIC_VERSION,
    extract_topics_from_chunk,
    canonicalize_topics,
    infer_topic_relationships,
)
from app.llm.embedder import embed_texts
from app.core.rust_bridge import get_index_manager

logger = logging.getLogger(__name__)
settings = get_settings()


async def _run_topic_extraction_pass(collection_id: str, job_id: Optional[str] = None) -> dict:
    """Extract topics for all outdated chunks in a collection and persist them.

    Args:
        collection_id: UUID of the collection to process.
        job_id: Optional ingest job ID for logging context.

    Returns:
        dict with topic_counts, topics_added, chunks_updated, error info.
    """
    if not settings.enable_topic_extraction:
        return {"skipped": True, "reason": "enable_topic_extraction is false"}

    log_prefix = f"[topics][{collection_id}]"
    if job_id:
        log_prefix = f"[topics][{job_id}][{collection_id}]"

    chunks = await get_outdated_topic_chunks(collection_id, TOPIC_VERSION)
    if not chunks:
        logger.info(f"{log_prefix} no chunks need topic extraction")
        return {"chunks_updated": 0, "topics_added": 0, "skipped": True, "reason": "no outdated chunks"}

    total = len(chunks)
    logger.info(f"{log_prefix} extracting topics for {total} chunks")

    batch_size = max(1, settings.topic_batch_size)
    concurrency = max(1, settings.topic_extraction_concurrency)

    semaphore = asyncio.Semaphore(concurrency)
    pending_batch: list[dict] = []
    batch_lock = asyncio.Lock()

    updated = 0
    errors = 0

    # topic_name -> metadata aggregated across all chunks
    topic_stats: dict[str, dict] = defaultdict(lambda: {
        "chunk_ids": set(),
        "keywords": set(),
        "embeddings": [],
        "entity_links": defaultdict(set),
    })

    async def _flush_batch(force: bool = False) -> None:
        nonlocal updated
        async with batch_lock:
            if not pending_batch:
                return
            if not force and len(pending_batch) < batch_size:
                return
            batch = pending_batch[:]
            pending_batch.clear()
        written = await bulk_update_chunk_topics(collection_id, batch)
        updated += written
        if (updated % 1000 < batch_size) or force:
            logger.info(f"{log_prefix} {updated}/{total} chunk topics written")

    async def _extract_one(chunk: dict) -> tuple[str, list[dict], list[dict]]:
        """Extract topics from one chunk and return (chunk_id, topics, entity_links)."""
        async with semaphore:
            text = chunk.get("contextual_text") or chunk.get("text") or ""
            try:
                result = await extract_topics_from_chunk(text)
                return chunk["id"], result.get("topics", []), result.get("entity_topic_links", [])
            except Exception as e:
                logger.warning(f"{log_prefix} extraction failed for chunk {chunk['id']}: {e}")
                return chunk["id"], [], []

    # Stage 1: extract raw topic candidates per chunk
    tasks = [asyncio.create_task(_extract_one(c)) for c in chunks]
    chunk_results: list[tuple[str, list[dict], list[dict]]] = []
    for task in asyncio.as_completed(tasks):
        try:
            chunk_results.append(await task)
        except Exception as e:
            logger.warning(f"{log_prefix} chunk task failed: {e}")
            errors += 1

    if not chunk_results:
        logger.warning(f"{log_prefix} all chunk extractions failed")
        return {"chunks_updated": 0, "topics_added": 0, "errors": errors}

    # Collect all unique topic names for Stage 2 canonicalization
    all_topic_names: set[str] = set()
    for _, topics, _ in chunk_results:
        for t in topics:
            all_topic_names.add(t["name"])

    logger.info(f"{log_prefix} canonicalizing {len(all_topic_names)} raw topic labels")

    # Stage 2: canonicalize
    canonical_mappings = await canonicalize_topics(list(all_topic_names))
    variant_to_canonical: dict[str, str] = {}
    for canonical, variants in canonical_mappings.items():
        for variant in variants:
            variant_to_canonical[variant] = canonical
        # Ensure canonical itself maps to itself
        variant_to_canonical[canonical] = canonical

    def _canonicalize(name: str) -> str:
        return variant_to_canonical.get(name, name)

    # Stage 1.5 (persist) + aggregate stats
    cooccurrence_counts: dict[tuple[str, str], int] = defaultdict(int)

    for chunk_id, topics, entity_links in chunk_results:
        canonical_names: list[str] = []
        for t in topics:
            canonical = _canonicalize(t["name"])
            canonical_names.append(canonical)
            stats = topic_stats[canonical]
            stats["chunk_ids"].add(chunk_id)
            stats["keywords"].update(t.get("keywords", []))

        # Store per-chunk topic list as JSON string
        unique_chunk_topics = sorted(set(canonical_names))
        if unique_chunk_topics:
            async with batch_lock:
                pending_batch.append({
                    "id": chunk_id,
                    "topics": json.dumps(unique_chunk_topics),
                    "topic_version": TOPIC_VERSION,
                })
            await _flush_batch()

        # Co-occurrence counts for topic graph
        for i in range(len(unique_chunk_topics)):
            for j in range(i + 1, len(unique_chunk_topics)):
                pair = tuple(sorted((unique_chunk_topics[i], unique_chunk_topics[j])))
                cooccurrence_counts[pair] += 1

        # Entity-topic links for node propagation later
        for link in entity_links:
            canonical_topic = _canonicalize(link["topic"])
            topic_stats[canonical_topic]["entity_links"][link["role"]].add(link["entity_name"])

    await _flush_batch(force=True)

    # Compute topic embeddings by averaging chunk embeddings
    chunk_embedding_map: dict[str, list[float]] = {}
    for chunk in chunks:
        emb = chunk.get("embedding")
        if emb and isinstance(emb, (list, tuple)) and len(emb) > 0:
            chunk_embedding_map[chunk["id"]] = [float(x) for x in emb]

    for canonical, stats in topic_stats.items():
        vectors = [chunk_embedding_map.get(cid) for cid in stats["chunk_ids"]]
        vectors = [v for v in vectors if v]
        if vectors:
            dim = len(vectors[0])
            avg = [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]
            stats["embedding"] = avg
        else:
            stats["embedding"] = None

    # Stage 3: infer topic relationships (optional; may be skipped if too few topics)
    topic_relationships: list[dict] = []
    if len(topic_stats) >= 2:
        cooc_pairs = [
            (a, b, count)
            for (a, b), count in cooccurrence_counts.items()
            if count >= 2
        ]
        try:
            topic_relationships = await infer_topic_relationships(
                list(topic_stats.keys()), cooc_pairs
            )
            logger.info(f"{log_prefix} inferred {len(topic_relationships)} topic relationships")
        except Exception as e:
            logger.warning(f"{log_prefix} topic relationship inference failed: {e}")

    # Persist topics table
    now = int(datetime.now(timezone.utc).timestamp() * 1_000_000)
    topic_records: list[dict] = []
    for canonical, stats in topic_stats.items():
        record = {
            "id": str(uuid.uuid4()),
            "collection_id": collection_id,
            "name": canonical,
            "keywords": sorted(stats["keywords"])[:20],
            "score": 0.0,
            "frequency": len(stats["chunk_ids"]),
            "chunk_count": len(stats["chunk_ids"]),
            "node_count": sum(len(v) for v in stats["entity_links"].values()),
            "created_at": now,
            "updated_at": now,
        }
        if stats.get("embedding"):
            record["embedding"] = stats["embedding"]
        if topic_relationships:
            record["relationships"] = json.dumps([
                r for r in topic_relationships
                if r["subject"] == canonical or r["object"] == canonical
            ])
        topic_records.append(record)

    if topic_records:
        await upsert_topics(collection_id, topic_records)
        logger.info(f"{log_prefix} wrote {len(topic_records)} topics")

    # Propagate topics to graph nodes (optional; best-effort)
    await _propagate_topics_to_nodes(collection_id, topic_stats)

    logger.info(
        f"{log_prefix} done: {updated} chunks updated, {len(topic_records)} topics added, "
        f"{len(topic_relationships)} topic relationships, {errors} errors"
    )

    return {
        "chunks_updated": updated,
        "topics_added": len(topic_records),
        "topic_relationships": len(topic_relationships),
        "errors": errors,
    }


async def _propagate_topics_to_nodes(collection_id: str, topic_stats: dict[str, dict]) -> None:
    """Best-effort propagation of topic labels onto existing graph nodes.

    Matches entity names from entity_topic_links to node labels/aliases and updates
    nodes.topics in LanceDB.
    """
    try:
        nodes = await list_graph_nodes(collection_id)
    except Exception as e:
        logger.warning(f"[topics][{collection_id}] could not list nodes for topic propagation: {e}")
        return

    if not nodes:
        return

    # Build lookup: normalized entity name -> list of node records
    name_to_nodes: dict[str, list[dict]] = defaultdict(list)
    for node in nodes:
        names = {node.get("label", "").strip().lower()}
        for alias in node.get("aliases", []) or []:
            names.add(str(alias).strip().lower())
        for name in names:
            if name:
                name_to_nodes[name].append(node)

    node_topics: dict[str, set[str]] = defaultdict(set)

    for canonical, stats in topic_stats.items():
        for role, entities in stats["entity_links"].items():
            for entity in entities:
                for node in name_to_nodes.get(entity.strip().lower(), []):
                    node_topics[node["id"]].add(canonical)

    if not node_topics:
        return

    now = int(datetime.now(timezone.utc).timestamp() * 1_000_000)
    updated_nodes: list[dict] = []
    for node in nodes:
        nid = node["id"]
        if nid not in node_topics:
            continue
        existing = set(node.get("topics", []) or [])
        merged = sorted(existing | node_topics[nid])
        if merged != sorted(existing):
            updated = dict(node)
            updated["topics"] = merged
            updated["updated_at"] = now
            updated_nodes.append(updated)

    if updated_nodes:
        try:
            await upsert_graph_nodes(collection_id, updated_nodes)
            logger.info(f"[topics][{collection_id}] propagated topics to {len(updated_nodes)} nodes")
        except Exception as e:
            logger.warning(f"[topics][{collection_id}] node topic propagation failed: {e}")

    # Also push to Rust in-memory graph if available
    im = get_index_manager()
    if im and updated_nodes:
        try:
            import json as json_mod
            loop = asyncio.get_event_loop()
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
                    "topics": n.get("topics", []),
                }
                for n in updated_nodes
            ]
            await loop.run_in_executor(
                None,
                lambda: im.upsert_nodes(collection_id, json_mod.dumps(rust_nodes)),
            )
        except Exception as e:
            logger.warning(f"[topics][{collection_id}] Rust node topic update failed: {e}")
