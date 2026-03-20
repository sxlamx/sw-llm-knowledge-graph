"""LLM extractor — entity/relationship extraction via Ollama Cloud."""

import httpx
import asyncio
import json
import logging
from typing import Optional
from tenacity import retry, stop_after_attempt, wait_exponential
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """You are a knowledge graph extraction system. Extract entities and relationships from the text.

ALLOWED ENTITY TYPES: Person, Organization, Location, Concept, Event

Rules:
1. Only use entity types from the list above.
2. Each relationship must have valid domain and range.
3. Confidence should reflect extraction certainty (0.0-1.0).
4. Return ONLY valid JSON matching the schema.

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
  "summary": "string"
}}
"""


class ExtractionError(Exception):
    pass


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
async def extract_from_chunk(chunk_text: str) -> dict:
    """Extract entities and relationships from a text chunk using Ollama Cloud."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        try:
            response = await client.post(
                f"{settings.ollama_cloud_base_url}/chat/completions",
                json={
                    "model": settings.ollama_cloud_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": EXTRACTION_PROMPT.format(chunk_text=chunk_text[:4000]),
                        }
                    ],
                    "temperature": 0.1,
                    "max_tokens": 2000,
                },
                headers={"Authorization": f"Bearer {settings.ollama_cloud_api_key}"},
            )

            if response.status_code == 429:
                raise ExtractionError("Rate limited by Ollama Cloud")
            response.raise_for_status()

            data = response.json()
            content = data["choices"][0]["message"]["content"].strip()

            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]

            return json.loads(content)

        except json.JSONDecodeError as e:
            logger.warning(f"JSON decode error: {e}, content: {content[:200]}")
            raise ExtractionError(f"Invalid JSON from LLM: {e}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise ExtractionError("Rate limited")
            raise ExtractionError(f"HTTP error: {e}")
        except Exception as e:
            logger.error(f"Extraction error: {e}")
            raise ExtractionError(str(e))


async def generate_contextual_prefix(
    doc_summary: str,
    chunk_text: str,
) -> str:
    """Generate a 2-sentence contextual prefix for a chunk."""
    prompt = f"""Given this document summary:
{doc_summary[:1000]}

And this chunk:
{chunk_text[:500]}

In exactly 2 sentences, describe what this chunk is about within the context of the document above. Be specific. Output ONLY the 2 sentences, nothing else."""

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        try:
            response = await client.post(
                f"{settings.ollama_cloud_base_url}/chat/completions",
                json={
                    "model": settings.ollama_cloud_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                    "max_tokens": 100,
                },
                headers={"Authorization": f"Bearer {settings.ollama_cloud_api_key}"},
            )
            response.raise_for_status()
            data = response.json()
            prefix = data["choices"][0]["message"]["content"].strip()
            return f"{prefix}\n\n{chunk_text}"
        except Exception as e:
            logger.warning(f"Prefix generation failed: {e}")
            return chunk_text


async def generate_doc_summary(raw_text: str) -> str:
    """Generate a 200-300 word document summary."""
    prompt = f"""You are a document analyst. Provide a 200-300 word summary of this document covering its main topics, purpose, and key entities mentioned. Be factual and concise.

Document:
{raw_text[:6000]}
"""

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        try:
            response = await client.post(
                f"{settings.ollama_cloud_base_url}/chat/completions",
                json={
                    "model": settings.ollama_cloud_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 400,
                },
                headers={"Authorization": f"Bearer {settings.ollama_cloud_api_key}"},
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"Summary generation failed: {e}")
            return ""
