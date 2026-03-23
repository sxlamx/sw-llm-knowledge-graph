"""PDF text extraction using pymupdf as primary, Rust engine as fallback.

Singapore SSO PDFs use Identity-H CID font encoding without ToUnicode tables,
which causes the Rust PDF extractor to produce garbled output. pymupdf (fitz)
handles this encoding correctly.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_GARBAGE_THRESHOLD = 0.5   # If >50% of words look like garbage, treat as failed


def _looks_like_garbage(text: str) -> bool:
    """Return True if the extracted text appears to be garbled (Identity-H encoding issue)."""
    if not text or len(text) < 100:
        return True
    indicators = ["?Identity-H", "Identity-H Unimplemented", "?Unimplemented"]
    for ind in indicators:
        if text.count(ind) > 10:
            return True
    # Rough heuristic: if >40% of chars are '?' it's garbled
    q_ratio = text.count("?") / max(len(text), 1)
    return q_ratio > 0.40


def extract_pdf_pymupdf(file_path: str) -> dict:
    """Extract text from a PDF using pymupdf (handles CID/Identity-H encoding).

    Returns a dict compatible with the Rust engine's extract_text output:
        {
            "raw_text": str,
            "title": str | None,
            "pages": [{"page_num": int, "text": str}],
            "metadata": {},
        }
    """
    import fitz  # pymupdf

    doc = fitz.open(file_path)
    pages = []
    all_text_parts = []

    for page_num, page in enumerate(doc):
        text = page.get_text("text")
        pages.append({"page_number": page_num + 1, "text": text})
        all_text_parts.append(text)

    raw_text = "\n\n".join(all_text_parts)

    # Try to get title from metadata or first page heading
    meta = doc.metadata or {}
    title = meta.get("title") or Path(file_path).stem

    doc.close()

    return {
        "raw_text": raw_text,
        "title": title,
        "pages": pages,
        "metadata": {
            "author": meta.get("author", ""),
            "subject": meta.get("subject", ""),
        },
    }


async def extract_text_smart(
    file_path: str,
    file_type: str,
    engine=None,
) -> dict:
    """Extract text, using pymupdf for PDFs and Rust for everything else.

    For PDFs: tries pymupdf first. Falls back to Rust if pymupdf fails.
    For non-PDFs: delegates to Rust engine.

    Returns the same dict structure as Rust's extract_text.
    """
    import asyncio

    if file_type.lower() in ("pdf",) and Path(file_path).suffix.lower() == ".pdf":
        # Try pymupdf first
        try:
            loop = asyncio.get_event_loop()
            doc_data = await loop.run_in_executor(None, extract_pdf_pymupdf, file_path)
            raw_text = doc_data.get("raw_text", "")

            if _looks_like_garbage(raw_text) and engine is not None:
                logger.warning(
                    f"pymupdf extracted garbled text from {Path(file_path).name} "
                    f"({len(raw_text):,} chars) — falling back to Rust engine"
                )
                # Fall through to Rust below
            else:
                logger.info(
                    f"pymupdf extracted {len(raw_text):,} chars from {Path(file_path).name}"
                )
                return doc_data
        except Exception as e:
            logger.warning(f"pymupdf failed for {file_path}: {e} — falling back to Rust")

    # Rust fallback (or non-PDF files)
    if engine is None:
        raise RuntimeError("No extraction engine available")

    loop = asyncio.get_event_loop()
    extracted_json = await loop.run_in_executor(
        None,
        lambda: engine.extract_text(file_path, file_type),
    )
    return json.loads(extracted_json)
