#!/usr/bin/env python3
"""
Benchmark: extract triplets from the 3 Singapore Acts PDFs using Ollama Cloud.

Usage:
    cd python-api
    python ../scripts/benchmark_extraction.py

Requires:
    - Ollama Cloud API key in OLLAMA_API_KEY below (or set OLLAMA_CLOUD_API_KEY env var)
    - pypdf  (pip install pypdf)
"""

import asyncio
import json
import re
import time
import httpx
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../python-api"))

PDFS = {
    "Accountants Act 2004":   "/Volumes/X9Pro/github/sso-crawler/PDFs/Acts/Accountants Act 2004.pdf",
    "Air Navigation Act 1966": "/Volumes/X9Pro/github/sso-crawler/PDFs/Acts/Air Navigation Act 1966.pdf",
    "Companies Act 1967":     "/Volumes/X9Pro/github/sso-crawler/PDFs/Acts/Companies Act 1967.pdf",
}

OLLAMA_BASE    = os.getenv("OLLAMA_CLOUD_BASE_URL", "https://ollama.com/v1")
OLLAMA_API_KEY = os.getenv("OLLAMA_CLOUD_API_KEY",  "ccdbf541917042eb945c2654c623a2ea.O3LfF6UKd6LrmsVLO7sCetX7")
MODEL          = os.getenv("OLLAMA_CLOUD_MODEL",     "devstral-small-2:24b")
CHUNKS_SAMPLE  = 8          # chunks to process per document (set 0 = all)
CHUNK_CHARS    = 1800       # ~450 tokens of input
CONCURRENCY    = 5          # parallel LLM requests per document

EXTRACTION_PROMPT = """You are a legal knowledge graph extraction system. Extract entities and relationships from this Singapore legislation text.

ALLOWED ENTITY TYPES: Person, Organization, Location, Concept, LegalBody, Role, Provision

Rules:
1. Only use entity types from the allowed list.
2. Relationships must be specific legal predicates (e.g. "GOVERNS", "DEFINES", "ESTABLISHES", "APPOINTS", "GRANTS_POWER_TO").
3. Return ONLY valid JSON — no prose, no markdown fences.
4. Extract at most 8 entities and 8 triplets.
5. Keep descriptions under 60 characters.

TEXT:
{text}

JSON:
{{
  "entities": [{{"name": "str", "type": "str", "description": "str"}}],
  "triplets": [{{"subject": "str", "predicate": "str", "object": "str", "confidence": 0.0}}]
}}"""


# ---------------------------------------------------------------------------
# PDF text extraction
# ---------------------------------------------------------------------------

def extract_pdf_text(path: str) -> str:
    import pypdf
    reader = pypdf.PdfReader(path)
    pages = [p.extract_text() for p in reader.pages]
    return "\n".join(t for t in pages if t)


def chunk_text(text: str, chunk_chars: int = CHUNK_CHARS) -> list[str]:
    """Character-based chunking with 20% overlap."""
    overlap = chunk_chars // 5
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start : min(start + chunk_chars, len(text))])
        start += chunk_chars - overlap
    return chunks


# ---------------------------------------------------------------------------
# Robust JSON repair — handles truncated responses
# ---------------------------------------------------------------------------

def _extract_objects(text: str, key: str) -> list[dict]:
    """Pull all complete {...} objects out of a named JSON array, even if truncated."""
    m = re.search(rf'"{key}"\s*:\s*\[', text)
    if not m:
        return []
    pos = m.end()
    objects, depth, in_str, escape, obj_start = [], 0, False, False, None
    for i in range(pos, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\" and in_str:
            escape = True
            continue
        if c == '"':
            in_str = not in_str
        if in_str:
            continue
        if c == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and obj_start is not None:
                try:
                    objects.append(json.loads(text[obj_start : i + 1]))
                except json.JSONDecodeError:
                    pass
                obj_start = None
        elif c == "]" and depth == 0:
            break
    return objects


def parse_llm_response(content: str) -> dict | None:
    """Try strict parse first; fall back to object-by-object extraction."""
    # Strip markdown fences
    for fence in ("```json", "```"):
        if content.startswith(fence):
            content = content[len(fence):]
            break
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()

    # Find first { ... last }
    brace = content.find("{")
    if brace > 0:
        content = content[brace:]
    rbrace = content.rfind("}")
    if rbrace >= 0:
        content = content[: rbrace + 1]

    # Strict parse
    try:
        parsed = json.loads(content)
        if "entities" in parsed or "triplets" in parsed:
            return parsed
    except json.JSONDecodeError:
        pass

    # Repair: extract complete objects from each array individually
    entities  = _extract_objects(content, "entities")
    triplets  = _extract_objects(content, "triplets")
    if entities or triplets:
        return {"entities": entities, "triplets": triplets, "_repaired": True}
    return None


# ---------------------------------------------------------------------------
# LLM extraction — single chunk with semaphore
# ---------------------------------------------------------------------------

async def extract_chunk(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    idx: int,
    text: str,
    progress_lock: asyncio.Lock,
    total: int,
) -> dict:
    prompt = EXTRACTION_PROMPT.format(text=text[:2000])
    t0 = time.perf_counter()
    async with sem:
        try:
            r = await client.post(
                f"{OLLAMA_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {OLLAMA_API_KEY}"},
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 2000,
                },
                timeout=httpx.Timeout(120.0, connect=10.0),
            )
            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"].strip()
            elapsed = time.perf_counter() - t0

            parsed = parse_llm_response(raw)
            if parsed:
                parsed["_elapsed_s"] = round(elapsed, 2)
                async with progress_lock:
                    ents  = len(parsed.get("entities", []))
                    trips = len(parsed.get("triplets", []))
                    tag   = " [repaired]" if parsed.get("_repaired") else ""
                    print(f"  chunk {idx+1:>3}/{total}  ✓ {ents:>2} entities, {trips:>2} triplets  ({elapsed:.1f}s){tag}")
                return parsed

            async with progress_lock:
                print(f"  chunk {idx+1:>3}/{total}  ⚠ json_decode  ({elapsed:.1f}s)  raw[:80]={repr(raw[:80])}")
            return {"entities": [], "triplets": [], "_elapsed_s": round(elapsed, 2), "_error": "json_decode"}

        except Exception as e:
            elapsed = time.perf_counter() - t0
            async with progress_lock:
                print(f"  chunk {idx+1:>3}/{total}  ✗ {e}  ({elapsed:.1f}s)")
            return {"entities": [], "triplets": [], "_elapsed_s": round(elapsed, 2), "_error": str(e)}


# ---------------------------------------------------------------------------
# Main benchmark
# ---------------------------------------------------------------------------

async def run():
    sample_label = str(CHUNKS_SAMPLE) if CHUNKS_SAMPLE > 0 else "ALL"
    print("=" * 72)
    print("Knowledge Graph Extraction Benchmark")
    print(f"Model: {MODEL}  |  Sample: {sample_label} chunks/doc  |  Concurrency: {CONCURRENCY}")
    print("=" * 72)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            r = await client.get(
                f"{OLLAMA_BASE}/models",
                headers={"Authorization": f"Bearer {OLLAMA_API_KEY}"},
                timeout=10.0,
            )
            r.raise_for_status()
            tags = [m["id"] for m in r.json().get("data", [])]
            found = any(MODEL.split(":")[0] in t for t in tags)
            status = "✓" if found else "⚠ not listed —"
            print(f"  {status}  model '{MODEL}'  |  available: {tags[:4]}")
        except Exception as e:
            print(f"  ✗ Ollama Cloud not reachable: {e}")
            return

    grand_t0 = time.perf_counter()
    all_stats = []

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for doc_name, pdf_path in PDFS.items():
            print(f"\n{'─'*72}")
            print(f"  {doc_name}  ({os.path.getsize(pdf_path)//1024} KB)")
            print(f"{'─'*72}")

            t0 = time.perf_counter()
            try:
                text = extract_pdf_text(pdf_path)
            except Exception as e:
                print(f"  ✗ PDF extraction failed: {e}")
                continue
            if not text:
                print("  ✗ No text extracted — skipping")
                continue
            extract_t = time.perf_counter() - t0

            chunks = chunk_text(text)
            sample = chunks if CHUNKS_SAMPLE == 0 else chunks[:CHUNKS_SAMPLE]
            print(f"  {len(text):,} chars  |  {len(chunks)} total chunks  |  processing {len(sample)}  |  PDF read: {extract_t:.2f}s")

            sem = asyncio.Semaphore(CONCURRENCY)
            lock = asyncio.Lock()
            llm_t0 = time.perf_counter()

            tasks = [
                extract_chunk(client, sem, i, chunk, lock, len(sample))
                for i, chunk in enumerate(sample)
            ]
            results = await asyncio.gather(*tasks)

            llm_elapsed = time.perf_counter() - llm_t0
            all_ents, all_trips = [], []
            errors, repairs = 0, 0
            chunk_times = []
            for res in results:
                chunk_times.append(res.get("_elapsed_s", 0))
                all_ents.extend(res.get("entities", []))
                all_trips.extend(res.get("triplets", []))
                if res.get("_error"):
                    errors += 1
                if res.get("_repaired"):
                    repairs += 1

            avg_t  = sum(chunk_times) / len(chunk_times) if chunk_times else 0
            est_total = avg_t * len(chunks) / CONCURRENCY  # wall-clock with concurrency

            print(f"\n  ── Timing ────────────────────────────────────")
            print(f"  LLM wall time ({len(sample)} chunks @ ×{CONCURRENCY}):  {llm_elapsed:.1f}s")
            print(f"  Avg per-chunk latency:              {avg_t:.1f}s")
            print(f"  Errors / repaired:                  {errors} / {repairs}")
            print(f"  Estimated full pipeline (×{CONCURRENCY}):     {est_total:.0f}s  (~{est_total/60:.1f} min)")

            # Deduplicate entities by name
            seen, uniq = set(), []
            for e in all_ents:
                k = e.get("name", "").lower()
                if k and k not in seen:
                    seen.add(k)
                    uniq.append(e)

            print(f"\n  ── Entities ({min(8, len(uniq))} of {len(uniq)}) ──")
            for e in uniq[:8]:
                print(f"    [{e.get('type','?'):<12}]  {e.get('name','?'):<32}  {e.get('description','')[:55]}")

            print(f"\n  ── Triplets ({min(10, len(all_trips))} of {len(all_trips)}) ──")
            for t in all_trips[:10]:
                conf = t.get("confidence", 0.0)
                print(f"    ({t.get('subject','?')})  ──[{t.get('predicate','?')}]──▶  ({t.get('object','?')})  conf={conf:.2f}")

            all_stats.append({
                "doc":            doc_name,
                "chunks":         len(chunks),
                "sample":         len(sample),
                "errors":         errors,
                "repairs":        repairs,
                "avg_chunk_s":    round(avg_t, 1),
                "est_wall_s":     round(est_total, 0),
                "triplets_total": len(all_trips),
            })

    grand_elapsed = time.perf_counter() - grand_t0

    print(f"\n{'='*72}")
    print("Summary")
    print(f"{'='*72}")
    hdr = f"{'Document':<28} {'Chunks':>6} {'Sample':>7} {'Errors':>7} {'Avg/req':>8} {'Est(×5)':>9} {'Triplets':>9}"
    print(hdr)
    print("─" * len(hdr))
    total_est = 0
    for s in all_stats:
        print(
            f"{s['doc']:<28} {s['chunks']:>6} {s['sample']:>7} {s['errors']:>7}"
            f" {s['avg_chunk_s']:>7.1f}s {s['est_wall_s']:>7.0f}s {s['triplets_total']:>9}"
        )
        total_est += s["est_wall_s"]
    print("─" * len(hdr))
    print(f"Benchmark ran in {grand_elapsed:.1f}s  |  Est. full pipeline (×{CONCURRENCY}): {total_est:.0f}s (~{total_est/60:.0f} min)")
    print()
    print("Full pipeline also includes:")
    print("  • embedding generation     +~0.1s/chunk (batched, GPU)")
    print("  • graph dedup/merge        +~0.05s/chunk")
    print("  • document summarisation   +~5s/doc")


if __name__ == "__main__":
    asyncio.run(run())
