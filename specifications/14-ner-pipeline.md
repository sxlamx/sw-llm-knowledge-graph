# 14 — NER Pipeline

## Overview

Named Entity Recognition (NER) is the primary mechanism for populating the knowledge graph from
ingested documents. It runs as a separate, always-on pass over all chunks — independent of the
optional LLM entity extraction pipeline (which is gated behind `ENABLE_CONTEXTUAL_PREFIX`).

The NER pipeline uses two complementary extraction methods:

1. **spaCy `en_core_web_trf`** — transformer-based NER for standard entity types (PERSON, ORG, LOC,
   DATE, etc.)
2. **LLM legal label pass** — Ollama-based extraction for domain-specific legal labels
   (LEGISLATION_TITLE, COURT_CASE, JURISDICTION, etc.)

Tags from both methods are merged and deduplicated by span overlap before storage.

---

## Critical Rule

**Always use `en_core_web_trf`. Never fall back to `en_core_web_sm` or any smaller model.**

sm-tagged chunks produce significantly lower-quality entity extraction and must be reprocessed.
The NER version counter exists specifically to detect and reprocess sm-tagged chunks.

If `en_core_web_trf` is not installed:

```bash
python -m spacy download en_core_web_trf
```

---

## Canonical Label Mapping

spaCy uses shorthand labels internally. The NER pipeline normalises all spaCy labels to canonical
names before storage. **The graph, node colors, and all downstream code must use canonical labels.**

```python
# python-api/app/llm/ner_tagger.py

SPACY_TO_CANONICAL: dict[str, str] = {
    "PERSON":   "PERSON",
    "ORG":      "ORGANIZATION",   # ← NOT "ORG"
    "GPE":      "LOCATION",       # ← NOT "GPE"
    "LOC":      "LOCATION",       # ← NOT "LOC"
    "FAC":      "LOCATION",
    "DATE":     "DATE",
    "TIME":     "DATE",
    "MONEY":    "MONEY",
    "PERCENT":  "PERCENT",
    "LAW":      "LAW",
    "NORP":     "ORGANIZATION",   # nationalities, religious/political groups
}
```

> **Common mistake**: The graph stores `"ORGANIZATION"` and `"LOCATION"`, not `"ORG"` or `"GPE"`.
> Any code that maps entity types to colors, icons, or filters must use the canonical names.
> See `frontend/src/components/graph/ForceGraph.tsx` `ENTITY_TYPE_COLORS` for the correct mapping.

---

## Legal NER Labels

The secondary LLM pass extracts legal-domain labels not covered by spaCy's general model:

```python
LEGAL_NER_LABELS: list[str] = [
    # Legislation
    "LEGISLATION_TITLE",        # "Companies Act 1967", "Criminal Procedure Code"
    "LEGISLATION_REFERENCE",    # "s 12(1)(a)", "Art. 4 para. 2"
    "STATUTE_SECTION",          # Section numbers within legislation
    # Case law
    "COURT_CASE",               # Case names: "Lim v PP [2021] SGCA 1"
    "CASE_CITATION",            # Formatted citations: "[2021] SGCA 1"
    # Parties and officers
    "COURT",                    # "Court of Appeal", "High Court of Singapore"
    "JUDGE",                    # "Justice Chan Sek Keong", "Lord Bingham"
    "LAWYER",                   # Advocates/solicitors appearing in a matter
    "PETITIONER",               # Initiating party (applicant, appellant, claimant)
    "RESPONDENT",               # Opposing party (defendant, respondent)
    "WITNESS",                  # Witnesses mentioned in proceedings
    # Concepts
    "JURISDICTION",             # "Singapore", "England and Wales", "EU"
    "LEGAL_CONCEPT",            # "mens rea", "estoppel", "fiduciary duty"
    "DEFINED_TERM",             # Terms defined within the document
]
```

`ALL_NER_LABELS` is the union of canonical spaCy labels and legal NER labels.

---

## NER Version

```python
NER_VERSION: int = 3
```

Chunks store the NER version they were tagged with. `get_outdated_ner_chunks()` returns all
chunks with `ner_version < NER_VERSION` for reprocessing.

Version history:
- **v1**: Initial hybrid spaCy + LLM legal NER
- **v2**: Re-run after fixing missing spaCy installation in venv
- **v3**: Installed `en_core_web_trf`; reprocess all sm-tagged chunks (v1/v2) with transformer

**Rule**: Increment `NER_VERSION` whenever:
- The spaCy model changes
- Label mapping (`SPACY_TO_CANONICAL`) changes
- Legal label list changes
- NER merging or dedup logic changes

---

## NER Tag Schema

Each chunk's `ner_tags` column stores a JSON array of `NerTag` objects:

```python
@dataclass
class NerTag:
    label: str      # canonical label e.g. "PERSON", "LEGISLATION_TITLE"
    text: str       # extracted text span
    start: int      # character offset start in chunk text
    end: int        # character offset end in chunk text
    score: float    # confidence 0.0–1.0
```

Stored as:

```json
[
  { "label": "PERSON",       "text": "John Smith",  "start": 12, "end": 22, "score": 0.99 },
  { "label": "ORGANIZATION", "text": "OpenAI",      "start": 35, "end": 41, "score": 0.95 },
  { "label": "COURT_CASE",   "text": "Lim v PP",    "start": 58, "end": 65, "score": 0.87 }
]
```

---

## Batch Processing

The NER pass is designed for high-throughput batch processing of large collections (500k+ chunks).

```
Configuration:
  _NER_BATCH_SIZE = 200    flush to LanceDB every N results
  _NER_CONCURRENCY = 16    parallel asyncio workers (spaCy runs sync via run_in_executor)

Flow:
  1. get_outdated_ner_chunks(collection_id, NER_VERSION)
  2. Semaphore(16) limits concurrent workers
  3. Each worker: tag_chunk(text) → tags_to_json(tags)
  4. Append to pending_batch
  5. Every 200 results (or on completion): bulk_update_chunk_ner_tags(batch)
```

LanceDB writes are batched to avoid per-row update overhead — a critical performance consideration
for collections with hundreds of thousands of chunks.

---

## Graph Construction from NER

After NER tagging, entity nodes and co-occurrence edges are built from the `ner_tags` column
using `build_graph_from_ner.py`. This creates the knowledge graph **without** LLM extraction.

The graph builder:
1. Scans all chunks in the collection for NER tags
2. Groups tags by canonical label
3. Creates/merges entity nodes (exact name match → merge; new → insert)
4. Creates co-occurrence edges between entities found in the same chunk
5. Writes nodes and edges in batches to LanceDB + in-memory petgraph

This is the **primary graph construction method** for Phase 1/2. LLM entity extraction
(`llm/extractor.py`) is optional and config-gated, providing richer relation labels
when enabled.

---

## Integration with Graph Viewer

Node colors in `frontend/src/components/graph/ForceGraph.tsx` use canonical label names:

```typescript
export const ENTITY_TYPE_COLORS: Record<string, string> = {
  PERSON:       '#4CAF50',   // green
  ORGANIZATION: '#2196F3',   // blue
  LOCATION:     '#FF9800',   // orange
  LAW:          '#607D8B',   // blue-grey
  DATE:         '#78909C',
  MONEY:        '#8BC34A',
  PERCENT:      '#B0BEC5',
  // Legal labels
  COURT_CASE:           '#9C27B0',
  LEGISLATION_TITLE:    '#3F51B5',
  LEGISLATION_REFERENCE:'#00BCD4',
  COURT:                '#FF5722',
  JUDGE:                '#795548',
};
```

**Never** add color mappings for spaCy shorthand labels (`ORG`, `GPE`, `LOC`) — these are
never stored in the graph and will never match.
