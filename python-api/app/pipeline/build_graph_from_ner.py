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


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


async def build_graph_from_ner(collection_id: str, min_chunk_freq: int = 2) -> dict:
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
                    }
                e = entity_map[key]
                e["chunk_ids"].add(chunk_id)
                e["confidence_sum"] += float(tag.get("score", 0.8))
                e["count"] += 1
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

    now = int(datetime.utcnow().timestamp() * 1_000_000)
    node_records: list[dict] = []
    added_count = 0
    merged_count = 0

    for key, e in kept.items():
        chunk_ids = list(e["chunk_ids"])
        confidence = min(e["confidence_sum"] / max(e["count"], 1), 1.0)
        entity_key = (e["entity_type"], _normalize(e["label"]))

        if entity_key in existing_map:
            merged_count += 1
        else:
            node_records.append({
                "id": e["id"],
                "collection_id": collection_id,
                "label": e["label"],
                "entity_type": e["entity_type"],
                "description": "",
                "aliases": [],
                "confidence": float(confidence),
                "source_chunk_ids": chunk_ids,
                "topics": [],
                "properties": "{}",
                "created_at": now,
                "updated_at": now,
            })
            added_count += 1

    logger.info(f"Writing {added_count} new nodes, {merged_count} already exist...")
    if node_records:
        await upsert_graph_nodes(collection_id, node_records)

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
            loop = asyncio.get_event_loop()
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
            loop = asyncio.get_event_loop()
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
                }
                for e in edge_records
            ]
            await loop.run_in_executor(None, lambda: im.upsert_edges(collection_id, json_mod.dumps(rust_edges)))
        except Exception as e:
            logger.warning(f"Rust upsert_edges failed: {e}")

    logger.info(f"Done. Added: {added_count} nodes, {len(edge_records)} edges. Merged: {merged_count}")

    return {
        "added_nodes": added_count,
        "merged_nodes": merged_count,
        "added_edges": len(edge_records),
        "total_entities": len(kept),
        "total_cooccurrences": len(co_occurrences),
    }
