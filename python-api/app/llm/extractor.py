"""LLM extractor — entity/relationship extraction via Ollama Cloud.

All LLM calls route through ``call_ollama_cloud`` from
``app.llm.ollama_client`` — no direct ``httpx`` calls to the Ollama Cloud
endpoint anywhere in this module.
"""

import json
import logging
from typing import Optional

from app.llm.ollama_client import call_ollama_cloud, OllamaCloudError

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """You are a knowledge graph extraction system. Extract entities, relationships, and named entity spans from the text.

ALLOWED ENTITY TYPES: Person, Organization, Location, Concept, Event

LEGAL NER LABELS (for ner_spans only):
- LEGISLATION_TITLE: Full act or statute name (e.g. "Air Navigation Act 1966", "Companies Act")
- LEGISLATION_REFERENCE: Section/clause citations (e.g. "Section 42(1)", "s 12", "Art. 3")
- STATUTE_SECTION: Section headings or numbered divisions within the document
- COURT_CASE: Short-form case name only (e.g. "ABC v DEF", "Re Smith")
- CASE_CITATION: Formatted case citation with year and court (e.g. "[2022] SGCA 1", "(2019) 1 SLR 100")
- JURISDICTION: Governing jurisdiction (e.g. "Singapore", "Malaysia")
- LEGAL_CONCEPT: Defined legal terms (e.g. "mens rea", "vicarious liability", "promissory estoppel")
- DEFINED_TERM: Terms explicitly defined in the text, usually in quotes or parentheses
- COURT: Name of a court or tribunal (e.g. "Court of Appeal", "High Court", "Industrial Arbitration Court")
- JUDGE: Name of a judge, justice, or magistrate (e.g. "Justice Chan Sek Keong", "Lord Bingham CJ")
- LAWYER: Name of advocate, solicitor, or counsel (e.g. "Mr Tan Ah Kow (instructed counsel)")
- PETITIONER: Initiating party — applicant, appellant, claimant, or plaintiff by name
- RESPONDENT: Opposing party — defendant or respondent by name
- WITNESS: Name of a witness giving testimony in the proceedings

Rules:
1. Only use entity types from the ALLOWED ENTITY TYPES list for the entities array.
2. Each relationship must have valid domain and range.
3. Confidence should reflect extraction certainty (0.0-1.0).
4. For ner_spans: use EXACT text from the chunk (copy verbatim, do not paraphrase).
5. Return ONLY valid JSON matching the schema.

TEXT:
{chunk_text}

JSON SCHEMA:
{{
  "entities": [
    {{
      "name": "string",
      "entity_type": "string",
      "description": "string",
      "aliases": ["string"],
      "confidence": 0.0
    }}
  ],
  "relationships": [
    {{
      "source": "string",
      "target": "string",
      "predicate": "string",
      "context": "string",
      "confidence": 0.0
    }}
  ],
  "topics": ["string"],
  "summary": "string",
  "ner_spans": [
    {{
      "text": "string",
      "label": "string",
      "confidence": 0.0
    }}
  ]
}}
"""


class ExtractionError(Exception):
    pass


async def extract_from_chunk(chunk_text: str, job_id: Optional[str] = None) -> dict:
    response = await call_ollama_cloud(
        system_prompt="You are a knowledge graph extraction system.",
        user_prompt=EXTRACTION_PROMPT.format(chunk_text=chunk_text[:4000]),
        max_tokens=3000,
        job_id=job_id,
    )
    content = response["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(f"JSON decode error: {e}, content: {content[:200]}")
        raise ExtractionError(f"Invalid JSON from LLM: {e}")


async def generate_contextual_prefix(
    doc_summary: str,
    chunk_text: str,
    job_id: Optional[str] = None,
) -> str:
    prompt = f"""Given this document summary:
{doc_summary[:1000]}

And this chunk:
{chunk_text[:500]}

In exactly 2 sentences, describe what this chunk is about within the context of the document above. Be specific. Output ONLY the 2 sentences, nothing else."""

    try:
        response = await call_ollama_cloud(
            system_prompt="You are a document context assistant.",
            user_prompt=prompt,
            temperature=0.0,
            max_tokens=100,
            job_id=job_id,
        )
        prefix = response["content"]
        return f"{prefix}\n\n{chunk_text}"
    except OllamaCloudError as e:
        logger.warning(f"Prefix generation failed: {e}")
        return chunk_text


async def generate_doc_summary(raw_text: str, job_id: Optional[str] = None) -> str:
    prompt = f"""You are a document analyst. Provide a 200-300 word summary of this document covering its main topics, purpose, and key entities mentioned. Be factual and concise.

Document:
{raw_text[:6000]}
"""

    try:
        response = await call_ollama_cloud(
            system_prompt="You are a document analysis specialist.",
            user_prompt=prompt,
            max_tokens=400,
            job_id=job_id,
        )
        return response["content"]
    except OllamaCloudError as e:
        logger.warning(f"Summary generation failed: {e}")
        return ""