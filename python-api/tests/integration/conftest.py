"""
Integration test fixtures — pipeline harness for real PDF ingestion.

Mark all tests in this package as integration tests so they can be
selectively included/excluded:

    pytest tests/integration/          # run only integration tests
    pytest -m integration              # same via marker
    pytest -m "not integration"        # skip integration tests (fast CI)

Design
------
These fixtures mirror the exact call sequence that the production API uses:

  POST /ingest/folder
    → run_ingest_pipeline()
       → Rust engine: scan_folder + extract_text + chunk_text
       → mock LLM: embed_texts + extract_from_chunk
       → LanceDB: upsert docs, chunks, nodes, edges
       → Rust index: insert_chunks + upsert_nodes + upsert_edges

The LLM layer is replaced with deterministic mocks so tests are hermetic and
fast, while the Rust document-processing and LanceDB persistence layers run
for real.  This validates the full data path without requiring live API keys.

Session scope
-------------
`pipeline_results` runs once per test session.  All three Acts are indexed into
separate collections so per-document parametrised tests can be written without
re-running the pipeline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Auto-apply the integration marker to every test collected from this package
def pytest_collection_modifyitems(items):
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


# ---------------------------------------------------------------------------
# Test document registry — these are the seed PDFs for all integration tests
# ---------------------------------------------------------------------------

ACTS_DIR = Path("/Volumes/X9Pro/github/sso-crawler/PDFs/Acts")

TEST_PDFS: dict[str, Path] = {
    "accountants": ACTS_DIR / "Accountants Act 2004.pdf",
    "companies": ACTS_DIR / "Companies Act 1967.pdf",
    "air_navigation": ACTS_DIR / "Air Navigation Act 1966.pdf",
}

# Expected domain concepts per Act — used in quality assertions
ACT_DOMAIN_HINTS: dict[str, list[str]] = {
    "accountants": ["accountant", "audit", "registrar", "practice"],
    "companies": ["company", "director", "share", "memorandum"],
    "air_navigation": ["aircraft", "aerodrome", "navigation", "flight"],
}

EMBEDDING_DIM = 4  # kept small for speed; real dim is 1536/3072


# ---------------------------------------------------------------------------
# Deterministic mock LLM implementations
# ---------------------------------------------------------------------------

def _deterministic_embedding(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """
    Produce a unit-normalised embedding from the MD5 of the text.
    Deterministic: same text always produces the same vector.
    Different texts produce different vectors (with overwhelming probability).
    """
    digest = hashlib.md5(text.encode()).digest()
    # Use digest bytes as raw floats, then normalise
    raw = [(digest[i % len(digest)] - 128) / 128.0 for i in range(dim)]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return [round(v / norm, 6) for v in raw]


def _mock_embed_texts(texts: list[str]) -> list[list[float]]:
    """Drop-in replacement for app.llm.embedder.embed_texts."""
    return [_deterministic_embedding(t) for t in texts]


def _mock_generate_doc_summary(text: str) -> str:
    """Return a short, deterministic summary based on early sentences."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    head = " ".join(sentences[:3])
    return head[:300] if head else "Singapore Act document."


def _mock_generate_contextual_prefix(summary: str, chunk_text: str) -> str:
    """Prepend a brief summary note to the chunk (mirrors production contextual enrichment)."""
    return f"[Context: {summary[:80]}] {chunk_text}"


def _extract_legal_entities(text: str) -> dict:
    """
    Deterministic regex-based entity extractor that mimics LLM output for
    Singapore legislation.  Extracts proper-noun phrases, classifies them by
    role keyword, and builds one RELATED_TO relationship between the first two
    entities found.

    Produces the same JSON schema as app.llm.extractor.extract_from_chunk.
    """
    PERSON_TERMS = {
        "Minister", "Registrar", "Director", "Inspector", "Auditor",
        "Accountant", "Judge", "Magistrate", "Officer", "Controller",
        "Commissioner", "Solicitor", "Liquidator", "Receiver", "Examiner",
    }
    ORG_TERMS = {
        "Authority", "Board", "Committee", "Institute", "Society",
        "Corporation", "Council", "Agency", "Tribunal", "Court",
    }
    LOCATION_TERMS = {"Singapore", "Republic"}

    # Candidate phrases: one or more Title-Case words (allow stop words inside)
    candidates = re.findall(
        r"\b([A-Z][a-z]{1,}(?:\s+(?:of|the|for|and|in|de)\s+[A-Z][a-z]+|\s+[A-Z][a-z]+)*)\b",
        text,
    )

    entities: list[dict] = []
    seen: set[str] = set()

    for phrase in candidates:
        phrase = phrase.strip()
        if phrase in seen or len(phrase) < 4:
            continue
        seen.add(phrase)
        words = set(phrase.split())

        if words & PERSON_TERMS:
            etype = "Person"
        elif words & ORG_TERMS:
            etype = "Organization"
        elif words & LOCATION_TERMS:
            etype = "Location"
        else:
            etype = "Concept"

        entities.append({
            "name": phrase,
            "entity_type": etype,
            "description": f"Legal entity: {phrase}",
            "aliases": [],
            "confidence": 0.85,
        })

        if len(entities) >= 6:
            break

    # CONCEPT patterns specific to each Act domain
    for pattern, label in [
        (r"public practice", "Public Practice"),
        (r"audit(?:ing)? (?:firm|practice|report)", "Audit Practice"),
        (r"financial (?:statement|report)", "Financial Statement"),
        (r"share capital", "Share Capital"),
        (r"memorandum of association", "Memorandum of Association"),
        (r"articles of association", "Articles of Association"),
        (r"\baeronautical\b", "Aeronautical Operations"),
        (r"\bair navigation\b", "Air Navigation"),
    ]:
        if re.search(pattern, text, re.IGNORECASE) and label not in seen:
            seen.add(label)
            entities.append({
                "name": label,
                "entity_type": "Concept",
                "description": f"Legal concept from Singapore legislation",
                "aliases": [],
                "confidence": 0.80,
            })

    # Build one relationship between the first two distinct entities
    relationships: list[dict] = []
    if len(entities) >= 2:
        relationships.append({
            "source": entities[0]["name"],
            "target": entities[1]["name"],
            "predicate": "RELATED_TO",
            "confidence": 0.70,
            "context": text[:120],
        })

    return {"entities": entities, "relationships": relationships}


async def _mock_extract_from_chunk(text: str) -> dict:
    """Async wrapper so it matches the real extractor's signature."""
    return _extract_legal_entities(text)


# ---------------------------------------------------------------------------
# Job manager mock — mimics app.pipeline.job_manager.JobManager
# ---------------------------------------------------------------------------

class _MockJobManager:
    """Minimal in-memory job manager: records events, never cancels."""

    def __init__(self) -> None:
        self._events: dict[str, list[dict]] = {}
        self._cancelled: set[str] = set()

    def emit(self, job_id: str, event: dict) -> None:
        self._events.setdefault(job_id, []).append(event)

    async def is_cancelled(self, job_id: str) -> bool:
        return job_id in self._cancelled

    def cancel(self, job_id: str) -> None:
        self._cancelled.add(job_id)

    def events_for(self, job_id: str) -> list[dict]:
        return self._events.get(job_id, [])


# ---------------------------------------------------------------------------
# Low-level DB helpers
# ---------------------------------------------------------------------------

async def _init_db(lancedb_path: str) -> None:
    """Initialise system tables in the integration test LanceDB instance."""
    import app.db.lancedb_client as db_mod
    db_mod._db = None  # force reconnect to new path
    db_mod.settings.lancedb_path = lancedb_path  # type: ignore[attr-defined]

    from app.db.lancedb_client import init_system_tables
    await init_system_tables()


async def _seed_collection(collection_id: str, name: str, pdf_path: str) -> None:
    """Insert a collection and a pending ingest job record."""
    from app.db.lancedb_client import create_collection, upsert_to_table

    await create_collection({
        "id": collection_id,
        "user_id": "integration-test-user",
        "name": name,
        "description": f"Integration test collection for {name}",
        "folder_path": str(Path(pdf_path).parent),
        "status": "pending",
        "doc_count": 0,
    })

    now_us = int(datetime.utcnow().timestamp() * 1_000_000)
    await upsert_to_table("ingest_jobs", [{
        "id": f"job-{collection_id}",
        "collection_id": collection_id,
        "status": "pending",
        "progress": 0.0,
        "total_docs": 0,
        "processed_docs": 0,
        "error_msg": "",
        "started_at": 0,
        "completed_at": 0,
        "created_at": now_us,
        "options": "{}",
    }])


async def _query_table_all(table_name: str) -> list[dict]:
    """Return all rows from a LanceDB table (returns [] if table doesn't exist)."""
    import app.db.lancedb_client as db_mod
    db = await db_mod.get_lancedb()
    try:
        tbl = db.open_table(table_name)
        return tbl.query().to_list()
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Single-PDF pipeline runner
# ---------------------------------------------------------------------------

async def _run_pipeline_for_pdf(
    pdf_path: str,
    collection_id: str,
    job_manager: _MockJobManager,
) -> dict:
    """
    Run the full production pipeline for a single PDF document.

    Mocked layers
    -------------
    - LLM embedder  (embed_texts)
    - LLM extractor (generate_doc_summary, generate_contextual_prefix,
                     extract_from_chunk)
    - job_manager   (get_job_manager)

    Real layers
    -----------
    - Rust ingestion engine (scan_folder, extract_text, chunk_text)
    - Rust index manager   (insert_chunks, upsert_nodes, upsert_edges)
    - LanceDB persistence  (upsert_document, upsert_to_table, etc.)
    """
    from app.models.schemas import IngestOptions
    from app.pipeline.ingest_worker import run_ingest_pipeline

    options = IngestOptions(
        max_files=5,
        max_depth=1,
        chunk_size_tokens=256,
        chunk_overlap_tokens=32,
        extract_entities=True,
    )

    patches = [
        patch("app.pipeline.ingest_worker.embed_texts",        side_effect=_mock_embed_texts),
        patch("app.pipeline.ingest_worker.generate_doc_summary",
              side_effect=_mock_generate_doc_summary),
        patch("app.pipeline.ingest_worker.generate_contextual_prefix",
              side_effect=_mock_generate_contextual_prefix),
        patch("app.pipeline.ingest_worker.extract_from_chunk",
              side_effect=_mock_extract_from_chunk),
        patch("app.pipeline.ingest_worker.get_job_manager",
              return_value=job_manager),
        # cost tracker — allow unlimited spending in tests
        patch("app.pipeline.ingest_worker.create_tracker",
              return_value=MagicMock(summary=lambda: {"total_usd": 0.0})),
        patch("app.pipeline.ingest_worker.remove_tracker"),
    ]

    with patches[0], patches[1], patches[2], patches[3], \
         patches[4], patches[5], patches[6]:
        await run_ingest_pipeline(
            job_id=f"job-{collection_id}",
            collection_id=collection_id,
            folder_path=str(Path(pdf_path).parent),
            options=options,
        )

    # Collect results from LanceDB
    chunks = await _query_table_all(f"{collection_id}_chunks")
    nodes  = await _query_table_all(f"{collection_id}_nodes")
    edges  = await _query_table_all(f"{collection_id}_edges")
    docs   = await _query_table_all(f"{collection_id}_documents")

    # Determine final job status
    all_jobs = await _query_table_all("ingest_jobs")
    job = next((j for j in all_jobs if j["id"] == f"job-{collection_id}"), {})

    return {
        "collection_id": collection_id,
        "pdf_path": pdf_path,
        "status": job.get("status", "unknown"),
        "error": job.get("error_msg"),
        "chunk_count": len(chunks),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "doc_count": len(docs),
        "chunks": chunks,
        "nodes": nodes,
        "edges": edges,
        "docs": docs,
        "job_events": job_manager.events_for(f"job-{collection_id}"),
    }


# ---------------------------------------------------------------------------
# Session fixture — indexes all three Acts once per test session
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def pipeline_results() -> dict[str, dict]:
    """
    Session-scoped fixture.  Indexes all three Acts using the full production
    pipeline (with mocked LLM) and returns a mapping of:

        pdf_key → {
            "collection_id": str,
            "pdf_path": str,
            "status": str,          # "completed" | "failed"
            "chunk_count": int,
            "node_count": int,
            "edge_count": int,
            "doc_count": int,
            "chunks": list[dict],
            "nodes": list[dict],
            "edges": list[dict],
            "docs": list[dict],
            "job_events": list[dict],
        }

    Skips if:
    - Any test PDF is missing from disk
    - The Rust ingestion engine is unavailable
    """
    # Validate PDF files exist before starting
    missing = [k for k, p in TEST_PDFS.items() if not p.exists()]
    if missing:
        pytest.skip(f"Test PDFs not found: {missing}. Path: {ACTS_DIR}")

    # Check Rust engine availability
    try:
        from app.core.rust_bridge import get_ingestion_engine
        engine = get_ingestion_engine()
        if engine is None:
            pytest.skip("Rust ingestion engine not available (get_ingestion_engine() returned None)")
    except Exception as exc:
        pytest.skip(f"Rust bridge import failed: {exc}")

    return asyncio.run(_build_all_results())


async def _build_all_results() -> dict[str, dict]:
    """Async body of pipeline_results — runs in its own event loop."""
    import app.db.lancedb_client as db_mod
    import app.pipeline.ingest_worker as worker_mod

    with tempfile.TemporaryDirectory(prefix="kg_integration_") as tmp_dir:
        # Wire both modules to the temp LanceDB
        original_db_settings = db_mod.settings
        original_worker_settings = worker_mod.settings

        db_mod.settings = _patch_settings(db_mod.settings, tmp_dir)
        worker_mod.settings = _patch_settings(worker_mod.settings, tmp_dir)
        db_mod._db = None  # force fresh connection

        try:
            await _init_db(tmp_dir)
            jm = _MockJobManager()
            results: dict[str, dict] = {}

            for key, pdf_path in TEST_PDFS.items():
                collection_id = f"test-{key}-{uuid.uuid4().hex[:8]}"
                await _seed_collection(collection_id, key, str(pdf_path))
                results[key] = await _run_pipeline_for_pdf(
                    str(pdf_path), collection_id, jm
                )
        finally:
            db_mod.settings = original_db_settings
            worker_mod.settings = original_worker_settings
            db_mod._db = None

    return results


def _patch_settings(settings_obj: Any, lancedb_path: str) -> Any:
    """
    Return a copy of settings with lancedb_path replaced.
    Works with both frozen and mutable pydantic models.
    """
    try:
        # pydantic v2 — model_copy
        return settings_obj.model_copy(update={"lancedb_path": lancedb_path})
    except AttributeError:
        pass
    try:
        # pydantic v1 — copy
        return settings_obj.copy(update={"lancedb_path": lancedb_path})
    except Exception:
        pass
    # Fallback: direct attribute mutation
    try:
        object.__setattr__(settings_obj, "lancedb_path", lancedb_path)
    except Exception:
        pass
    return settings_obj


# ---------------------------------------------------------------------------
# Per-PDF parametrised convenience fixture
# ---------------------------------------------------------------------------

PDF_KEYS = list(TEST_PDFS.keys())


@pytest.fixture(params=PDF_KEYS)
def single_pdf_result(request, pipeline_results) -> dict:
    """
    Parametrised fixture — yields the pipeline result for one PDF at a time.
    Tests decorated with this fixture run once per Act.
    """
    return pipeline_results[request.param]


# ---------------------------------------------------------------------------
# Domain keywords fixture (for content-quality assertions)
# ---------------------------------------------------------------------------

@pytest.fixture(params=list(ACT_DOMAIN_HINTS.items()), ids=list(ACT_DOMAIN_HINTS.keys()))
def domain_keyword_check(request, pipeline_results):
    """Pairs a pipeline result with the expected domain keyword hints for that Act."""
    key, keywords = request.param
    return pipeline_results[key], keywords
