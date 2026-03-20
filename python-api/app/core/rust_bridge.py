"""Rust core bridge — PyO3 wrapper with async helpers."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Optional
import logging

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=8)
_index_manager: Optional["PyIndexManager"] = None

try:
    from rust_core import PyIndexManager, PySearchEngine, PyIngestionEngine, PyOntologyValidator
    RUST_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Rust core not available: {e}. Using fallback implementations.")
    RUST_AVAILABLE = False


def get_index_manager() -> Optional["PyIndexManager"]:
    global _index_manager
    if _index_manager is None and RUST_AVAILABLE:
        try:
            from app.config import get_settings
            settings = get_settings()
            _index_manager = PyIndexManager(settings.lancedb_path)
        except Exception as e:
            logger.error(f"Failed to initialize Rust IndexManager: {e}")
            return None
    return _index_manager


def get_search_engine() -> Optional["PySearchEngine"]:
    if RUST_AVAILABLE:
        return PySearchEngine()
    return None


def get_ingestion_engine() -> Optional["PyIngestionEngine"]:
    if RUST_AVAILABLE:
        return PyIngestionEngine()
    return None


def get_ontology_validator() -> Optional["PyOntologyValidator"]:
    if RUST_AVAILABLE:
        return PyOntologyValidator()
    return None


async def rust_search_async(collection_id: str, embedding: list[float], limit: int) -> list[dict]:
    im = get_index_manager()
    if im is None:
        return []

    loop = asyncio.get_event_loop()
    try:
        results = await loop.run_in_executor(
            _executor,
            lambda: im.vector_search(collection_id, embedding, limit),
        )
        return results
    except Exception as e:
        logger.error(f"Rust search error: {e}")
        return []


async def rust_insert_chunks_async(collection_id: str, chunks_json: str) -> int:
    im = get_index_manager()
    if im is None:
        return 0

    loop = asyncio.get_event_loop()
    try:
        count = await loop.run_in_executor(
            _executor,
            lambda: im.insert_chunks(collection_id, chunks_json),
        )
        return count
    except Exception as e:
        logger.error(f"Rust insert error: {e}")
        return 0


async def rust_init_collection_async(collection_id: str) -> None:
    im = get_index_manager()
    if im is None:
        return

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            _executor,
            lambda: im.initialize_collection(collection_id),
        )
    except Exception as e:
        logger.error(f"Rust init collection error: {e}")
