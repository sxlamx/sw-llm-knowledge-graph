"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, REGISTRY
import asyncio
import time
import os

from app.config import get_settings
from app.core.logging_config import setup_logging
from app.core.rust_bridge import get_index_manager, _tantivy_commit_loop
from app.core.metrics import KG_PENDING_WRITES, KG_INDEX_STATE, KG_CONCURRENT_SEARCHES
from app.db.lancedb_client import get_lancedb, init_system_tables
from app.auth.middleware import auth_middleware, rate_limit_middleware
from app.auth.csrf import csrf_middleware, generate_csrf_token, set_csrf_cookie
from app.routers import auth, collections, ingest, search, documents
from app.routers import graph, ontology, topics
from app.routers import drive, analytics, agent, finetune, admin
from app.routers.ws import router as ws_router

settings = get_settings()
setup_logging(
    log_dir=settings.log_dir,
    sentry_dsn=settings.sentry_dsn,
    log_level=settings.rust_log,
)
logger = logging.getLogger(__name__)


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

    # Phase 3: start Tantivy batch-commit background task (500 ms interval).
    commit_task = asyncio.create_task(_tantivy_commit_loop(interval_seconds=0.5))

    yield

    commit_task.cancel()
    try:
        await commit_task
    except asyncio.CancelledError:
        pass

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

allowed_origins = [origin.strip() for origin in settings.frontend_origin.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-CSRF-Token"],
)

app.middleware("http")(csrf_middleware)
app.middleware("http")(rate_limit_middleware)
app.middleware("http")(auth_middleware)

app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(collections.router, prefix="/api/v1/collections", tags=["collections"])
app.include_router(ingest.router, prefix="/api/v1/ingest", tags=["ingest"])
app.include_router(search.router, prefix="/api/v1/search", tags=["search"])
app.include_router(documents.router, prefix="/api/v1/documents", tags=["documents"])
app.include_router(graph.router, prefix="/api/v1/graph", tags=["graph"])
app.include_router(ontology.router, prefix="/api/v1/ontology", tags=["ontology"])
app.include_router(topics.router, prefix="/api/v1/topics", tags=["topics"])
app.include_router(drive.router, prefix="/api/v1/drive", tags=["drive"])
app.include_router(analytics.router, prefix="/api/v1/analytics", tags=["analytics"])
app.include_router(agent.router, prefix="/api/v1/agent", tags=["agent"])
app.include_router(finetune.router, prefix="/api/v1/finetune", tags=["finetune"])
app.include_router(admin.router,   prefix="/api/v1/admin",   tags=["admin"])
app.include_router(ws_router, tags=["websocket"])


@app.get("/csrf-token")
async def get_csrf_token(response: Response):
    """Generate and return a CSRF token for the session."""
    token = generate_csrf_token()
    set_csrf_cookie(response, token)
    return {"csrf_token": token}


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
        KG_PENDING_WRITES.set(im.pending_writes_count())
        KG_INDEX_STATE.set(im.get_state())
        available = im.available_search_permits()
        # search_semaphore capacity is 100; slots in use = capacity - available
        KG_CONCURRENT_SEARCHES.set(max(0, 100 - available))
    except Exception:
        pass  # metrics best-effort; don't fail health checks

    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://accounts.google.com https://apis.google.com; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https://lh3.googleusercontent.com https://www.gstatic.com; "
        "connect-src 'self' https://accounts.google.com https://oauth2.googleapis.com "
        "ws://localhost:8333 wss://localhost:8333; "
        "frame-src https://accounts.google.com; "
        "frame-ancestors 'none';"
    )
    return response
