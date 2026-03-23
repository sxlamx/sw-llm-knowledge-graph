"""Backfill NER tags for all existing chunks in the database.

Runs spaCy + LLM legal NER on every chunk that has an empty/missing ner_tags field.
Can also be forced to re-tag all chunks with --force.

Usage:
    python scripts/backfill_ner_tags.py                  # spaCy + LLM
    python scripts/backfill_ner_tags.py --spacy-only     # spaCy only (no LLM cost)
    python scripts/backfill_ner_tags.py --force          # re-tag even existing tags
    python scripts/backfill_ner_tags.py --collection <id>  # single collection only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# Ensure the project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db.lancedb_client import get_lancedb, get_chunks_for_collection, update_chunk_ner_tags
from app.llm.ner_tagger import tag_chunk, tags_to_json

_CONCURRENCY = 5  # parallel chunks (limited to avoid overloading spaCy/LLM)


async def backfill_collection(
    collection_id: str,
    use_llm: bool,
    force: bool,
    use_regex: bool = True,
) -> dict:
    """Tag all (or untagged) chunks in a collection. Returns stats dict."""
    from app.llm.extractor import extract_from_chunk

    # Ensure ner_tags column exists in the table schema before reading/writing
    try:
        db = await get_lancedb()
        tbl = db.open_table(f"{collection_id}_chunks")
        if "ner_tags" not in [f.name for f in tbl.schema]:
            tbl.add_columns({"ner_tags": "CAST('[]' AS STRING)"})
            print(f"  Added 'ner_tags' column to {collection_id}_chunks")
    except Exception as e:
        print(f"  [WARN] Could not ensure ner_tags column: {e}")

    chunks = await get_chunks_for_collection(collection_id)
    total = len(chunks)
    if total == 0:
        return {"collection_id": collection_id, "total": 0, "tagged": 0, "skipped": 0, "errors": 0}

    # Filter to untagged unless --force
    if not force:
        to_process = [
            c for c in chunks
            if not c.get("ner_tags") or c.get("ner_tags") in ("[]", "", None)
        ]
    else:
        to_process = chunks

    skipped = total - len(to_process)
    print(f"  {collection_id}: {total} chunks total, {skipped} already tagged, {len(to_process)} to process")

    if not to_process:
        return {"collection_id": collection_id, "total": total, "tagged": 0, "skipped": skipped, "errors": 0}

    semaphore = asyncio.Semaphore(_CONCURRENCY)
    tagged = 0
    errors = 0
    start = time.time()

    async def _process_one(chunk: dict, idx: int) -> bool:
        nonlocal tagged, errors
        async with semaphore:
            chunk_id = chunk.get("id", "")
            chunk_text = chunk.get("text", "")
            if not chunk_text:
                return False
            try:
                llm_ner_spans: list[dict] = []
                if use_llm:
                    try:
                        result = await extract_from_chunk(chunk_text)
                        llm_ner_spans = result.get("ner_spans", [])
                    except Exception as llm_err:
                        # LLM failure is non-fatal — fall back to spaCy only
                        print(f"    [WARN] LLM failed for chunk {chunk_id[:8]}: {llm_err}")

                tags = await tag_chunk(chunk_text, llm_ner_spans, use_regex_citations=use_regex)
                tags_json = tags_to_json(tags)
                await update_chunk_ner_tags(collection_id, chunk_id, tags_json)
                tagged += 1
                if idx % 50 == 0 or idx == len(to_process) - 1:
                    elapsed = time.time() - start
                    rate = tagged / elapsed if elapsed > 0 else 0
                    eta = (len(to_process) - tagged) / rate if rate > 0 else 0
                    label_count = len(json.loads(tags_json))
                    print(
                        f"    [{idx+1}/{len(to_process)}] chunk {chunk_id[:8]}… "
                        f"{label_count} tags | {rate:.1f} chunks/s | ETA {eta:.0f}s"
                    )
                return True
            except Exception as e:
                errors += 1
                print(f"    [ERROR] chunk {chunk_id[:8]}: {e}")
                return False


    await asyncio.gather(*[_process_one(c, i) for i, c in enumerate(to_process)])

    elapsed = time.time() - start
    return {
        "collection_id": collection_id,
        "total": total,
        "tagged": tagged,
        "skipped": skipped,
        "errors": errors,
        "elapsed_s": round(elapsed, 1),
    }


async def main(args: argparse.Namespace) -> None:
    db = await get_lancedb()

    # Discover collections — list_tables() returns a ListTablesResponse with .tables list
    tables_response = db.list_tables()
    raw_tables = getattr(tables_response, "tables", None) or list(tables_response)
    chunk_tables = [t for t in raw_tables if isinstance(t, str) and t.endswith("_chunks")]
    collection_ids = [t.replace("_chunks", "") for t in chunk_tables]

    if args.collection:
        if args.collection not in collection_ids:
            print(f"Collection '{args.collection}' not found. Available: {collection_ids}")
            sys.exit(1)
        collection_ids = [args.collection]

    mode = "spaCy only" if args.spacy_only else "spaCy + LLM"
    regex_mode = "off" if args.no_regex else "on"
    print(f"\nNER backfill — mode: {mode} | regex citations: {regex_mode} | force: {args.force}")
    print(f"Collections to process: {collection_ids}\n")

    all_stats = []
    for cid in collection_ids:
        print(f"Processing collection: {cid}")
        stats = await backfill_collection(cid, use_llm=not args.spacy_only, force=args.force, use_regex=not args.no_regex)
        all_stats.append(stats)
        print(f"  Done: {stats['tagged']} tagged, {stats['skipped']} skipped, {stats['errors']} errors in {stats.get('elapsed_s', '?')}s\n")

    print("=" * 60)
    print("Summary:")
    total_tagged = sum(s["tagged"] for s in all_stats)
    total_errors = sum(s["errors"] for s in all_stats)
    for s in all_stats:
        print(f"  {s['collection_id'][:20]}… : {s['tagged']} tagged, {s['skipped']} skipped, {s['errors']} errors")
    print(f"\nTotal tagged: {total_tagged} | Total errors: {total_errors}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill NER tags for existing chunks")
    parser.add_argument("--spacy-only", action="store_true", help="Skip LLM call — spaCy NER only")
    parser.add_argument("--no-regex", action="store_true", help="Skip regex citation detector pass")
    parser.add_argument("--force", action="store_true", help="Re-tag even chunks that already have tags")
    parser.add_argument("--collection", metavar="ID", help="Process only this collection ID")
    asyncio.run(main(parser.parse_args()))
