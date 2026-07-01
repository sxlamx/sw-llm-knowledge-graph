"""Build knowledge graph from NER tags on chunks.

This is the primary graph-building script for Phase 2 (NER-based, no LLM required).

Algorithm:
1. Load all chunks with ner_tags from LanceDB {collection_id}_chunks
2. Group all NerTag objects by canonical label
3. For each tag: create or merge into existing entity node
4. For each chunk: create co-occurrence edges between all entity pairs in same chunk
5. Write nodes batch to {collection_id}_nodes (LanceDB first)
6. Write edges batch to {collection_id}_edges (LanceDB first)
7. Update in-memory KnowledgeGraph via Rust bridge (brief write lock)
8. Return {added_nodes, merged_nodes, added_edges}
"""

import asyncio
import json
import logging
import math
import re
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Optional

from app.config import get_settings
from app.db.lancedb_client import (
    get_lancedb,
    upsert_graph_nodes,
    upsert_graph_edges,
    list_graph_nodes,
)
from app.core.rust_bridge import get_index_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

settings = get_settings()

ENTITY_TYPE_SCHEMA = {
    "PERSON", "ORGANIZATION", "LOCATION", "DATE", "MONEY", "PERCENT", "LAW",
    "CONCEPT", "EVENT", "DOCUMENT",
    "COURT_CASE", "COURT", "LEGISLATION_TITLE", "LEGISLATION_REFERENCE",
    "STATUTE_SECTION", "JURISDICTION", "LEGAL_CONCEPT", "DEFINED_TERM", "CASE_CITATION",
}

SKIP_LABELS = {"CARDINAL", "ORDINAL", "QUANTITY"}


def _levenshtein(a: str, b: str) -> int:
    n, m = len(a), len(b)
    if n == 0:
        return m
    if m == 0:
        return n
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        curr = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[m]


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    mag1 = math.sqrt(sum(a * a for a in v1))
    mag2 = math.sqrt(sum(b * b for b in v2))
    if mag1 == 0.0 or mag2 == 0.0:
        return 0.0
    return dot / (mag1 * mag2)


def _normalize(text: str) -> str:
    """Normalize text for entity matching — aligned with Rust normalize_name().

    Lowercase, strip non-alphanumeric (except whitespace), collapse whitespace.
    """
    text = text.strip().lower()
    text = re.sub(r'[^a-z0-9\s]', '', text)
    return ' '.join(text.split())


async def build_graph_from_ner(collection_id: str, min_chunk_freq: int = 2, job_id: str | None = None) -> dict:
    """
    Build or update knowledge graph nodes and edges from NER-tagged chunks.

    Args:
        collection_id: Collection UUID
        min_chunk_freq: Minimum chunk appearances for a node to be kept (default 2)

    Returns:
        dict with keys: added_nodes, merged_nodes, added_edges, total_entities, total_cooccurrences
    """
    db = await get_lancedb()

    chunk_table_name = f"{collection_id}_chunks"
    try:
        chunk_tbl = db.open_table(chunk_table_name)
    except Exception:
        logger.error(f"Chunks table not found: {chunk_table_name}")
        return {"error": f"Chunks table not found: {chunk_table_name}"}

    total = chunk_tbl.count_rows()
    logger.info(f"Reading {total} chunks from {chunk_table_name}...")

    entity_map: dict[str, dict] = {}
    co_occurrences: dict[tuple[str, str], set[str]] = defaultdict(set)

    offset = 0
    page = 2000
    processed = 0

    while True:
        rows = (
            chunk_tbl.search()
            .select(["id", "ner_tags"])
            .limit(page)
            .offset(offset)
            .to_list()
        )
        if not rows:
            break

        for row in rows:
            chunk_id = row["id"]
            raw = row.get("ner_tags") or "[]"
            try:
                tags = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                tags = []

            chunk_keys: list[str] = []
            for tag in tags:
                label = tag.get("label", "")
                text = tag.get("text", "").strip()
                if not text or label in SKIP_LABELS:
                    continue
                key = f"{label}::{_normalize(text)}"

                if key not in entity_map:
                    entity_map[key] = {
                        "id": str(uuid.uuid4()),
                        "label": text,
                        "entity_type": label,
                        "chunk_ids": set(),
                        "confidence_sum": 0.0,
                        "count": 0,
                        "aliases": [],
                    }
                e = entity_map[key]
                e["chunk_ids"].add(chunk_id)
                e["confidence_sum"] += float(tag.get("score", 0.8))
                e["count"] += 1
                normalized_label = _normalize(text)
                if normalized_label != _normalize(e["label"]) and text not in e["aliases"]:
                    e["aliases"].append(text)
                chunk_keys.append(key)

            for i in range(len(chunk_keys)):
                for j in range(i + 1, len(chunk_keys)):
                    pair = tuple(sorted([chunk_keys[i], chunk_keys[j]]))
                    co_occurrences[pair].add(chunk_id)

        offset += page
        processed += len(rows)
        if processed % 10000 < page:
            logger.info(f"  processed {processed}/{total} chunks, {len(entity_map)} entities so far")

        if len(rows) < page:
            break

    logger.info(f"Total entities before pruning: {len(entity_map)}")

    kept = {k: v for k, v in entity_map.items() if len(v["chunk_ids"]) >= min_chunk_freq}
    logger.info(f"Entities after pruning (min_chunk_freq={min_chunk_freq}): {len(kept)}")

    existing_nodes = await list_graph_nodes(collection_id)
    existing_map: dict[tuple[str, str], dict] = {}
    for n in existing_nodes:
        key = (n.get("entity_type", ""), _normalize(n.get("label", "")))
        existing_map[key] = n

    existing_embedding_map: dict[str, list[float]] = {}
    for n in existing_nodes:
        emb = n.get("embedding")
        if emb:
            existing_embedding_map[n["id"]] = emb

    now = int(datetime.utcnow().timestamp() * 1_000_000)
    node_records: list[dict] = []
    added_count = 0
    merged_count = 0

    merge_updates: list[dict] = []

    for key, e in kept.items():
        chunk_ids = list(e["chunk_ids"])
        confidence = min(e["confidence_sum"] / max(e["count"], 1), 1.0)
        entity_key = (e["entity_type"], _normalize(e["label"]))

        if entity_key in existing_map:
            merged_count += 1
            existing = existing_map[entity_key]
            existing_aliases = existing.get("aliases") or []
            new_aliases = [a for a in (e.get("aliases") or []) if a not in existing_aliases]
            merged_aliases = existing_aliases + new_aliases
            existing_confidence = float(existing.get("confidence", 0.0))
            merged_confidence = (existing_confidence + confidence) / 2.0
            merge_updates.append({
                "id": existing["id"],
                "collection_id": collection_id,
                "label": existing.get("label", e["label"]),
                "entity_type": existing.get("entity_type", e["entity_type"]),
                "description": existing.get("description") or "",
                "aliases": merged_aliases,
                "confidence": merged_confidence,
                "source_chunk_ids": list(set((existing.get("source_chunk_ids") or []) + chunk_ids)),
                "topics": existing.get("topics") or [],
                "properties": existing.get("properties") or "{}",
                "created_at": existing.get("created_at", now),
                "updated_at": now,
            })
        else:
            fuzzy_match = None
            if e["entity_type"] in ENTITY_TYPE_SCHEMA:
                for ex_key, ex_node in existing_map.items():
                    if ex_key[0] != e["entity_type"]:
                        continue
                    dist = _levenshtein(_normalize(e["label"]), ex_key[1])
                    if dist >= 3 or dist == 0:
                        continue
                    ex_emb = existing_embedding_map.get(ex_node["id"])
                    if not ex_emb:
                        continue
                    from app.llm.embedder import embed_texts
                    incoming_emb = (await embed_texts([e["label"]]))[0]
                    sim = _cosine_similarity(incoming_emb, ex_emb)
                    if sim > 0.92:
                        fuzzy_match = ex_node
                        break

            if fuzzy_match:
                merged_count += 1
                existing_aliases = fuzzy_match.get("aliases") or []
                new_aliases = [a for a in (e.get("aliases") or []) if a not in existing_aliases]
                merged_aliases = existing_aliases + new_aliases
                existing_confidence = float(fuzzy_match.get("confidence", 0.0))
                merged_confidence = (existing_confidence + confidence) / 2.0
                merge_updates.append({
                    "id": fuzzy_match["id"],
                    "collection_id": collection_id,
                    "label": fuzzy_match.get("label", e["label"]),
                    "entity_type": fuzzy_match.get("entity_type", e["entity_type"]),
                    "description": fuzzy_match.get("description") or "",
                    "aliases": merged_aliases,
                    "confidence": merged_confidence,
                    "source_chunk_ids": list(set((fuzzy_match.get("source_chunk_ids") or []) + chunk_ids)),
                    "topics": fuzzy_match.get("topics") or [],
                    "properties": fuzzy_match.get("properties") or "{}",
                    "created_at": fuzzy_match.get("created_at", now),
                    "updated_at": now,
                })
            else:
                node_records.append({
                    "id": e["id"],
                    "collection_id": collection_id,
                    "label": e["label"],
                    "entity_type": e["entity_type"],
                    "description": "",
                    "aliases": e.get("aliases", []),
                    "confidence": float(confidence),
                    "source_chunk_ids": chunk_ids,
                    "topics": [],
                    "properties": "{}",
                    "created_at": now,
                    "updated_at": now,
                })
                added_count += 1

    logger.info(f"Writing {added_count} new nodes, merging {merged_count} existing nodes...")
    if node_records:
        await upsert_graph_nodes(collection_id, node_records)
    if merge_updates:
        await upsert_graph_nodes(collection_id, merge_updates)

    kept_keys = set(kept.keys())
    edge_records: list[dict] = []
    for (ka, kb), chunk_ids in co_occurrences.items():
        if ka not in kept_keys or kb not in kept_keys:
            continue
        ea = kept[ka]
        eb = kept[kb]
        edge_records.append({
            "id": str(uuid.uuid4()),
            "collection_id": collection_id,
            "source": ea["id"],
            "source_id": ea["id"],
            "target": eb["id"],
            "target_id": eb["id"],
            "relation_type": "co_occurrence",
            "edge_type": "co_occurrence",
            "weight": float(min(len(chunk_ids) / 10.0, 1.0)),
            "context": f"co-occurs in {len(chunk_ids)} chunk(s)",
            "chunk_id": list(chunk_ids)[0],
            "created_at": now,
        })

    logger.info(f"Writing {len(edge_records)} edges...")
    if edge_records:
        await upsert_graph_edges(collection_id, edge_records)

    im = get_index_manager()
    if im and node_records:
        try:
            import json as json_mod
            loop = asyncio.get_running_loop()
            rust_nodes = [
                {
                    "id": n["id"],
                    "node_type": n["entity_type"].upper(),
                    "label": n["label"],
                    "description": n.get("description", ""),
                    "aliases": n.get("aliases", []),
                    "confidence": n.get("confidence", 0.7),
                    "ontology_class": n["entity_type"],
                    "properties": {},
                    "collection_id": collection_id,
                    "display_label": None,
                    "dedup_key": None,
                    "doc_origins": [],
                    "created_at": None,
                    "updated_at": None,
                }
                for n in node_records
            ]
            await loop.run_in_executor(None, lambda: im.upsert_nodes(collection_id, json_mod.dumps(rust_nodes)))
        except Exception as e:
            logger.warning(f"Rust upsert_nodes failed: {e}")

    if im and edge_records:
        try:
            import json as json_mod
            loop = asyncio.get_running_loop()
            rust_edges = [
                {
                    "id": e["id"],
                    "source": e["source"],
                    "target": e["target"],
                    "edge_type": {"custom": e.get("relation_type", "CO_OCCURRENCE")},
                    "weight": e.get("weight", 0.7),
                    "context": e.get("context"),
                    "chunk_id": e.get("chunk_id"),
                    "properties": {},
                    "collection_id": collection_id,
                    "predicate": e.get("relation_type", "co_occurrence"),
                    "doc_origins": [],
                }
                for e in edge_records
            ]
            await loop.run_in_executor(None, lambda: im.upsert_edges(collection_id, json_mod.dumps(rust_edges)))
        except Exception as e:
            logger.warning(f"Rust upsert_edges failed: {e}")

    logger.info(f"Done. Added: {added_count} nodes, {len(edge_records)} edges. Merged: {merged_count}")

    if job_id:
        try:
            from app.pipeline.job_manager import get_job_manager
            jm = get_job_manager()
            await jm.emit(job_id, {
                "type": "graph_update",
                "collection_id": collection_id,
                "added_nodes": added_count,
                "merged_nodes": merged_count,
                "added_edges": len(edge_records),
            })
        except Exception as e:
            logger.warning(f"Failed to emit graph_update event: {e}")

    return {
        "added_nodes": added_count,
        "merged_nodes": merged_count,
        "added_edges": len(edge_records),
        "total_entities": len(kept),
        "total_cooccurrences": len(co_occurrences),
    }
