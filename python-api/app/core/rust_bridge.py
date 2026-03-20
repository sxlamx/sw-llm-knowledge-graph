"""Rust core bridge — PyO3 wrapper with async helpers."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
import logging
import json

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=8)
_index_manager: Optional["PyIndexManager"] = None

try:
    # IndexManager is exported as "IndexManager" by PyO3 (not "PyIndexManager")
    from rust_core import IndexManager as PyIndexManager, PySearchEngine, PyIngestionEngine, PyOntologyValidator
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
        logger.error(f"Rust vector search error: {e}")
        return []


async def rust_keyword_search_async(collection_id: str, query: str, limit: int) -> list[dict]:
    im = get_index_manager()
    if im is None:
        return []

    loop = asyncio.get_event_loop()
    try:
        results_json = await loop.run_in_executor(
            _executor,
            lambda: im.text_search(collection_id, query, limit),
        )
        results = json.loads(results_json)
        return [
            {
                "id": r.get("id", ""),
                "doc_id": r.get("doc_id", ""),
                "text": r.get("text", ""),
                "collection_id": collection_id,
                "keyword_score": 1.0,
            }
            for r in results
        ]
    except Exception as e:
        logger.error(f"Rust keyword search error: {e}")
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


# ---------------------------------------------------------------------------
# Phase 3 background tasks
# ---------------------------------------------------------------------------

async def _tantivy_commit_loop(interval_seconds: float = 0.5) -> None:
    """Commit staged Tantivy documents every `interval_seconds`.

    This implements the Phase 3 "Tantivy batch committer: 500 ms interval"
    requirement.  Start once at application startup:

        asyncio.create_task(_tantivy_commit_loop())
    """
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(interval_seconds)
        im = get_index_manager()
        if im is None:
            continue
        try:
            await loop.run_in_executor(
                _executor,
                lambda: im.flush_tantivy(),
            )
        except Exception as e:
            logger.debug(f"Tantivy commit loop error (non-fatal): {e}")


async def _graph_prune_loop(
    collection_ids_fn,
    interval_seconds: float = 3600.0,
    min_weight: float = 0.3,
    max_degree: int = 100,
) -> None:
    """Hourly graph-pruning background task.

    `collection_ids_fn` is a zero-argument async callable that returns the
    list of active collection IDs to prune.  Example startup usage:

        asyncio.create_task(
            _graph_prune_loop(
                collection_ids_fn=lambda: list_active_collection_ids(),
            )
        )
    """
    import json as _json
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(interval_seconds)
        im = get_index_manager()
        if im is None:
            continue
        try:
            collection_ids = await collection_ids_fn()
            for cid in collection_ids:
                try:
                    result_json = await loop.run_in_executor(
                        _executor,
                        lambda c=cid: im.prune_graph(c, min_weight, max_degree),
                    )
                    result = _json.loads(result_json)
                    if result.get("edges_removed", 0) > 0:
                        logger.info(
                            "Graph pruned collection=%s removed=%d affected=%d",
                            cid,
                            result["edges_removed"],
                            result.get("nodes_affected", 0),
                        )
                except Exception as e:
                    logger.warning(f"Graph prune failed for collection {cid}: {e}")
        except Exception as e:
            logger.warning(f"Graph prune loop error: {e}")
