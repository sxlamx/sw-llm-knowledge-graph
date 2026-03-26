#!/usr/bin/env python3
"""
Standalone NER backfill — tags all untagged/outdated chunks in a collection.

Uses spaCy nlp.pipe() batch processing + MPS/GPU acceleration for speed.

Usage (from python-api directory):
    .venv/bin/python ../scripts/ner_backfill.py [collection_id ...]
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
logger = logging.getLogger("ner_backfill")

# ---------------------------------------------------------------------------
# Batch NER using nlp.pipe()
# ---------------------------------------------------------------------------

SPACY_BATCH_SIZE = 256   # texts per nlp.pipe() call — bigger = more GPU util
DB_WRITE_BATCH   = 1000  # rows per LanceDB merge_insert call


def _load_nlp():
    """Load en_core_web_trf, optionally on MPS/CUDA."""
    import spacy
    try:
        from thinc.api import prefer_gpu
        used_gpu = prefer_gpu(gpu_id=0)
        if used_gpu:
            logger.info("Using GPU (MPS/CUDA) for spaCy inference")
        else:
            logger.info("GPU not available — using CPU")
    except Exception as e:
        logger.warning(f"prefer_gpu failed: {e}")

    try:
        nlp = spacy.load("en_core_web_trf", disable=["parser", "lemmatizer"])
        logger.info(f"Loaded spaCy model: {nlp.meta['name']} {nlp.meta['version']}")
        return nlp
    except OSError:
        logger.error(
            "spaCy model 'en_core_web_trf' not found. "
            "Run: python -m spacy download en_core_web_trf"
        )
        sys.exit(1)


def _spacy_batch_to_tags_json(nlp, texts: list[str]) -> list[str]:
    """Run nlp.pipe() on a batch of texts and return JSON NER tags for each."""
    from app.llm.ner_tagger import (
        SPACY_TO_CANONICAL, NerTag, _run_regex_citations, _merge_tags, tags_to_json
    )
    import json

    results: list[str] = []
    for doc in nlp.pipe(texts, batch_size=SPACY_BATCH_SIZE):
        spacy_tags: list[NerTag] = []
        for ent in doc.ents:
            canonical = SPACY_TO_CANONICAL.get(ent.label_)
            if canonical is None:
                continue
            spacy_tags.append(NerTag(
                label=canonical,
                text=ent.text,
                start=ent.start_char,
                end=ent.end_char,
                source="spacy",
                confidence=1.0,
            ))
        regex_tags = _run_regex_citations(doc.text)
        merged = _merge_tags(spacy_tags, regex_tags)
        results.append(tags_to_json(merged))
    return results


async def _run_ner_backfill(collection_id: str, nlp) -> None:
    from app.db.lancedb_client import get_outdated_ner_chunks, bulk_update_chunk_ner_tags
    from app.llm.ner_tagger import NER_VERSION

    logger.info(f"=== NER backfill for collection {collection_id} ===")
    chunks = await get_outdated_ner_chunks(collection_id, NER_VERSION)
    if not chunks:
        logger.info(f"  All chunks already at NER v{NER_VERSION} — nothing to do.")
        return

    total = len(chunks)
    logger.info(f"  {total} chunks need NER (v{NER_VERSION})")

    tagged = 0
    errors = 0

    # Process in SPACY_BATCH_SIZE groups — nlp.pipe must run on the main thread (MPS)
    for batch_start in range(0, total, SPACY_BATCH_SIZE):
        batch = chunks[batch_start: batch_start + SPACY_BATCH_SIZE]
        texts = [c.get("text", "") for c in batch]
        ids   = [c.get("id", "")   for c in batch]

        try:
            tags_jsons: list[str] = _spacy_batch_to_tags_json(nlp, texts)
        except Exception as e:
            logger.warning(f"  batch {batch_start}-{batch_start+len(batch)} failed: {e}")
            errors += len(batch)
            continue

        updates = [
            {"id": cid, "ner_tags": tj, "ner_version": NER_VERSION}
            for cid, tj in zip(ids, tags_jsons)
            if cid
        ]

        # Accumulate and flush in DB_WRITE_BATCH chunks to keep write pressure low
        written = await bulk_update_chunk_ner_tags(collection_id, updates)
        tagged += written

        if tagged % 5000 < SPACY_BATCH_SIZE or batch_start + SPACY_BATCH_SIZE >= total:
            pct = tagged / total * 100
            logger.info(f"  [{pct:.1f}%] {tagged}/{total} tagged, {errors} errors")

    logger.info(f"  Done: {tagged} tagged, {errors} errors for {collection_id}")


async def list_collections() -> list[str]:
    from app.db.lancedb_client import get_lancedb
    db = await get_lancedb()
    try:
        rows = db.open_table("collections").to_pandas()
        return rows["id"].tolist()
    except Exception:
        return []


async def main() -> None:
    nlp = _load_nlp()

    if len(sys.argv) > 1:
        collection_ids = sys.argv[1:]
    else:
        collection_ids = await list_collections()
        if not collection_ids:
            logger.error("No collections found in database.")
            sys.exit(1)
        logger.info(f"Found {len(collection_ids)} collection(s): {collection_ids}")

    for cid in collection_ids:
        await _run_ner_backfill(cid, nlp)


if __name__ == "__main__":
    asyncio.run(main())
