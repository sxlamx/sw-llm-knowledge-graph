"""Job manager — AsyncIO job queue and SSE broadcaster."""

import asyncio
from typing import Callable, Optional
from app.models.schemas import IngestOptions

_job_queues: dict[str, asyncio.Queue] = {}
_job_queues_lock = asyncio.Lock()
_subscribers: dict[str, list[Callable]] = {}
_subscribers_lock = asyncio.Lock()
_cancelled_jobs: set[str] = set()


class JobManager:
    def __init__(self):
        pass

    async def start_job(
        self,
        job_id: str,
        collection_id: str,
        folder_path: str,
        options: IngestOptions,
    ) -> None:
        from app.pipeline.ingest_worker import run_ingest_pipeline  # lazy to avoid circular
        await run_ingest_pipeline(job_id, collection_id, folder_path, options)

    async def emit(self, job_id: str, event: dict) -> None:
        async with _subscribers_lock:
            subscribers = list(_subscribers.get(job_id, []))
        for callback in subscribers:
            try:
                if asyncio.iscoroutinefunction(callback):
                    asyncio.ensure_future(callback(event))
                else:
                    callback(event)
            except Exception:
                pass

    async def subscribe(self, job_id: str, callback: Callable) -> None:
        async with _subscribers_lock:
            if job_id not in _subscribers:
                _subscribers[job_id] = []
            _subscribers[job_id].append(callback)

    async def unsubscribe(self, job_id: str, callback: Callable) -> None:
        async with _subscribers_lock:
            if job_id in _subscribers:
                _subscribers[job_id] = [cb for cb in _subscribers[job_id] if cb is not callback]
                if not _subscribers[job_id]:
                    del _subscribers[job_id]

    async def cancel_job(self, job_id: str) -> None:
        _cancelled_jobs.add(job_id)

    async def is_cancelled(self, job_id: str) -> bool:
        return job_id in _cancelled_jobs


_jm: Optional[JobManager] = None


def get_job_manager() -> JobManager:
    global _jm
    if _jm is None:
        _jm = JobManager()
    return _jm
