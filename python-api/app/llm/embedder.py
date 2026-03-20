"""LLM embedder — Ollama local embeddings (qwen3-embedding with 1536-dim MRL)."""

import httpx
import asyncio
import logging
from typing import Optional
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

_cache: dict[str, list[float]] = {}
_cache_lock = asyncio.Lock()
_CACHE_TTL_SECONDS = 300


async def embed_texts(
    texts: list[str],
    batch_size: int = 10,
) -> list[list[float]]:
    """Embed texts using local Ollama (qwen3-embedding with configurable dimensions)."""
    if not texts:
        return []

    all_embeddings = []
    texts_to_embed = []

    async with _cache_lock:
        for i, text in enumerate(texts):
            cache_key = f"{text[:100]}"
            if cache_key in _cache:
                all_embeddings.append((i, _cache[cache_key]))
            else:
                texts_to_embed.append((i, text))

    for i, text in texts_to_embed:
        cache_key = f"{text[:100]}"
        try:
            embedding = await _embed_single(text)
            async with _cache_lock:
                _cache[cache_key] = embedding
            all_embeddings.append((i, embedding))
        except Exception as e:
            logger.warning(f"Embedding failed for text (len={len(text)}): {e}")
            all_embeddings.append((i, [0.0] * settings.ollama_embed_dimensions))

    all_embeddings.sort(key=lambda x: x[0])
    return [emb for _, emb in all_embeddings]


async def _embed_single(text: str) -> list[float]:
    """Call local Ollama /api/embed endpoint with MRL dimension control."""
    timeout = httpx.Timeout(30.0, connect=10.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{settings.ollama_embed_base_url}/api/embed",
            json={
                "model": settings.ollama_embed_model,
                "input": text[:8000],
                "dimensions": settings.ollama_embed_dimensions,
            },
        )
        response.raise_for_status()
        data = response.json()

        embeddings = data.get("embeddings", [])
        if not embeddings:
            raise ValueError("No embeddings returned from Ollama")

        return embeddings[0]


async def embed_query(query: str) -> list[float]:
    """Embed a single query string."""
    results = await embed_texts([query])
    return results[0] if results else [0.0] * settings.ollama_embed_dimensions
