"""Standalone entity extraction script.

Reads existing chunks from a collection and runs LLM entity extraction,
writing nodes/edges directly to LanceDB without re-ingesting documents.

Usage:
    cd python-api
    python scripts/extract_entities.py \
        --collection c31b83c3-fa36-4c17-892c-1aa13c0f006a \
        --sample 100 \
        --concurrency 5
"""

import asyncio
import json
import logging
import sys
import uuid
import argparse
from pathlib import Path

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


async def main(collection_id: str, sample: int, concurrency: int) -> None:
    from app.db.lancedb_client import get_lancedb, upsert_graph_node, upsert_graph_edge
    from app.llm.extractor import extract_from_chunk

    db = await get_lancedb()

    # Load chunks
    table_name = f"{collection_id}_chunks"
    try:
        tbl = db.open_table(table_name)
        all_chunks = tbl.search().limit(10000).to_list()
    except Exception as e:
        logger.error(f"Cannot open chunks table {table_name}: {e}")
        return

    logger.info(f"Loaded {len(all_chunks)} chunks from {table_name}")

    # Sample evenly across the dataset
    if sample and sample < len(all_chunks):
        step = max(1, len(all_chunks) // sample)
        chunks = all_chunks[::step][:sample]
        logger.info(f"Sampled {len(chunks)} chunks (every {step}th)")
    else:
        chunks = all_chunks
        logger.info(f"Processing all {len(chunks)} chunks")

    limiter = asyncio.Semaphore(concurrency)
    nodes: list[dict] = []
    edges: list[dict] = []
    ok = 0
    fail = 0

    async def process_chunk(chunk: dict) -> tuple[list, list]:
        async with limiter:
            text = chunk.get("contextual_text") or chunk.get("text", "")
            if not text.strip():
                return [], []
            try:
                result = await extract_from_chunk(text)
            except Exception as e:
                logger.warning(f"Chunk {chunk.get('id', '?')[:8]}: extraction failed — {e}")
                return [], []

            chunk_nodes = []
            name_to_id: dict[str, str] = {}
            for ent in result.get("entities", []):
                nid = str(uuid.uuid4())
                name_to_id[ent.get("name", "")] = nid
                chunk_nodes.append({
                    "id": nid,
                    "collection_id": collection_id,
                    "label": ent.get("name", ""),
                    "entity_type": ent.get("entity_type", "Concept"),
                    "description": ent.get("description", ""),
                    "aliases": ent.get("aliases") or [],
                    "confidence": float(ent.get("confidence", 0.7)),
                    "source_chunk_ids": [chunk.get("id", "")],
                    "topics": result.get("topics") or [],
                    "properties": {},
                })

            chunk_edges = []
            for rel in result.get("relationships", []):
                src_id = name_to_id.get(rel.get("source", ""))
                tgt_id = name_to_id.get(rel.get("target", ""))
                if src_id and tgt_id:
                    chunk_edges.append({
                        "id": str(uuid.uuid4()),
                        "collection_id": collection_id,
                        "source": src_id,
                        "source_id": src_id,
                        "target": tgt_id,
                        "target_id": tgt_id,
                        "relation_type": rel.get("predicate", "RELATED_TO"),
                        "edge_type": rel.get("predicate", "RELATED_TO"),
                        "weight": float(rel.get("confidence", 0.7)),
                        "context": rel.get("context", ""),
                        "chunk_id": chunk.get("id", ""),
                    })

            return chunk_nodes, chunk_edges

    tasks = [asyncio.create_task(process_chunk(c)) for c in chunks]
    total = len(tasks)
    for i, fut in enumerate(asyncio.as_completed(tasks)):
        n, e = await fut
        if n or e:
            nodes.extend(n)
            edges.extend(e)
            ok += 1
        else:
            fail += 1
        if (i + 1) % 10 == 0 or (i + 1) == total:
            logger.info(f"Progress: {i+1}/{total} chunks | nodes={len(nodes)} edges={len(edges)} ok={ok} fail={fail}")

    # Merge duplicate nodes by label
    merged: dict[str, dict] = {}
    for n in nodes:
        key = n["label"].lower().strip()
        if not key:
            continue
        if key in merged:
            existing = merged[key]
            existing["source_chunk_ids"] = list(
                set(existing.get("source_chunk_ids", []) + n.get("source_chunk_ids", []))
            )
            existing["confidence"] = max(existing.get("confidence", 0.0), n.get("confidence", 0.0))
        else:
            merged[key] = dict(n)

    final_nodes = list(merged.values())
    logger.info(f"Merged to {len(final_nodes)} unique nodes, {len(edges)} edges")

    # Write to LanceDB
    written_nodes = 0
    for n in final_nodes:
        try:
            await upsert_graph_node(collection_id, n)
            written_nodes += 1
        except Exception as e:
            logger.warning(f"Failed to write node {n.get('label')}: {e}")

    written_edges = 0
    for e in edges:
        try:
            await upsert_graph_edge(collection_id, e)
            written_edges += 1
        except Exception as e:
            logger.warning(f"Failed to write edge: {e}")

    logger.info(f"Done. Wrote {written_nodes} nodes, {written_edges} edges to collection {collection_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection", required=True, help="Collection ID")
    parser.add_argument("--sample", type=int, default=100,
                        help="Max chunks to sample (0 = all). Default 100.")
    parser.add_argument("--concurrency", type=int, default=5,
                        help="Max concurrent LLM calls. Default 5.")
    args = parser.parse_args()

    asyncio.run(main(args.collection, args.sample, args.concurrency))
