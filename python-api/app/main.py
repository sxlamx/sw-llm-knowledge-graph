"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
import asyncio
import time
import os

from app.config import get_settings
from app.core.rust_bridge import get_index_manager
from app.db.lancedb_client import get_lancedb, init_system_tables
from app.auth.middleware import auth_middleware, rate_limit_middleware
from app.routers import auth, collections, ingest, search, documents

settings = get_settings()
logger = logging.getLogger(__name__)

logging.basicConfig(
    level=getattr(logging, settings.rust_log.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up Knowledge Graph API...")
    try:
        os.makedirs(settings.lancedb_path, exist_ok=True)
        os.makedirs(settings.documents_path, exist_ok=True)
        await init_system_tables()
        logger.info("LanceDB initialized successfully")
    except Exception as e:
        logger.warning(f"LanceDB init warning (may not be critical): {e}")

    yield

    logger.info("Shutting down Knowledge Graph API...")


app = FastAPI(
    title="Knowledge Graph Builder API",
    description="LLM-Powered Knowledge Graph Builder with hybrid search",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)

app.middleware("http")(rate_limit_middleware)
app.middleware("http")(auth_middleware)

app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(collections.router, prefix="/api/v1/collections", tags=["collections"])
app.include_router(ingest.router, prefix="/api/v1/ingest", tags=["ingest"])
app.include_router(search.router, prefix="/api/v1/search", tags=["search"])
app.include_router(documents.router, prefix="/api/v1/documents", tags=["documents"])


@app.get("/health")
async def health_check():
    try:
        lancedb_status = "connected"
        rust_status = "connected"
        index_state = "unknown"

        try:
            im = get_index_manager()
            index_state = im.get_state()
        except Exception:
            rust_status = "not_initialized"

        return {
            "status": "ok",
            "version": "0.1.0",
            "lancedb": lancedb_status,
            "rust_core": rust_status,
            "index_state": index_state,
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "version": "0.1.0",
                "error": str(e),
            }
        )


@app.get("/api/v1/health")
async def api_health():
    return await health_check()


@app.get("/metrics")
async def metrics():
    try:
        im = get_index_manager()
        pending = im.pending_writes_count()
        permits = im.available_search_permits()
        state = im.get_state()
    except Exception:
        pending = 0
        permits = 100
        state = "unknown"

    lines = [
        "# HELP kg_index_state Index state (0=uninitialized, 1=building, 2=active, 3=compacting, 4=degraded)",
        "# TYPE kg_index_state gauge",
        f"kg_index_state {state}",
        "# HELP kg_index_pending_writes Vectors written since last compaction",
        "# TYPE kg_index_pending_writes gauge",
        f"kg_index_pending_writes {pending}",
        "# HELP kg_search_available_permits Available search semaphore permits",
        "# TYPE kg_search_available_permits gauge",
        f"kg_search_available_permits {permits}",
    ]
    return Response(content="\n".join(lines), media_type="text/plain")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https://lh3.googleusercontent.com; "
        "connect-src 'self' https://accounts.google.com; "
        "frame-ancestors 'none';"
    )
    return response
