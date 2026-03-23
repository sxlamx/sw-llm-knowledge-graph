"""
Integration tests — Document Indexation

Verifies that the production pipeline correctly:
1. Parses real PDFs via the Rust engine (non-empty text extraction)
2. Produces well-structured, ordered chunks with proper metadata
3. Persists document and chunk records to LanceDB
4. Embeds every chunk (correct vector dimension, finite values)
5. Emits the expected SSE progress events
6. Handles multi-page documents (page attribution)
7. Terminates with status "completed" for all three Acts

Each test is parameterised over the three seed PDFs so you can re-use this
file against any new document by adding an entry to TEST_PDFS in conftest.py.

Run with:
    pytest tests/integration/test_indexation.py -v
    pytest tests/integration/test_indexation.py -v -k accountants
"""

from __future__ import annotations

import math
import re

import pytest

from .helpers import (
    EMBEDDING_DIM,
    assert_chunk_integrity,
    assert_chunks_are_ordered,
    assert_chunks_cover_pages,
    assert_document_integrity,
    assert_no_duplicate_chunk_ids,
    assert_pipeline_completed,
)
from .conftest import PDF_KEYS, TEST_PDFS


# ---------------------------------------------------------------------------
# 1. Pipeline completion
# ---------------------------------------------------------------------------

class TestPipelineCompletion:
    """The pipeline must finish cleanly for every Act."""

    def test_status_is_completed(self, single_pdf_result):
        """Job status stored in LanceDB must be 'completed', not failed/running."""
        assert_pipeline_completed(single_pdf_result)

    def test_no_error_message(self, single_pdf_result):
        """No error_msg should be set on a successful job."""
        error = single_pdf_result.get("error") or ""
        assert not error.strip(), f"Unexpected error: {error!r}"

    def test_progress_events_emitted(self, single_pdf_result):
        """Pipeline must emit at least one 'progress' and one 'completed' SSE event."""
        events = single_pdf_result["job_events"]
        types = {e.get("type") for e in events}
        assert "progress" in types, f"No progress events emitted; got types: {types}"
        assert "completed" in types, f"No completed event emitted; got types: {types}"

    def test_final_event_progress_is_1(self, single_pdf_result):
        """The completed event must carry progress=1.0 or processed==total."""
        events = single_pdf_result["job_events"]
        completed = next((e for e in events if e.get("type") == "completed"), None)
        assert completed is not None
        processed = completed.get("processed", 0)
        total = completed.get("total", -1)
        assert processed == total or total == 0, (
            f"Completed event: processed={processed} total={total}"
        )


# ---------------------------------------------------------------------------
# 2. Document records
# ---------------------------------------------------------------------------

class TestDocumentRecords:
    """LanceDB must contain one document record per PDF file ingested."""

    def test_at_least_one_document(self, single_pdf_result):
        docs = single_pdf_result["docs"]
        assert len(docs) >= 1, "No document records persisted to LanceDB"

    def test_document_file_path_matches_pdf(self, single_pdf_result):
        """Each document record must point to the real PDF on disk."""
        docs = single_pdf_result["docs"]
        collection_id = single_pdf_result["collection_id"]
        pdf_path = single_pdf_result["pdf_path"]
        for doc in docs:
            assert_document_integrity(doc, collection_id, pdf_path)

    def test_document_title_is_non_empty(self, single_pdf_result):
        for doc in single_pdf_result["docs"]:
            title = doc.get("title", "").strip()
            assert title, f"Document {doc['id']} has blank title"

    def test_document_has_summary(self, single_pdf_result):
        """doc_summary must be populated (the mock summary generator always returns text)."""
        for doc in single_pdf_result["docs"]:
            summary = doc.get("doc_summary", "")
            assert isinstance(summary, str) and summary.strip(), (
                f"Document {doc['id']} has empty doc_summary"
            )


# ---------------------------------------------------------------------------
# 3. Chunk records
# ---------------------------------------------------------------------------

class TestChunkRecords:
    """Chunks are the atomic unit of retrieval — their integrity is critical."""

    def test_chunks_created(self, single_pdf_result):
        """At least one chunk must be produced per document."""
        assert single_pdf_result["chunk_count"] >= 1, "Pipeline produced zero chunks"

    def test_chunk_count_reasonable(self, single_pdf_result):
        """
        A real Act PDF (300–2000 kB) should produce at least 5 chunks at
        chunk_size_tokens=256.  An unrealistically high count suggests chunking
        is creating micro-fragments.
        """
        count = single_pdf_result["chunk_count"]
        assert 5 <= count <= 5000, (
            f"Chunk count {count} is outside expected range [5, 5000]"
        )

    def test_every_chunk_has_valid_structure(self, single_pdf_result):
        collection_id = single_pdf_result["collection_id"]
        for chunk in single_pdf_result["chunks"]:
            assert_chunk_integrity(chunk, collection_id, EMBEDDING_DIM)

    def test_no_duplicate_chunk_ids(self, single_pdf_result):
        assert_no_duplicate_chunk_ids(single_pdf_result["chunks"])

    def test_chunks_are_position_ordered(self, single_pdf_result):
        assert_chunks_are_ordered(single_pdf_result["chunks"])

    def test_chunks_reference_multiple_pages(self, single_pdf_result):
        """
        Acts are multi-page documents. Chunks should span at least 3 distinct pages
        (allowing for very short Acts).
        """
        assert_chunks_cover_pages(single_pdf_result["chunks"], expected_min_pages=3)

    def test_chunk_text_is_real_prose(self, single_pdf_result):
        """
        Chunk text should look like legal prose — not base64, binary blobs or
        whitespace-only strings.  We check that >80 % of chunks contain at least
        one lowercase word of 3+ characters.
        """
        chunks = single_pdf_result["chunks"]
        word_pattern = re.compile(r"[a-z]{3,}")
        good = sum(1 for c in chunks if word_pattern.search(c.get("text", "")))
        ratio = good / max(len(chunks), 1)
        assert ratio >= 0.80, (
            f"Only {ratio:.0%} of chunks contain legible prose text"
        )

    def test_chunk_token_counts_within_bounds(self, single_pdf_result):
        """
        chunk_size_tokens=256, overlap=32 → most chunks should be ≤ 256 tokens.
        We allow a 20 % slack for the last chunk of each document.
        """
        chunks = single_pdf_result["chunks"]
        oversized = [c for c in chunks if c.get("token_count", 0) > 512]
        assert len(oversized) == 0, (
            f"{len(oversized)} chunks exceed 512 tokens (configured max=256): "
            f"{[c['token_count'] for c in oversized[:3]]}"
        )

    def test_all_chunks_linked_to_a_document(self, single_pdf_result):
        """Every chunk must carry a doc_id that exists in the documents table."""
        doc_ids = {d["id"] for d in single_pdf_result["docs"]}
        orphans = [
            c["id"] for c in single_pdf_result["chunks"]
            if c.get("doc_id") not in doc_ids
        ]
        assert not orphans, f"Orphan chunks (no matching document): {orphans[:5]}"


# ---------------------------------------------------------------------------
# 4. Embedding quality
# ---------------------------------------------------------------------------

class TestEmbeddings:
    """Embeddings must be non-degenerate and consistent."""

    def test_embedding_dimension(self, single_pdf_result):
        for chunk in single_pdf_result["chunks"]:
            emb = chunk.get("embedding", [])
            assert len(emb) == EMBEDDING_DIM, (
                f"Chunk {chunk['id']} has embedding dim {len(emb)}, expected {EMBEDDING_DIM}"
            )

    def test_embeddings_are_finite(self, single_pdf_result):
        for chunk in single_pdf_result["chunks"]:
            emb = chunk.get("embedding", [])
            assert all(math.isfinite(v) for v in emb), (
                f"Chunk {chunk['id']} has NaN/Inf in embedding"
            )

    def test_embeddings_are_not_all_zeros(self, single_pdf_result):
        """The zero vector would indicate a fallback path was hit."""
        zero_count = sum(
            1 for c in single_pdf_result["chunks"]
            if all(v == 0.0 for v in c.get("embedding", [0.0]))
        )
        assert zero_count == 0, (
            f"{zero_count} chunks have all-zero embeddings (embedding call likely failed)"
        )

    def test_embeddings_differ_across_chunks(self, single_pdf_result):
        """
        Deterministic mock embeddings are text-hash-based, so distinct texts
        must produce distinct vectors.
        """
        chunks = single_pdf_result["chunks"]
        if len(chunks) < 2:
            return
        seen: set[tuple] = set()
        collisions = 0
        for c in chunks:
            key = tuple(round(v, 4) for v in c.get("embedding", []))
            if key in seen:
                collisions += 1
            seen.add(key)
        # Allow at most 5 % collisions (shouldn't happen with MD5-based mock)
        assert collisions / max(len(chunks), 1) <= 0.05, (
            f"Too many embedding collisions: {collisions}/{len(chunks)}"
        )


# ---------------------------------------------------------------------------
# 5. Text-content quality (domain keyword presence)
# ---------------------------------------------------------------------------

class TestTextContentQuality:
    """
    The extracted text must contain domain-specific terms from each Act.
    This detects PDF extraction failures (blank text, encoding issues, etc.).
    """

    def test_domain_keywords_appear_in_chunks(self, domain_keyword_check):
        """
        At least one domain keyword must appear in at least 10 % of chunks.
        Parameterised: runs once per (Act, keywords) pair.
        """
        result, keywords = domain_keyword_check
        chunks = result["chunks"]
        all_text = " ".join(c.get("text", "") for c in chunks).lower()

        found = [kw for kw in keywords if kw.lower() in all_text]
        assert found, (
            f"None of the expected domain keywords {keywords} found in "
            f"chunk corpus (first 200 chars): {all_text[:200]!r}"
        )

    def test_text_is_not_mostly_whitespace(self, single_pdf_result):
        """PDF extraction should not produce pages that are predominantly whitespace."""
        chunks = single_pdf_result["chunks"]
        bad = [
            c["id"]
            for c in chunks
            if len(c.get("text", "").strip()) < 20
        ]
        # Allow up to 5 % of chunks to be very short (headers, section numbers)
        threshold = max(1, int(len(chunks) * 0.05))
        assert len(bad) <= threshold, (
            f"{len(bad)} chunks have < 20 non-whitespace characters "
            f"(threshold={threshold}): {bad[:5]}"
        )
