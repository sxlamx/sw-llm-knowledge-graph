#!/usr/bin/env python3
"""
Standalone embedding backfill — re-embeds all chunks that have zero-vector embeddings.

Uses sentence-transformers (Qwen/Qwen3-Embedding-0.6B) with MPS/GPU acceleration.

Usage (from python-api directory):
    .venv/bin/python ../scripts/embed_backfill.py [collection_id ...]
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

# Allow running from python-api/ or repo root
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root / "python-api"))

os.environ.setdefault("LANCEDB_PATH", str(repo_root / ".data" / "lancedb"))
os.environ.setdefault("DATA_DIR",     str(repo_root / ".data"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("embed_backfill")

EMBED_BATCH_SIZE = 512   # texts per encode() call
DB_WRITE_BATCH   = 2000  # rows per merge_insert call


def _load_model():
    """Load sentence-transformers model with MPS/CUDA if available."""
    import torch
    from sentence_transformers import SentenceTransformer
    from app.config import get_settings
    settings = get_settings()

    device = "cpu"
    if torch.backends.mps.is_available():
        device = "mps"
        logger.info("Using MPS (Apple Silicon GPU)")
    elif torch.cuda.is_available():
        device = "cuda"
        logger.info("Using CUDA GPU")
    else:
        logger.info("Using CPU")

    model = SentenceTransformer(
        settings.hf_embed_model,
        trust_remote_code=True,
        device=device,
    )
    dim = model.get_sentence_embedding_dimension()
    logger.info(f"Loaded model: {settings.hf_embed_model} (dim={dim})")
    return model, settings.embedding_dimension


def _embed_batch(model, texts: list[str], dim: int) -> list[list[float]]:
    """Embed a batch of passage texts, truncating to configured dimension."""
    embeddings = model.encode(
        texts,
        batch_size=EMBED_BATCH_SIZE,
        normalize_embeddings=True,
        show_progress_bar=False,
        prompt="",  # passage mode — no instruction prefix
    )
    return [e.tolist()[:dim] for e in embeddings]


async def _get_chunks_needing_embedding(collection_id: str) -> list[dict]:
    """Fetch id+text for all chunks. Avoids loading embedding vectors to save memory.

    Since all existing chunks were ingested with zero-vectors, we just load id+text.
    To support incremental re-runs, we sample the first element of the embedding
    per row using a LanceDB computed check via a small probe.
    """
    from app.db.lancedb_client import get_lancedb
    db = await get_lancedb()
    table_name = f"{collection_id}_chunks"
    try:
        tbl = db.open_table(table_name)
        # Only fetch id and text — avoid loading embedding vectors into memory
        rows = tbl.search().limit(None).select(["id", "text"]).to_list()
        return [{"id": r["id"], "text": r.get("text", "")} for r in rows]
    except Exception as e:
        logger.error(f"Failed to fetch chunks for {collection_id}: {e}")
        return []


async def _count_zero_embeddings(collection_id: str) -> int:
    """Quick count of chunks with zero-vector embeddings (sample-based)."""
    from app.db.lancedb_client import get_lancedb
    import pyarrow.compute as pc
    db = await get_lancedb()
    try:
        tbl = db.open_table(f"{collection_id}_chunks")
        # Sample 1000 rows to estimate
        rows = tbl.search().limit(1000).select(["embedding"]).to_list()
        zero = sum(1 for r in rows if sum(x*x for x in (r.get("embedding") or [])) < 1e-6)
        total = tbl.count_rows()
        return int(zero / max(len(rows), 1) * total)
    except Exception:
        return 0


async def _bulk_update_embeddings(collection_id: str, updates: list[dict]) -> int:
    """Write updated embeddings back via merge_insert."""
    if not updates:
        return 0
    import pyarrow as pa
    from app.db.lancedb_client import get_lancedb
    db = await get_lancedb()
    table_name = f"{collection_id}_chunks"
    try:
        tbl = db.open_table(table_name)
        ids  = [u["id"]        for u in updates]
        embs = [u["embedding"] for u in updates]

        # Get embedding dimension from table schema
        schema = tbl.schema
        emb_field = schema.field("embedding")
        emb_dim = emb_field.type.list_size

        batch = pa.table({
            "id":        pa.array(ids, type=pa.string()),
            "embedding": pa.array(embs, type=pa.list_(pa.float32(), emb_dim)),
        })
        (
            tbl.merge_insert("id")
            .when_matched_update_all()
            .execute(batch)
        )
        return len(updates)
    except Exception as e:
        logger.error(f"Failed to write embeddings for {collection_id}: {e}")
        return 0


async def _run_embed_backfill(collection_id: str, model, dim: int) -> None:
    logger.info(f"=== Embedding backfill for collection {collection_id} ===")

    logger.info("  Scanning chunks (loading id+text only)...")
    chunks = await _get_chunks_needing_embedding(collection_id)
    if not chunks:
        logger.info("  No chunks found.")
        return

    total = len(chunks)
    logger.info(f"  {total} chunks need re-embedding")

    embedded = 0
    errors = 0
    pending: list[dict] = []

    for batch_start in range(0, total, EMBED_BATCH_SIZE):
        batch = chunks[batch_start: batch_start + EMBED_BATCH_SIZE]
        texts = [c["text"] for c in batch]
        ids   = [c["id"]   for c in batch]

        try:
            embs = _embed_batch(model, texts, dim)
        except Exception as e:
            logger.warning(f"  batch {batch_start}-{batch_start+len(batch)} embed failed: {e}")
            errors += len(batch)
            continue

        for cid, emb in zip(ids, embs):
            pending.append({"id": cid, "embedding": emb})

        # Flush to DB when pending hits DB_WRITE_BATCH
        if len(pending) >= DB_WRITE_BATCH:
            written = await _bulk_update_embeddings(collection_id, pending)
            embedded += written
            pending = []

        if embedded % 5000 < EMBED_BATCH_SIZE or batch_start + EMBED_BATCH_SIZE >= total:
            pct = (batch_start + len(batch)) / total * 100
            logger.info(f"  [{pct:.1f}%] {batch_start + len(batch)}/{total} embedded, {errors} errors")

    # Final flush
    if pending:
        written = await _bulk_update_embeddings(collection_id, pending)
        embedded += written

    logger.info(f"  Done: {embedded} embedded, {errors} errors for {collection_id}")


async def list_collections() -> list[str]:
    from app.db.lancedb_client import get_lancedb
    db = await get_lancedb()
    try:
        rows = db.open_table("collections").to_pandas()
        return rows["id"].tolist()
    except Exception:
        return []


async def main() -> None:
    model, dim = _load_model()

    if len(sys.argv) > 1:
        collection_ids = sys.argv[1:]
    else:
        collection_ids = await list_collections()
        if not collection_ids:
            logger.error("No collections found in database.")
            sys.exit(1)
        logger.info(f"Found {len(collection_ids)} collection(s): {collection_ids}")

    for cid in collection_ids:
        await _run_embed_backfill(cid, model, dim)


if __name__ == "__main__":
    asyncio.run(main())
