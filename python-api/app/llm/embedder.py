"""LLM embedder — HuggingFace sentence-transformers (local, GPU-accelerated).

Uses Qwen/Qwen3-Embedding by default. Supports MRL dimension truncation and
separate prompt instructions for passages (indexing) vs queries (search).
"""

import asyncio
import logging
from functools import lru_cache
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

_cache: dict[str, list[float]] = {}
_cache_lock = asyncio.Lock()

# Qwen3-Embedding passage/query prompts (empty = no instruction = passage mode)
_PASSAGE_PROMPT = ""
_QUERY_PROMPT = "Instruct: Given a search query, retrieve relevant document passages.\nQuery: "


@lru_cache(maxsize=1)
def _get_model():
    """Load the sentence-transformers model once and cache it."""
    from sentence_transformers import SentenceTransformer
    logger.info(f"Loading embedding model: {settings.hf_embed_model}")
    token = settings.hf_token or None
    model = SentenceTransformer(
        settings.hf_embed_model,
        token=token,
        trust_remote_code=True,
    )
    logger.info(f"Embedding model loaded (dim={model.get_sentence_embedding_dimension()})")
    return model


def _encode_batch(texts: list[str], prompt: str = "") -> list[list[float]]:
    """Run inference synchronously (called via run_in_executor)."""
    model = _get_model()
    dim = settings.embedding_dimension
    encode_kwargs: dict = {
        "batch_size": 32,
        "show_progress_bar": False,
        "normalize_embeddings": True,
    }
    if prompt:
        encode_kwargs["prompt"] = prompt
    embeddings = model.encode(texts, **encode_kwargs)
    # Truncate to configured dimension (MRL support)
    return [e.tolist()[:dim] for e in embeddings]


async def embed_texts(
    texts: list[str],
) -> list[list[float]]:
    """Embed document passages using a local HuggingFace sentence-transformers model.

    Model is loaded once and reused. Cache-hits are served immediately.
    """
    if not texts:
        return []

    results: list[list[float]] = [[]] * len(texts)
    uncached: list[tuple[int, str]] = []

    async with _cache_lock:
        for i, text in enumerate(texts):
            key = text[:100]
            if key in _cache:
                results[i] = _cache[key]
            else:
                uncached.append((i, text))

    if uncached:
        loop = asyncio.get_event_loop()
        try:
            batch_texts = [b[1] for b in uncached]
            embeddings = await loop.run_in_executor(
                None, _encode_batch, batch_texts, _PASSAGE_PROMPT
            )
            async with _cache_lock:
                for (idx, text), emb in zip(uncached, embeddings):
                    _cache[text[:100]] = emb
                    results[idx] = emb
        except Exception as e:
            logger.warning(f"Embedding failed: {e}")
            fallback = [0.0] * settings.embedding_dimension
            for idx, _ in uncached:
                results[idx] = fallback

    return results


async def embed_query(query: str) -> list[float]:
    """Embed a search query (uses query instruction prompt)."""
    loop = asyncio.get_event_loop()
    try:
        embeddings = await loop.run_in_executor(
            None, _encode_batch, [query], _QUERY_PROMPT
        )
        return embeddings[0]
    except Exception as e:
        logger.warning(f"Query embedding failed: {e}")
        return [0.0] * settings.embedding_dimension
