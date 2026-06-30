"""Topic extraction — LLM-based topic candidate extraction, canonicalization, and topic graph inference.

This module adapts the 3-stage prompt structure from requirements/prompts/:
  1. main-prompt.txt -> extract topic candidates per chunk
  2. entity.txt       -> canonicalize topic labels across chunks
  3. inference.txt    -> infer relationships between canonical topics

All LLM calls route through the existing Ollama Cloud API.
"""

import asyncio
import json
import logging
import re
from typing import Optional

import httpx
from app.config import get_settings

logger = logging.getLogger(__name__)

TOPIC_VERSION: int = 1

# ---------------------------------------------------------------------------
# Stage 1: Topic candidate extraction per chunk
# ---------------------------------------------------------------------------

TOPIC_EXTRACTION_SYSTEM_PROMPT = """
You are an advanced document analyst specialized in thematic topic extraction.
Your task is to identify the main topics discussed in a text chunk.
"""

TOPIC_EXTRACTION_USER_PROMPT = """
Read the text below (delimited by triple backticks) and identify the main topics it discusses.

Rules:
- Extract 3-8 topics per chunk. Use fewer topics for short or narrow chunks.
- Each topic should be a short, canonical label (1-3 words maximum).
- Use lower-case labels except for proper nouns.
- Standardize synonyms: e.g. "AI", "artificial intelligence", "A.I." -> "artificial intelligence".
- Provide 2-5 representative keywords per topic.
- Optionally note any named entities or concepts in the chunk and which topic they best belong to.

Output ONLY valid JSON matching this schema:

{
  "topics": [
    {
      "name": "string",
      "confidence": 0.0,
      "keywords": ["string"]
    }
  ],
  "entity_topic_links": [
    {
      "entity_name": "string",
      "topic": "string",
      "role": "example|concept|person|organization|location"
    }
  ]
}

Text to analyze:
```
{chunk_text}
```

Return only the JSON object, no commentary.
"""

# ---------------------------------------------------------------------------
# Stage 2: Topic canonicalization across chunks
# ---------------------------------------------------------------------------

TOPIC_CANONICALIZATION_SYSTEM_PROMPT = """
You are an expert in document classification and taxonomy.
Your task is to standardize topic labels so that synonymous or near-synonymous labels are merged.
"""

TOPIC_CANONICALIZATION_USER_PROMPT = """
Below is a list of topic labels extracted from many chunks of a document collection.
Some labels refer to the same real-world subject but are worded differently.

Please identify groups of labels that refer to the same subject, and provide a single standardized name for each group.
Return your answer as a JSON object where keys are standardized names and values are arrays of all variant labels that should map to that standard name.
Only include groups that have multiple variants or that need standardization.

Topic labels:
{topic_list}

Format your response as valid JSON like this:
{{
  "standardized topic 1": ["variant 1", "variant 2"],
  "standardized topic 2": ["variant 3", "variant 4", "variant 5"]
}}

Only output the JSON object.
"""

# ---------------------------------------------------------------------------
# Stage 3: Topic relationship inference
# ---------------------------------------------------------------------------

TOPIC_RELATIONSHIP_SYSTEM_PROMPT = """
You are an expert in knowledge representation and taxonomy construction.
Your task is to infer plausible semantic relationships between topics in a document collection.
"""

TOPIC_RELATIONSHIP_USER_PROMPT = """
I have the following canonical topics extracted from a document collection:
{topics}

Here are the co-occurrence statistics (how often pairs of topics appear together in the same chunk):
{cooccurrence_text}

Please infer plausible relationships between these topics.
Use only these predicates:
- "related_to" — topics are semantically related
- "subtopic_of" — one topic is a specialization of another
- "prerequisite_of" — one topic is typically needed to understand another

Return your answer as a JSON array of triples in this format:

[
  {{
    "subject": "topic a",
    "predicate": "related_to",
    "object": "topic b",
    "confidence": 0.8
  }}
]

Only include highly plausible relationships.
Make sure the subject and object are different topics.
Return only the JSON array.
"""

# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    for fence in ("```json", "```"):
        if text.startswith(fence):
            text = text[len(fence):]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _extract_json_objects(text: str, key: str) -> list[dict]:
    """Pull all complete {...} objects out of a named JSON array, even if truncated."""
    m = re.search(rf'"{re.escape(key)}"\s*:\s*\[', text)
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


def _safe_json_loads(text: str) -> Optional[dict | list]:
    """Try strict parse; fall back to object extraction if the response is wrapped or damaged."""
    text = _strip_markdown_fences(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find first { ... last }
    brace = text.find("{")
    if brace >= 0:
        text = text[brace:]
    rbrace = text.rfind("}")
    if rbrace >= 0:
        text = text[: rbrace + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Last resort: extract objects from known arrays
    topics = _extract_json_objects(text, "topics")
    entities = _extract_json_objects(text, "entity_topic_links")
    if topics or entities:
        return {"topics": topics, "entity_topic_links": entities}
    return None


# ---------------------------------------------------------------------------
# LLM call helper
# ---------------------------------------------------------------------------


async def _call_ollama(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1500,
    response_format: Optional[dict] = None,
) -> dict | list:
    settings = get_settings()
    payload = {
        "model": settings.topic_extraction_model or settings.ollama_cloud_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    if response_format:
        payload["response_format"] = response_format

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        response = await client.post(
            f"{settings.ollama_cloud_base_url}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {settings.ollama_cloud_api_key}"},
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"].strip()

    parsed = _safe_json_loads(content)
    if parsed is None:
        raise TopicExtractionError(f"Could not parse LLM response as JSON: {content[:200]}")
    return parsed


class TopicExtractionError(Exception):
    pass


# ---------------------------------------------------------------------------
# Stage 1 public API
# ---------------------------------------------------------------------------


async def extract_topics_from_chunk(chunk_text: str) -> dict:
    """Extract topic candidates and entity->topic links from a single chunk.

    Returns a dict with keys:
      - topics: list of {name, confidence, keywords}
      - entity_topic_links: list of {entity_name, topic, role}
    """
    if not chunk_text or not chunk_text.strip():
        return {"topics": [], "entity_topic_links": []}

    try:
        result = await _call_ollama(
            TOPIC_EXTRACTION_SYSTEM_PROMPT,
            TOPIC_EXTRACTION_USER_PROMPT.replace("{chunk_text}", chunk_text[:4000]),
            max_tokens=1200,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logger.warning(f"Topic extraction failed for chunk: {e}")
        return {"topics": [], "entity_topic_links": []}

    if not isinstance(result, dict):
        return {"topics": [], "entity_topic_links": []}

    topics = []
    for t in result.get("topics", []):
        if not isinstance(t, dict):
            continue
        name = (t.get("name") or "").strip().lower()
        if not name:
            continue
        topics.append({
            "name": name,
            "confidence": float(t.get("confidence", 0.8)),
            "keywords": [str(k).strip().lower() for k in t.get("keywords", []) if k],
        })

    links = []
    for l in result.get("entity_topic_links", []):
        if not isinstance(l, dict):
            continue
        entity_name = (l.get("entity_name") or "").strip()
        topic = (l.get("topic") or "").strip().lower()
        if not entity_name or not topic:
            continue
        links.append({
            "entity_name": entity_name,
            "topic": topic,
            "role": (l.get("role") or "concept").strip().lower(),
        })

    return {"topics": topics, "entity_topic_links": links}


# ---------------------------------------------------------------------------
# Stage 2 public API
# ---------------------------------------------------------------------------


async def canonicalize_topics(topic_names: list[str]) -> dict[str, list[str]]:
    """Return {canonical_name: [variants]} for all topics that need standardization.

    Unmentioned topics are left uncanonicalized and will retain their original names.
    """
    unique_names = sorted({t.strip().lower() for t in topic_names if t and t.strip()})
    if len(unique_names) <= 1:
        return {}

    try:
        result = await _call_ollama(
            TOPIC_CANONICALIZATION_SYSTEM_PROMPT,
            TOPIC_CANONICALIZATION_USER_PROMPT.replace("{topic_list}", "\n".join(f"- {t}" for t in unique_names)),
            max_tokens=1500,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logger.warning(f"Topic canonicalization failed: {e}")
        return {}

    if not isinstance(result, dict):
        return {}

    mappings: dict[str, list[str]] = {}
    for canonical, variants in result.items():
        if not isinstance(variants, list):
            continue
        canonical = (canonical or "").strip().lower()
        if not canonical:
            continue
        clean_variants = [str(v).strip().lower() for v in variants if v]
        if clean_variants:
            mappings[canonical] = clean_variants

    return mappings


# ---------------------------------------------------------------------------
# Stage 3 public API
# ---------------------------------------------------------------------------


async def infer_topic_relationships(
    topic_names: list[str],
    cooccurrence_pairs: list[tuple[str, str, int]],
) -> list[dict]:
    """Infer topic graph triples from canonical topics and co-occurrence data.

    Args:
        topic_names: list of canonical topic names
        cooccurrence_pairs: list of (topic_a, topic_b, count) co-occurrence counts

    Returns:
        list of {subject, predicate, object, confidence} triples
    """
    topic_names = sorted({t.strip().lower() for t in topic_names if t and t.strip()})
    if len(topic_names) < 2:
        return []

    cooc_text = "\n".join(
        f"- {a} + {b}: {count} chunks"
        for a, b, count in cooccurrence_pairs
        if a in topic_names and b in topic_names
    )
    if not cooc_text:
        return []

    try:
        result = await _call_ollama(
            TOPIC_RELATIONSHIP_SYSTEM_PROMPT,
            TOPIC_RELATIONSHIP_USER_PROMPT
            .replace("{topics}", "\n".join(f"- {t}" for t in topic_names))
            .replace("{cooccurrence_text}", cooc_text),
            max_tokens=1200,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logger.warning(f"Topic relationship inference failed: {e}")
        return []

    triples: list[dict] = []
    if isinstance(result, list):
        raw_triples = result
    elif isinstance(result, dict):
        raw_triples = result.get("relationships", result.get("triples", result.get("triplets", [])))
    else:
        raw_triples = []

    for t in raw_triples:
        if not isinstance(t, dict):
            continue
        subject = (t.get("subject") or "").strip().lower()
        obj = (t.get("object") or t.get("target") or "").strip().lower()
        predicate = (t.get("predicate") or "").strip().lower()
        if not subject or not obj or not predicate:
            continue
        if subject == obj:
            continue
        if predicate not in {"related_to", "subtopic_of", "prerequisite_of"}:
            continue
        triples.append({
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "confidence": float(t.get("confidence", 0.7)),
        })

    return triples
