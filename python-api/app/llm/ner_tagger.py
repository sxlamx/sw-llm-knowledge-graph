"""NER tagger — hybrid spaCy (standard) + LLM (legal) named entity recognition.

spaCy (en_core_web_trf) handles standard entities (PERSON, ORG, GPE, LOC, DATE, MONEY, LAW)
with character-precise offsets.  The LLM extraction pass (already running per chunk) returns
legal-specific spans (LEGISLATION_TITLE, LEGISLATION_REFERENCE, etc.) as text snippets;
this module locates them in the chunk via str.find() to recover character offsets.

All tags are merged and deduplicated by span overlap before being stored as JSON in the
chunk's ner_tags column.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# NER version — increment whenever labels, logic, or spaCy model changes.
# Chunks store this version; --reindex-ner retags any chunk below current.
#   v1 — initial hybrid spaCy + LLM legal NER
#   v2 — re-run after fixing missing spaCy installation in venv
#   v3 — installed en_core_web_trf; reprocess all sm-tagged chunks with transformer model
# ---------------------------------------------------------------------------
NER_VERSION: int = 3

# ---------------------------------------------------------------------------
# Canonical label mapping from spaCy labels
# ---------------------------------------------------------------------------

SPACY_TO_CANONICAL: dict[str, str] = {
    "PERSON": "PERSON",
    "ORG": "ORGANIZATION",
    "GPE": "LOCATION",
    "LOC": "LOCATION",
    "FAC": "LOCATION",
    "DATE": "DATE",
    "TIME": "DATE",
    "MONEY": "MONEY",
    "PERCENT": "PERCENT",
    "LAW": "LAW",
    "NORP": "ORGANIZATION",    # nationalities, religious/political groups
}

# Labels the LLM is asked to produce (legal-specific)
LEGAL_NER_LABELS: list[str] = [
    "LEGISLATION_TITLE",
    "LEGISLATION_REFERENCE",
    "STATUTE_SECTION",
    "COURT_CASE",
    "JURISDICTION",
    "LEGAL_CONCEPT",
    "DEFINED_TERM",
    # Party / role labels (Blackstone/OpenNyAI equivalents, detected by LLM)
    "COURT",         # Judicial bodies: "Court of Appeal", "High Court of Singapore"
    "JUDGE",         # Judicial officers: "Justice Chan Sek Keong", "Lord Bingham"
    "LAWYER",        # Advocates/solicitors appearing in a matter
    "PETITIONER",    # Initiating party: applicant, appellant, claimant
    "RESPONDENT",    # Opposing party: defendant, respondent
    "WITNESS",       # Witnesses mentioned in proceedings
    # Citation patterns — also detected by regex pass (high precision)
    "CASE_CITATION", # Formatted case citations: "[2021] SGCA 1", "(2019) 1 SLR 100"
]

# All known labels (for UI and filtering)
ALL_NER_LABELS: list[str] = list(dict.fromkeys(list(SPACY_TO_CANONICAL.values()) + LEGAL_NER_LABELS))


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class NerTag:
    label: str            # canonical label e.g. "PERSON", "LEGISLATION_TITLE"
    text: str             # extracted text span
    start: int            # char offset in chunk (-1 if not locatable)
    end: int              # char offset in chunk (-1 if not locatable)
    source: str           # "spacy" | "llm"
    confidence: float     # 0.0–1.0


# ---------------------------------------------------------------------------
# Regex-based citation detector
# ---------------------------------------------------------------------------

import re as _re

# Common citation patterns ordered from most to least specific
_CITATION_PATTERNS: list[_re.Pattern] = [
    # Singapore neutral citations: [YYYY] SGCA/SGHC/SGDC/SGMC/SGSMC N
    _re.compile(r'\[\d{4}\]\s+SG(?:CA|HC|DC|MC|SMC)\s+\d+', _re.IGNORECASE),
    # Singapore Law Reports: [YYYY] N SLR(R) N  or  (YYYY) N SLR N
    _re.compile(r'[\[(]\d{4}[\])]\s+\d*\s*SLR(?:\(R\))?\s+\d+', _re.IGNORECASE),
    # UK/Commonwealth: [YYYY] N AC/QB/Ch/WLR/All ER N
    _re.compile(r'\[\d{4}\]\s+\d*\s*(?:AC|QB|Ch|WLR|All\s+ER|EWCA|UKHL|UKSC|EWHC)\s+\d+', _re.IGNORECASE),
    # Neutral citation with any court abbreviation: [YYYY] COURT NN
    _re.compile(r'\[\d{4}\]\s+[A-Z]{2,8}\s+\d+(?:\s*\(\w+\))?'),
    # Bracketed year + volume + series + page: (YYYY) N AC/QB N
    _re.compile(r'\(\d{4}\)\s+\d+\s+[A-Z]{2,6}\s+\d+'),
    # MLJ, CLJ (Malaysian/Commonwealth): [YYYY] N MLJ/CLJ N
    _re.compile(r'[\[(]\d{4}[\])]\s+\d*\s*(?:MLJ|CLJ|MLJU)\s+\d+', _re.IGNORECASE),
]


def _run_regex_citations(text: str) -> list[NerTag]:
    """Detect case citations via regex. Deterministic, zero cost, high precision."""
    seen: set[tuple[int, int]] = set()
    tags: list[NerTag] = []
    for pattern in _CITATION_PATTERNS:
        for m in pattern.finditer(text):
            span = (m.start(), m.end())
            if span in seen:
                continue
            seen.add(span)
            tags.append(NerTag(
                label="CASE_CITATION",
                text=m.group(0).strip(),
                start=m.start(),
                end=m.end(),
                source="regex",
                confidence=0.95,
            ))
    tags.sort(key=lambda t: t.start)
    return tags


# ---------------------------------------------------------------------------
# Lazy spaCy model loader
# ---------------------------------------------------------------------------

_nlp = None
_nlp_lock = asyncio.Lock()
_SPACY_MODEL = "en_core_web_trf"


def _load_spacy_sync() -> object:
    """Load spaCy model synchronously (called via run_in_executor)."""
    import spacy  # noqa: PLC0415 — deferred import
    try:
        return spacy.load(_SPACY_MODEL, disable=["parser", "lemmatizer"])
    except OSError:
        logger.error(
            f"spaCy model '{_SPACY_MODEL}' not found. "
            "Run: python -m spacy download en_core_web_trf"
        )
        return None


async def check_ner_ready() -> None:
    """Raise RuntimeError if en_core_web_trf is not loadable.

    Call this before starting any NER pass so failures are loud and early,
    not silent per-chunk errors.
    """
    nlp = await _get_nlp()
    if nlp is None:
        raise RuntimeError(
            f"spaCy model '{_SPACY_MODEL}' is not installed. "
            "Run: python -m spacy download en_core_web_trf"
        )
    # Quick smoke-test: tag a short string and expect the NER component
    import spacy as _spacy
    if "ner" not in nlp.pipe_names:
        raise RuntimeError(
            f"Loaded spaCy model '{_SPACY_MODEL}' has no NER component (pipe_names={nlp.pipe_names}). "
            "Ensure en_core_web_trf is correctly installed."
        )
    logger.info(f"[ner] spaCy model '{_SPACY_MODEL}' ready (pipes: {nlp.pipe_names})")


async def _get_nlp():
    global _nlp
    if _nlp is not None:
        return _nlp
    async with _nlp_lock:
        if _nlp is not None:
            return _nlp
        loop = asyncio.get_event_loop()
        _nlp = await loop.run_in_executor(None, _load_spacy_sync)
    return _nlp


# ---------------------------------------------------------------------------
# spaCy tagging
# ---------------------------------------------------------------------------

def _run_spacy(nlp, text: str) -> list[NerTag]:
    """Run spaCy NER and return NerTag list (synchronous — call via executor)."""
    if nlp is None:
        return []
    try:
        doc = nlp(text)
        tags = []
        for ent in doc.ents:
            canonical = SPACY_TO_CANONICAL.get(ent.label_)
            if canonical is None:
                continue
            tags.append(NerTag(
                label=canonical,
                text=ent.text,
                start=ent.start_char,
                end=ent.end_char,
                source="spacy",
                confidence=1.0,
            ))
        return tags
    except Exception as e:
        logger.warning(f"spaCy NER failed: {e}")
        return []


# ---------------------------------------------------------------------------
# LLM span offset resolution
# ---------------------------------------------------------------------------

def _find_all_offsets(text: str, span: str) -> list[tuple[int, int]]:
    """Return all (start, end) occurrences of span in text (case-sensitive first, then insensitive)."""
    occurrences: list[tuple[int, int]] = []
    start = 0
    while True:
        idx = text.find(span, start)
        if idx == -1:
            break
        occurrences.append((idx, idx + len(span)))
        start = idx + 1
    if not occurrences:
        # Try case-insensitive
        lower_text = text.lower()
        lower_span = span.lower()
        start = 0
        while True:
            idx = lower_text.find(lower_span, start)
            if idx == -1:
                break
            occurrences.append((idx, idx + len(span)))
            start = idx + 1
    return occurrences


def _resolve_llm_spans(chunk_text: str, llm_ner_spans: list[dict]) -> list[NerTag]:
    """Convert LLM-returned {text, label, confidence} dicts into NerTag with offsets."""
    tags: list[NerTag] = []
    for span in llm_ner_spans:
        label = span.get("label", "")
        text = span.get("text", "").strip()
        confidence = float(span.get("confidence", 0.8))
        if not label or not text or label not in LEGAL_NER_LABELS:
            continue
        offsets = _find_all_offsets(chunk_text, text)
        if offsets:
            for start, end in offsets:
                tags.append(NerTag(
                    label=label,
                    text=text,
                    start=start,
                    end=end,
                    source="llm",
                    confidence=confidence,
                ))
        else:
            # Span identified but not locatable (LLM paraphrased) — keep with sentinel offsets
            tags.append(NerTag(
                label=label,
                text=text,
                start=-1,
                end=-1,
                source="llm",
                confidence=confidence * 0.7,  # lower confidence since not verified
            ))
    return tags


# ---------------------------------------------------------------------------
# Deduplication / merge
# ---------------------------------------------------------------------------

def _overlaps(a: NerTag, b: NerTag) -> bool:
    """Return True if two located spans overlap."""
    if a.start < 0 or b.start < 0:
        return False
    return a.start < b.end and b.start < a.end


def _merge_tags(spacy_tags: list[NerTag], llm_tags: list[NerTag]) -> list[NerTag]:
    """Merge spaCy and LLM tags, preferring spaCy for overlapping standard entities."""
    merged: list[NerTag] = list(spacy_tags)

    for llm_tag in llm_tags:
        # Check for overlap with any already-merged tag
        overlapping = any(_overlaps(llm_tag, existing) for existing in merged)
        if not overlapping:
            merged.append(llm_tag)

    # Sort by start offset (unlocated spans at end)
    merged.sort(key=lambda t: (t.start if t.start >= 0 else float("inf")))
    return merged


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def tag_chunk(
    chunk_text: str,
    llm_ner_spans: Optional[list[dict]] = None,
    use_regex_citations: bool = True,
) -> list[NerTag]:
    """Tag a chunk with NER spans from spaCy + LLM + optional regex citation detector.

    Args:
        chunk_text: raw chunk text (not contextual_text).
        llm_ner_spans: list of {text, label, confidence} dicts from the LLM extraction call.
        use_regex_citations: whether to run the regex citation pass (default True).

    Returns:
        Merged, deduplicated list of NerTag objects.
    """
    nlp = await _get_nlp()

    loop = asyncio.get_event_loop()
    spacy_tags = await loop.run_in_executor(None, _run_spacy, nlp, chunk_text)

    llm_tags = _resolve_llm_spans(chunk_text, llm_ner_spans or [])
    merged = _merge_tags(spacy_tags, llm_tags)

    if use_regex_citations:
        regex_tags = _run_regex_citations(chunk_text)
        merged = _merge_tags(merged, regex_tags)

    return merged


def tags_to_json(tags: list[NerTag]) -> str:
    """Serialise NerTag list to JSON string for storage in LanceDB."""
    return json.dumps([asdict(t) for t in tags])


def json_to_tags(raw: str) -> list[NerTag]:
    """Deserialise JSON string from LanceDB back to NerTag list."""
    if not raw:
        return []
    try:
        return [NerTag(**d) for d in json.loads(raw)]
    except Exception:
        return []
