"""Build graph nodes + edges from NER tags already on chunks.

Runs entirely offline — no LLM calls. Reads ner_tags from every chunk,
merges entities by (entity_type, normalised_label), and creates co-occurrence
edges between entities that appear in the same chunk.

Usage:
    python scripts/build_graph_from_ner.py [--collection-id ID] [--batch 500] [--min-chunks 2]
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python-api"))

import lancedb

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

LANCEDB_PATH = os.environ.get(
    "LANCEDB_PATH",
    os.path.join(os.path.dirname(__file__), "..", ".data", "lancedb"),
)

# Entity types to skip (too noisy for graph)
_SKIP_LABELS = {"CARDINAL", "ORDINAL", "QUANTITY", "PERCENT", "MONEY", "TIME", "DATE"}

# Min chunk appearances for a node to be kept (prune singletons)
DEFAULT_MIN_CHUNKS = 2

# Batch size for writing nodes/edges
DEFAULT_BATCH = 500


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


async def build_graph(collection_id: str, min_chunks: int, batch_size: int) -> None:
    db = lancedb.connect(LANCEDB_PATH)

    table_name = f"{collection_id}_chunks"
    try:
        tbl = db.open_table(table_name)
    except Exception:
        logger.error(f"Chunks table not found: {table_name}")
        return

    total = tbl.count_rows()
    logger.info(f"Reading {total} chunks from {table_name}...")

    # entity_key → {id, label, entity_type, chunk_ids, confidence_sum, count}
    entities: dict[str, dict] = {}
    # (entity_key_a, entity_key_b) sorted → {chunk_ids}
    co_occurrences: dict[tuple[str, str], set[str]] = defaultdict(set)

    offset = 0
    page = 2000
    processed = 0

    while True:
        rows = (
            tbl.search()
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
                if not text or label in _SKIP_LABELS:
                    continue
                key = f"{label}::{_normalise(text)}"

                if key not in entities:
                    entities[key] = {
                        "id": str(uuid.uuid4()),
                        "label": text,
                        "entity_type": label,
                        "chunk_ids": set(),
                        "confidence_sum": 0.0,
                        "count": 0,
                    }
                e = entities[key]
                e["chunk_ids"].add(chunk_id)
                e["confidence_sum"] += float(tag.get("score", 0.8))
                e["count"] += 1
                chunk_keys.append(key)

            # Co-occurrence edges: all pairs in this chunk
            for i in range(len(chunk_keys)):
                for j in range(i + 1, len(chunk_keys)):
                    pair = tuple(sorted([chunk_keys[i], chunk_keys[j]]))
                    co_occurrences[pair].add(chunk_id)

        offset += page
        processed += len(rows)
        if processed % 10000 < page:
            logger.info(f"  processed {processed}/{total} chunks, {len(entities)} entities so far")

        if len(rows) < page:
            break

    logger.info(f"Total entities before pruning: {len(entities)}")

    # Prune entities that appear in fewer than min_chunks chunks
    kept = {k: v for k, v in entities.items() if len(v["chunk_ids"]) >= min_chunks}
    logger.info(f"Entities after pruning (min_chunks={min_chunks}): {len(kept)}")

    # --- Write nodes ---
    now = int(datetime.utcnow().timestamp() * 1_000_000)
    nodes_table_name = f"{collection_id}_nodes"
    edges_table_name = f"{collection_id}_edges"

    node_records = []
    for key, e in kept.items():
        chunk_ids = list(e["chunk_ids"])
        confidence = min(e["confidence_sum"] / max(e["count"], 1), 1.0)
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

    logger.info(f"Writing {len(node_records)} nodes in batches of {batch_size}...")
    _write_table(db, nodes_table_name, node_records, batch_size)

    # --- Build edges from co-occurrences ---
    kept_keys = set(kept.keys())
    edge_records = []
    for (ka, kb), chunk_ids in co_occurrences.items():
        if ka not in kept_keys or kb not in kept_keys:
            continue
        if len(chunk_ids) < 1:
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
            "relation_type": "CO_OCCURS",
            "edge_type": "CO_OCCURS",
            "weight": float(min(len(chunk_ids) / 10.0, 1.0)),
            "context": f"co-occurs in {len(chunk_ids)} chunk(s)",
            "chunk_id": list(chunk_ids)[0],
            "created_at": now,
        })

    logger.info(f"Writing {len(edge_records)} edges in batches of {batch_size}...")
    _write_table(db, edges_table_name, edge_records, batch_size)

    logger.info(f"Done. Nodes: {len(node_records)}, Edges: {len(edge_records)}")


def _write_table(db, table_name: str, records: list[dict], batch_size: int) -> None:
    if not records:
        return
    # Drop existing table to rebuild cleanly
    try:
        db.drop_table(table_name)
    except Exception:
        pass

    for i in range(0, len(records), batch_size):
        batch = records[i: i + batch_size]
        try:
            tbl = db.open_table(table_name)
            tbl.add(batch)
        except Exception:
            db.create_table(table_name, data=batch)
        if i % (batch_size * 10) == 0 and i > 0:
            logger.info(f"  {table_name}: {i}/{len(records)} written")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection-id", default="3be71e2f-26ff-472e-9678-9327b5afa4fe")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--min-chunks", type=int, default=DEFAULT_MIN_CHUNKS)
    args = parser.parse_args()

    await build_graph(args.collection_id, args.min_chunks, args.batch)


if __name__ == "__main__":
    asyncio.run(main())
