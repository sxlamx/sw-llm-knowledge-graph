"""Multimodal embedding service — extract images from PDFs, generate captions via GPT-4o.

Strategy
--------
1. For each PDF page, render a JPEG thumbnail via `pdfium2` (ships libpdfium as a wheel).
   Fall back to `poppler` (`pdftoppm`) if pdfium2 is not installed.
2. Each page image is sent to the configured vision model with the prompt:
   "Describe the main content of this image concisely (2-3 sentences)."
3. The caption is embedded using the standard text embedder and stored as a separate
   chunk record with `has_image=True` and the base64-encoded thumbnail.
4. These image chunks participate in hybrid search identically to text chunks.
"""

import asyncio
import base64
import io
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from app.config import get_settings
from app.llm.embedder import embed_texts

settings = get_settings()
logger = logging.getLogger(__name__)

# Maximum pixels per dimension before JPEG is downsampled
_MAX_DIM = 1024


# ---------------------------------------------------------------------------
# Image extraction
# ---------------------------------------------------------------------------

def _extract_pages_pdfium(pdf_path: str, max_pages: int, dpi: int) -> list[bytes]:
    """Render PDF pages to JPEG bytes using pdfium2."""
    import pdfium2 as pdfium   # type: ignore

    doc = pdfium.PdfDocument(pdf_path)
    pages: list[bytes] = []
    for i in range(min(len(doc), max_pages)):
        page = doc[i]
        scale = dpi / 72.0
        bitmap = page.render(scale=scale, rotation=0)
        pil_img = bitmap.to_pil()
        # Downscale if larger than _MAX_DIM in either dimension
        w, h = pil_img.size
        if max(w, h) > _MAX_DIM:
            ratio = _MAX_DIM / max(w, h)
            pil_img = pil_img.resize((int(w * ratio), int(h * ratio)))
        buf = io.BytesIO()
        pil_img.convert("RGB").save(buf, format="JPEG", quality=75)
        pages.append(buf.getvalue())
    return pages


def _extract_pages_poppler(pdf_path: str, max_pages: int, dpi: int) -> list[bytes]:
    """Render PDF pages using pdftoppm (poppler) as subprocess fallback."""
    import subprocess
    from PIL import Image   # type: ignore

    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = [
            "pdftoppm",
            "-jpeg",
            "-r", str(dpi),
            "-l", str(max_pages),
            pdf_path,
            os.path.join(tmpdir, "page"),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"pdftoppm failed: {result.stderr.decode()}")

        pages: list[bytes] = []
        for fname in sorted(Path(tmpdir).glob("*.jpg")):
            img = Image.open(str(fname))
            w, h = img.size
            if max(w, h) > _MAX_DIM:
                ratio = _MAX_DIM / max(w, h)
                img = img.resize((int(w * ratio), int(h * ratio)))
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=75)
            pages.append(buf.getvalue())
        return pages


def extract_page_images(pdf_path: str) -> list[bytes]:
    """Return JPEG bytes for each rendered page (up to `vision_max_pages`)."""
    if not pdf_path.lower().endswith(".pdf"):
        return []

    max_pages = settings.vision_max_pages
    dpi = settings.vision_image_dpi

    try:
        return _extract_pages_pdfium(pdf_path, max_pages, dpi)
    except ImportError:
        logger.debug("pdfium2 not available, falling back to poppler")
    except Exception as exc:
        logger.warning(f"pdfium2 extraction failed ({exc}), falling back to poppler")

    try:
        return _extract_pages_poppler(pdf_path, max_pages, dpi)
    except Exception as exc:
        logger.warning(f"poppler extraction also failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# Vision captioning
# ---------------------------------------------------------------------------

async def _caption_image(jpeg_bytes: bytes, page_num: int) -> str:
    """Send a JPEG image to the vision model and return a short caption."""
    from app.llm.extractor import _llm_client

    b64 = base64.b64encode(jpeg_bytes).decode()
    data_uri = f"data:image/jpeg;base64,{b64}"

    prompt = (
        "Describe the main content of this document page image in 2-3 concise sentences. "
        "Focus on text content, diagrams, charts, or tables present. "
        "Do not describe visual styling."
    )

    client = _llm_client()
    resp = await client.chat.completions.create(
        model=settings.vision_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri, "detail": "low"}},
                ],
            }
        ],
        max_tokens=256,
        temperature=0.0,
    )
    return (resp.choices[0].message.content or "").strip()


# ---------------------------------------------------------------------------
# High-level pipeline step
# ---------------------------------------------------------------------------

async def extract_image_chunks(
    pdf_path: str,
    doc_id: str,
    collection_id: str,
    doc_title: str = "",
    topics: Optional[list[str]] = None,
    concurrency: int = 4,
) -> list[dict]:
    """Extract images from a PDF and return image chunk dicts ready for LanceDB.

    Each returned dict matches the `{cid}_chunks` table schema with the extra
    fields `has_image=True` and `image_b64` containing the JPEG thumbnail.
    """
    if not settings.vision_enabled:
        return []

    page_images = extract_page_images(pdf_path)
    if not page_images:
        return []

    sem = asyncio.Semaphore(concurrency)

    async def _process_page(idx: int, jpeg: bytes) -> Optional[dict]:
        async with sem:
            try:
                caption = await _caption_image(jpeg, idx + 1)
            except Exception as exc:
                logger.warning(f"Caption failed for page {idx + 1} of {doc_id}: {exc}")
                return None

            if not caption:
                return None

            embedding = await embed_texts([caption])
            emb = embedding[0] if embedding else [0.0] * settings.embedding_dimension

            import uuid as _uuid
            chunk_id = str(_uuid.uuid4())
            return {
                "id": chunk_id,
                "doc_id": doc_id,
                "collection_id": collection_id,
                "chunk_index": idx,
                "text": caption,
                "contextual_text": f"[Image caption — page {idx + 1} of '{doc_title}']\n{caption}",
                "embedding": emb,
                "topics": topics or [],
                "has_image": True,
                "image_b64": base64.b64encode(jpeg).decode(),
                "page_number": idx + 1,
                "source_node_ids": [],
                "metadata": json.dumps({
                    "type": "image_caption",
                    "page": idx + 1,
                    "doc_title": doc_title,
                }),
            }

    tasks = [_process_page(i, jpeg) for i, jpeg in enumerate(page_images)]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r is not None]
