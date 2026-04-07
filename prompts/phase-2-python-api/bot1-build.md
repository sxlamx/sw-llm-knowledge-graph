# Bot 1 — Build: Phase 2 — Python API Layer

## Your Role

You are a senior Python engineer implementing the FastAPI orchestration layer for
`sw-llm-knowledge-graph`. This layer wraps the Rust core, handles authentication, drives
the ingestion pipeline, performs NER tagging, and exposes all REST endpoints.

---

## Project Context

- **Repo root**: `/Volumes/ExtremeSSD/github/sw-llm-knowledge-graph/`
- **API directory**: `python-api/app/`
- **Python version**: 3.12+
- **Framework**: FastAPI + Pydantic v2 + pydantic-settings
- **Storage**: LanceDB system tables — NO PostgreSQL, NO SQLAlchemy, NO Alembic
- **Embeddings**: HuggingFace `sentence-transformers`, model `Qwen/Qwen3-Embedding-0.6B`, 1024-dim
- **LLM (optional)**: Ollama Cloud (`llama3.2`) — gated behind config flags
- **NER**: spaCy `en_core_web_trf` — transformer model, NEVER `en_core_web_sm`

**Read these specs before writing any code:**
- `specifications/03-ingestion-pipeline.md` — pipeline stages, NER tagging, embedding
- `specifications/08-api-design.md` — all API endpoints, request/response schemas
- `specifications/10-auth-security.md` — Google OAuth, RS256 JWT, token rotation, secrets
- `specifications/14-ner-pipeline.md` — CRITICAL: NER labels, SPACY_TO_CANONICAL, NER_VERSION

---

## LESSONS.md Rules (Non-Negotiable)

1. **NER model**: Import `en_core_web_trf`. If missing: `python -m spacy download en_core_web_trf`.
   Raise `RuntimeError` if not installed — NEVER fall back to `en_core_web_sm`.
2. **PyO3 import**: `from rust_core import IndexManager as PyIndexManager` (Rust struct name).
   If import fails, set `RUST_AVAILABLE = False` and log warning — do NOT crash.
3. **LanceDB for metadata**: All users/collections/ingest_jobs stored in LanceDB system tables
   (see `lancedb_client.py`). No PostgreSQL/SQLAlchemy imports.
4. **Embeddings**: Use HuggingFace `sentence-transformers` with `Qwen/Qwen3-Embedding-0.6B`.
   DO NOT use `openai.embeddings.create()` anywhere in the ingest pipeline.
5. **Contextual prefix**: Gate `generate_contextual_prefix()` behind `settings.enable_contextual_prefix`
   (default `False`). Never call it unconditionally.
6. **Dev token fallback**: `verify_token()` accepts `dev_token_{user_id}` ONLY when JWT PEM key files
   do not exist on disk (`jwt_public_key.pem` missing).
7. **LanceDB documents stub fix**: Never use `range(offset, min(offset + limit, 0))` — always query
   LanceDB directly for the document list.

---

## Implementation Tasks (in dependency order)

### 1. Configuration (`app/config.py`)

`Settings` class using `pydantic-settings`:
```python
class Settings(BaseSettings):
    ollama_cloud_base_url: str = "https://api.ollama.com/v1"
    ollama_cloud_api_key: str = ""
    ollama_cloud_model: str = "llama3.2"
    hf_embed_model: str = "Qwen/Qwen3-Embedding-0.6B"
    hf_token: str = ""
    embedding_dimension: int = 1024
    lancedb_path: str = "/data/lancedb"
    documents_path: str = "/data/documents"
    google_client_id: str = ""
    google_client_secret: str = ""
    jwt_private_key_path: str = "./secrets/jwt_private_key.pem"
    jwt_public_key_path: str = "./secrets/jwt_public_key.pem"
    jwt_expiry_minutes: int = 60
    jwt_refresh_expiry_days: int = 7
    frontend_origin: str = "http://localhost:5333"
    cookie_secure: bool = False
    allowed_folder_roots: str = "/data/documents"
    enable_contextual_prefix: bool = False
    rate_limit_per_user: int = 60
    rate_limit_window_seconds: int = 60
```

### 2. LanceDB system tables (`app/db/lancedb_client.py`)

System tables (created at startup via `init_system_tables()`):
- `users`: id, google_sub, email, name, avatar_url, role, status, created_at, last_login
- `collections`: id, user_id, name, description, folder_path, status, doc_count, created_at, updated_at
- `ingest_jobs`: id, collection_id, status, progress, total_docs, processed_docs, error_msg, started_at, completed_at, created_at, options, last_completed_file
- `revoked_tokens`: jti, revoked_at, expires_at

All timestamps as `Int64` (Unix epoch ms). Use PyArrow schemas matching `specifications/02-data-models.md` section 3.

Key functions:
- `get_lancedb()` — singleton connection
- `init_system_tables()` — create tables if not exist
- `upsert_to_table(table_name, records, pkey)` — upsert pattern
- `get_collection(collection_id)`, `get_user_by_google_sub(google_sub)`
- `get_ingest_job(job_id)`, `update_ingest_job(job_id, updates)`
- `get_outdated_ner_chunks(collection_id, ner_version)` — returns chunks with `ner_version < NER_VERSION`
- `bulk_update_chunk_ner_tags(batch)` — batched LanceDB update for NER results

### 3. Google OAuth + JWT (`app/auth/`)

**`google.py`**: Validate Google ID token via `google.auth.transport.requests.Request` + `google.oauth2.id_token.verify_oauth2_token`.

**`jwt.py`**:
- `issue_access_token(user, private_key) -> str` — RS256, expiry `settings.jwt_expiry_minutes`
- `issue_refresh_token(user, private_key) -> str` — RS256, expiry `settings.jwt_refresh_expiry_days` days
- `verify_token(token, public_key) -> dict` — decode RS256; if PEM files don't exist, accept `dev_token_{user_id}` returning `{"sub": user_id}`
- Refresh token rotation: revoke old `jti` in `revoked_tokens` table, issue new

**`middleware.py`**:
- `get_current_user(credentials, db) -> User` — FastAPI dependency extracting JWT from Bearer header
- `InMemoryRateLimiter`: per-user sliding window, 60 req/min default

### 4. Auth router (`app/routers/auth.py`)

- `POST /auth/google` — validate Google token → find/create user in LanceDB → issue JWT pair → return access_token JSON + set refresh cookie
- `POST /auth/refresh` — rotate refresh token (check `revoked_tokens` → issue new pair)
- `POST /auth/logout` — revoke refresh token jti

Refresh token cookie: `HttpOnly=True`, `SameSite=strict`, `Secure=settings.cookie_secure`, path=`/api/v1/auth`.

### 5. Collections router (`app/routers/collections.py`)

- `GET /collections` — list user's collections from LanceDB `collections` table filtered by `user_id`
- `POST /collections` — create collection, validate folder_path against `allowed_folder_roots`
- `DELETE /collections/{id}` — delete collection + all associated LanceDB tables
- Ownership check: verify `collection.user_id == current_user.id` before any operation

### 6. HuggingFace embedder (`app/llm/embedder.py`)

```python
from sentence_transformers import SentenceTransformer
# Model: settings.hf_embed_model = "Qwen/Qwen3-Embedding-0.6B"
# Dim: settings.embedding_dimension = 1024
# Passage prompt: "" (empty)
# Query prompt: "Instruct: Given a search query, retrieve relevant document passages.\nQuery: "
```

- `embed_texts(texts: list[str]) -> list[list[float]]` — batch passage embeddings (batch_size=32)
- `embed_query(query: str) -> list[float]` — single query embedding with instruction prefix
- In-process LRU cache keyed by first 100 chars
- Fallback: zero vector on failure (log warning)

### 7. NER tagger (`app/llm/ner_tagger.py`)

**CRITICAL**: See `specifications/14-ner-pipeline.md` for full details.

```python
NER_VERSION = 3

SPACY_TO_CANONICAL = {
    "PERSON": "PERSON", "ORG": "ORGANIZATION", "GPE": "LOCATION",
    "LOC": "LOCATION", "FAC": "LOCATION", "DATE": "DATE", "TIME": "DATE",
    "MONEY": "MONEY", "PERCENT": "PERCENT", "LAW": "LAW", "NORP": "ORGANIZATION",
}

LEGAL_NER_LABELS = [
    "LEGISLATION_TITLE", "LEGISLATION_REFERENCE", "STATUTE_SECTION",
    "COURT_CASE", "JURISDICTION", "LEGAL_CONCEPT", "DEFINED_TERM",
    "COURT", "JUDGE", "LAWYER", "PETITIONER", "RESPONDENT", "WITNESS", "CASE_CITATION",
]
```

- `check_ner_ready()` — verify `en_core_web_trf` is installed; raise `RuntimeError` if not
- `tag_chunk(text: str) -> list[NerTag]` — run spaCy trf model, map to canonical labels
- `tags_to_json(tags: list[NerTag]) -> str` — serialize to JSON array
- `_run_ner_pass(collection_id, job_id)` — batch NER over all outdated chunks:
  - `_NER_BATCH_SIZE = 200`, `_NER_CONCURRENCY = 16`
  - flush to LanceDB every 200 results via `bulk_update_chunk_ner_tags`

### 8. Ingest worker (`app/pipeline/ingest_worker.py`)

Pipeline stages per document:
1. `extract_text_smart(file_path)` — PDF extraction (pymupdf) → raw text + pages
2. Split into chunks (fixed-size, no overlap needed for basic ingest)
3. `embed_texts(chunk_texts)` — HuggingFace batch embedding
4. Write chunks to LanceDB `{collection_id}_chunks` table via Rust bridge OR direct LanceDB write
5. If `settings.enable_contextual_prefix`: call `generate_contextual_prefix()` per chunk
6. After all chunks: `_run_ner_pass(collection_id, job_id)`
7. Update job progress via `job_manager.update_progress()`

File deduplication: check `{collection_id}_chunks` for existing `path` field before processing.

### 9. Ingest router (`app/routers/ingest.py`)

- `POST /ingest/folder` — validate folder_path, create `ingest_jobs` record, dispatch async task
- `GET /ingest/jobs/{id}` — return job status from LanceDB
- `GET /ingest/jobs/{id}/stream` — SSE stream of progress events

### 10. Documents router (`app/routers/documents.py`)

- `GET /documents?collection_id=xxx&limit=50&offset=0` — query LanceDB `{collection_id}_chunks`
  table, aggregate by `doc_id` to get unique documents. Return doc list.
  **NEVER** use `range(offset, min(offset + limit, 0))` — always query LanceDB directly.

### 11. Search router (`app/routers/search.py`)

- `POST /search` — call `search_service.hybrid_search()` with `mode` param
  (hybrid / vector / keyword / graph)

### 12. FastAPI app (`app/main.py`)

- CORS middleware with `settings.frontend_origin`
- Security headers middleware (CSP, X-Frame-Options, HSTS, X-Content-Type-Options)
- Rate limiter middleware
- Lifespan: `await init_system_tables()` on startup; start Tantivy flush loop (500ms interval)
- Mount all routers at `/api/v1/`
- Health check: `GET /health` — return `{"status": "ok"}` (no auth required)
- Guard all `rust_bridge` calls with `if RUST_AVAILABLE:` check

---

## Constraints

- NO PostgreSQL, SQLAlchemy, or Alembic imports anywhere
- NO `openai.embeddings.create()` in ingest pipeline
- NO fallback to `en_core_web_sm` — raise loudly if `en_core_web_trf` missing
- NO hardcoded secrets — read from `settings` or environment variables
- NO raw SQL string interpolation — use LanceDB where-clause string parameters carefully
- Gate all LLM calls with null checks on `settings.ollama_cloud_base_url`

---

## Acceptance Criteria

1. `POST /auth/google` with valid Google token returns `access_token` + sets `kg_refresh_token` cookie
2. `GET /collections` with valid JWT returns user's collections
3. `POST /collections` creates a record in LanceDB `collections` table
4. `POST /ingest/folder` starts an async job; `GET /ingest/jobs/{id}` shows progress
5. After ingest, `GET /documents?collection_id=xxx` returns non-empty list
6. `POST /search` with `mode: "vector"` returns relevant chunks
7. NER pass runs after ingest; chunks gain `ner_tags` JSON field with canonical labels
8. No `en_core_web_sm` fallback; missing `en_core_web_trf` raises `RuntimeError`
9. `verify_token` with `dev_token_alice` works when no PEM keys exist; fails when keys exist
10. `GET /health` returns 200 without authentication
