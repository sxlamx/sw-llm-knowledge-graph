# 12 — Project Structure

## 1. Top-Level Directory Layout

```
sw-llm-knowledge-graph/
│
├── rust-core/                      # Rust crate (compiled to .so via Maturin)
│   ├── Cargo.toml
│   ├── Cargo.lock
│   ├── pyproject.toml              # Maturin build configuration
│   ├── src/
│   │   ├── lib.rs                  # PyO3 module definition and exports
│   │   ├── index_manager.rs        # IndexManager: state machine, locking, atomic swap
│   │   ├── graph_engine.rs         # KnowledgeGraph, petgraph ops, entity resolution
│   │   ├── search_engine.rs        # Hybrid search orchestration, score fusion
│   │   ├── models.rs               # Shared Rust structs: Node, Edge, Chunk, SearchResult
│   │   ├── errors.rs               # Error types (thiserror)
│   │   │
│   │   ├── ingestion/
│   │   │   ├── mod.rs
│   │   │   ├── scanner.rs          # File discovery, BLAKE3 hash, notify file watcher
│   │   │   ├── extractor.rs        # PDF/DOCX/TXT/MD/HTML text extraction
│   │   │   └── chunker.rs          # text-splitter, overlap logic, boundary detection
│   │   │
│   │   ├── ontology/
│   │   │   ├── mod.rs
│   │   │   ├── types.rs            # EntityTypeDef, RelationshipTypeDef, Ontology struct
│   │   │   ├── validator.rs        # OntologyValidator, ValidationReport
│   │   │   └── rules.rs            # ValidationRule trait + built-in implementations
│   │   │
│   │   ├── storage/
│   │   │   ├── mod.rs
│   │   │   ├── lancedb.rs          # LanceDB table ops, schema builders, RecordBatch construction
│   │   │   └── tantivy.rs          # TantivyHandle, BM25 index ops, batch committer
│   │   │
│   │   ├── graph/
│   │   │   ├── mod.rs
│   │   │   ├── builder.rs          # Entity resolution, graph construction from LLM output
│   │   │   ├── traversal.rs        # BFS, Dijkstra, subgraph extraction, batched hops
│   │   │   └── export.rs           # GraphML, JSON export
│   │   │
│   │   └── wal/
│   │       ├── mod.rs
│   │       ├── writer.rs           # WalWriter: append-only log for graph mutations
│   │       └── recovery.rs         # Replay WAL on startup
│   │
│   └── tests/
│       ├── index_concurrency_test.rs   # Concurrency stress tests for IndexManager
│       ├── search_test.rs              # Hybrid search integration tests
│       ├── ontology_validation_test.rs # Validator unit tests
│       ├── entity_resolution_test.rs   # Fuzzy merge tests
│       └── graph_traversal_test.rs     # BFS, Dijkstra, subgraph tests
│
├── python-api/                     # FastAPI orchestration layer
│   ├── pyproject.toml              # Dependencies (uv/poetry)
│   ├── requirements.txt            # Pinned dependencies for Docker
│   ├── alembic.ini                 # Alembic migration config
│   │
│   └── app/
│       ├── main.py                 # FastAPI app, CORS, middleware, startup/shutdown
│       ├── config.py               # Settings (pydantic-settings, env vars)
│       │
│       ├── auth/
│       │   ├── google.py           # Google ID token validation (google-auth-library)
│       │   ├── jwt.py              # RS256 JWT issue/verify, token rotation
│       │   └── middleware.py       # get_current_user dependency, rate limiter
│       │
│       ├── routers/
│       │   ├── auth.py             # /auth/google, /auth/refresh, /auth/logout
│       │   ├── collections.py      # GET/POST/DELETE /collections
│       │   ├── ingest.py           # POST /ingest/folder, GET /ingest/jobs, SSE stream
│       │   ├── search.py           # POST /search, GET /search/suggestions
│       │   ├── graph.py            # GET/PUT /graph/nodes, GET /graph/path, /graph/subgraph
│       │   ├── topics.py           # GET /topics, GET /topics/{id}/nodes
│       │   ├── ontology.py         # GET/PUT /ontology, POST /ontology/generate
│       │   └── documents.py        # GET/DELETE /documents
│       │
│       ├── llm/
│       │   ├── extractor.py        # Ontology-guided entity/relation extraction prompts
│       │   ├── chunker.py          # Contextual prefix generation (GPT-4o-mini)
│       │   ├── embedder.py         # text-embedding-3-large batched calls
│       │   └── ontogpt.py          # Ontology bootstrap from sample documents
│       │
│       ├── pipeline/
│       │   ├── ingest_worker.py    # Full ingestion pipeline: scan → extract → chunk → embed → graph
│       │   └── job_manager.py      # AsyncIO job queue, SSE broadcaster, status tracking
│       │
│       ├── db/
│       │   ├── postgres.py         # SQLAlchemy async models + session factory
│       │   ├── models.py           # SQLAlchemy ORM models (User, Collection, IngestJob, etc.)
│       │   └── migrations/
│       │       ├── env.py
│       │       └── versions/
│       │           ├── 001_initial_schema.py
│       │           ├── 002_add_ontology_tables.py
│       │           └── 003_add_user_feedback.py
│       │
│       ├── models/
│       │   └── schemas.py          # Pydantic request/response schemas
│       │
│       └── core/
│           ├── rust_bridge.py      # PyO3 import wrapper, ThreadPoolExecutor, async helpers
│           └── websocket.py        # WebSocket connection manager
│
│   └── tests/
│       ├── test_auth.py
│       ├── test_ingest.py
│       ├── test_search.py
│       └── test_graph.py
│
├── frontend/                       # React 18 + Vite + MUI
│   ├── package.json
│   ├── package-lock.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── index.html
│   │
│   └── src/
│       ├── main.tsx                # React root, Redux Provider, GoogleOAuthProvider, Router
│       ├── App.tsx                 # Route definitions, lazy loading, Suspense
│       ├── theme.ts                # MUI theme configuration (dark/light)
│       │
│       ├── store/
│       │   ├── index.ts            # configureStore, root reducer, middleware
│       │   ├── slices/
│       │   │   ├── authSlice.ts
│       │   │   ├── collectionsSlice.ts
│       │   │   ├── searchSlice.ts
│       │   │   ├── graphSlice.ts
│       │   │   └── uiSlice.ts
│       │   └── wsMiddleware.ts     # WebSocket Redux middleware
│       │
│       ├── api/
│       │   ├── baseApi.ts          # RTK Query createApi, baseQuery with JWT refresh
│       │   ├── authApi.ts
│       │   ├── collectionsApi.ts
│       │   ├── ingestApi.ts
│       │   ├── searchApi.ts
│       │   ├── graphApi.ts
│       │   ├── ontologyApi.ts
│       │   └── documentsApi.ts
│       │
│       ├── pages/
│       │   ├── Landing.tsx
│       │   ├── Dashboard.tsx
│       │   ├── Collection.tsx
│       │   ├── Search.tsx
│       │   ├── GraphViewer.tsx
│       │   ├── OntologyEditor.tsx
│       │   └── Settings.tsx
│       │
│       ├── components/
│       │   ├── auth/
│       │   │   ├── GoogleLoginButton.tsx
│       │   │   └── RequireAuth.tsx
│       │   ├── graph/
│       │   │   ├── ForceGraph.tsx      # react-force-graph-2d wrapper
│       │   │   ├── NodeDetailPanel.tsx # MUI Drawer, entity details, edit form
│       │   │   ├── PathFinder.tsx      # Two-node selection, highlight shortest path
│       │   │   └── GraphControls.tsx   # Depth slider, edge filter, topic filter
│       │   ├── search/
│       │   │   ├── SearchBar.tsx       # Debounced input, mode selector, autocomplete
│       │   │   ├── ResultCard.tsx      # Single result card with highlight
│       │   │   ├── TopicSidebar.tsx    # MUI Drawer, topic multi-select checkboxes
│       │   │   └── SearchResults.tsx   # react-window virtualized list
│       │   ├── ingest/
│       │   │   ├── IngestPanel.tsx     # Folder picker (FSA API), options form, start button
│       │   │   ├── ProgressBar.tsx     # SSE-driven linear progress
│       │   │   └── JobStatusChip.tsx   # Status badge (pending/running/completed/failed)
│       │   └── common/
│       │       ├── Layout.tsx          # AppBar + Navigation Drawer + main content
│       │       ├── NavBar.tsx          # Top navigation with user menu
│       │       ├── ThemeProvider.tsx   # MUI theme + dark mode toggle
│       │       ├── ErrorBoundary.tsx
│       │       └── LoadingOverlay.tsx
│       │
│       ├── hooks/
│       │   ├── useDebounce.ts
│       │   ├── useSSE.ts           # Server-Sent Events hook with auto-reconnect
│       │   └── useGraphData.ts     # Graph data pagination hook
│       │
│       ├── types/
│       │   ├── api.ts              # TypeScript interfaces matching API schemas
│       │   ├── graph.ts            # Graph node/edge types for frontend
│       │   └── store.ts            # Redux state shape types
│       │
│       └── workers/
│           └── graphLayout.worker.ts  # Web Worker: d3-force layout computation
│
│   └── tests/
│       ├── components/
│       │   ├── SearchBar.test.tsx
│       │   └── GraphViewer.test.tsx
│       └── store/
│           └── authSlice.test.ts
│
├── docker/
│   ├── docker-compose.yml          # Full stack orchestration
│   ├── docker-compose.dev.yml      # Development overrides (hot reload)
│   ├── Dockerfile.rust             # Build context for rust-core extension
│   ├── Dockerfile.api              # Python FastAPI + rust-core .so
│   ├── Dockerfile.frontend         # Nginx serving Vite build
│   └── nginx.conf                  # Nginx config for frontend + API proxy
│
├── specifications/                 # Design specifications (this directory)
│   ├── 00-index.md
│   ├── 01-system-architecture.md
│   ├── 02-data-models.md
│   ├── 03-ingestion-pipeline.md
│   ├── 04-ontology-engine.md
│   ├── 05-index-manager.md
│   ├── 06-search-engine.md
│   ├── 07-graph-engine.md
│   ├── 08-api-design.md
│   ├── 09-frontend-design.md
│   ├── 10-auth-security.md
│   ├── 11-concurrency-performance.md
│   ├── 12-project-structure.md
│   └── 13-development-roadmap.md
│
├── scripts/
│   ├── generate_jwt_keys.sh        # Generate RS256 key pair
│   ├── seed_db.py                  # Seed PostgreSQL with test data
│   └── benchmark.sh                # Run Criterion benchmarks
│
├── .github/
│   └── workflows/
│       ├── rust-ci.yml             # cargo test + cargo clippy + cargo fmt
│       ├── python-ci.yml           # pytest + mypy + ruff
│       └── frontend-ci.yml         # vitest + tsc + eslint
│
├── .env.example                    # Template for required environment variables
├── .gitignore
└── README.md
```

---

## 2. Cargo.toml (rust-core)

```toml
[package]
name = "rust-core"
version = "0.1.0"
edition = "2021"

[lib]
name = "rust_core"
crate-type = ["cdylib"]  # Required for PyO3 extension module

[dependencies]
# Vector store
lancedb = "0.9"

# Arrow (zero-copy columnar data)
arrow-array  = "53"
arrow-schema = "53"
arrow-select = "53"

# Async runtime
tokio = { version = "1", features = ["full"] }
futures = "0.3"

# CPU parallelism
rayon = "1"

# Graph (in-memory)
petgraph = "0.6"

# Full-text search
tantivy = "0.22"

# Python bindings
pyo3 = { version = "0.22", features = ["extension-module"] }

# Serialization
serde      = { version = "1", features = ["derive"] }
serde_json = "1"

# Unique IDs
uuid = { version = "1", features = ["v4", "serde"] }

# Hashing
blake3 = "1"

# File watching
notify = "6"

# Text splitting / chunking
text-splitter = { version = "0.14", features = ["tiktoken-rs"] }

# HTTP server (if Rust exposes its own HTTP layer)
axum  = "0.7"
tower = "0.4"
tower-http = { version = "0.5", features = ["cors", "trace"] }

# Observability
tracing              = "0.1"
tracing-subscriber   = { version = "0.3", features = ["env-filter"] }
opentelemetry        = "0.22"
opentelemetry-jaeger = "0.21"
metrics              = "0.22"
metrics-exporter-prometheus = "0.13"

# Caching
lru = "0.12"

# Concurrent HashMap (lock-free reads)
dashmap = "5"

# Faster Mutex/RwLock (parking_lot uses OS futex, lower overhead than std)
parking_lot = "0.12"

# Error handling
thiserror = "1"
anyhow    = "1"

# String similarity (Levenshtein for entity resolution)
strsim = "0.11"

# Ordered float (for BinaryHeap in Dijkstra)
ordered-float = "4"

# PDF extraction
lopdf       = "0.32"
pdf-extract = "0.7"

# DOCX extraction
docx-rs = "0.4"

# HTML extraction
scraper = "0.19"

# Markdown parsing
pulldown-cmark = "0.11"

# Async trait
async-trait = "0.1"

# Time
chrono = { version = "0.4", features = ["serde"] }

[dev-dependencies]
tokio       = { version = "1", features = ["full", "test-util"] }
criterion   = { version = "0.5", features = ["html_reports"] }
tempfile    = "3"
rand        = "0.8"

[[bench]]
name    = "search_bench"
harness = false

[[bench]]
name    = "index_bench"
harness = false

[profile.release]
opt-level     = 3
lto           = true
codegen-units = 1
panic         = "abort"   # smaller binary, faster panic handling

[profile.dev]
opt-level = 1             # faster compile, some optimization for tests
```

---

## 3. Python pyproject.toml (python-api)

```toml
[build-system]
requires      = ["hatchling"]
build-backend = "hatchling.build"

[project]
name    = "knowledge-graph-api"
version = "0.1.0"
requires-python = ">=3.12"

dependencies = [
    # Web framework
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sse-starlette>=2.0",

    # Auth
    "google-auth>=2.30",
    "PyJWT>=2.8",
    "cryptography>=42.0",

    # LLM
    "openai>=1.30",

    # Database
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.29",
    "alembic>=1.13",

    # Validation
    "pydantic>=2.7",
    "pydantic-settings>=2.3",

    # HTTP client
    "httpx>=0.27",

    # Python bindings to Rust core
    "rust-core",   # installed via maturin develop or wheel

    # Utilities
    "python-multipart>=0.0.9",
    "aiofiles>=23.0",
    "tenacity>=8.3",
    "tiktoken>=0.7",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
    "mypy>=1.10",
    "ruff>=0.4",
    "httpx>=0.27",  # for TestClient
]
```

---

## 4. Frontend package.json

```json
{
  "name": "knowledge-graph-frontend",
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest",
    "lint": "eslint src --ext .ts,.tsx",
    "typecheck": "tsc --noEmit"
  },
  "dependencies": {
    "react": "^18.3.0",
    "react-dom": "^18.3.0",
    "react-router-dom": "^6.23.0",
    "@mui/material": "^6.0.0",
    "@mui/x-data-grid": "^7.0.0",
    "@mui/icons-material": "^6.0.0",
    "@emotion/react": "^11.11.0",
    "@emotion/styled": "^11.11.0",
    "@reduxjs/toolkit": "^2.3.0",
    "react-redux": "^9.1.0",
    "@react-oauth/google": "^0.12.0",
    "react-force-graph-2d": "^1.25.0",
    "cytoscape": "^3.30.0",
    "react-window": "^1.8.10",
    "d3-force": "^3.0.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@types/react-window": "^1.8.8",
    "@types/cytoscape": "^3.20.0",
    "@types/d3-force": "^3.0.10",
    "@vitejs/plugin-react": "^4.3.0",
    "vite": "^5.3.0",
    "typescript": "^5.5.0",
    "vitest": "^1.6.0",
    "@testing-library/react": "^16.0.0",
    "@testing-library/user-event": "^14.5.0",
    "eslint": "^9.5.0",
    "@typescript-eslint/eslint-plugin": "^7.14.0"
  }
}
```

---

## 5. Docker Configuration

### docker-compose.yml

```yaml
version: "3.9"

services:
  python-api:
    build:
      context: ..
      dockerfile: docker/Dockerfile.api
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql+asyncpg://kg:${POSTGRES_PASSWORD}@postgres:5432/kg
      LANCEDB_PATH: /data/lancedb
      TANTIVY_PATH: /data/tantivy
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      GOOGLE_CLIENT_ID: ${GOOGLE_CLIENT_ID}
      GOOGLE_CLIENT_SECRET: ${GOOGLE_CLIENT_SECRET}
      FRONTEND_ORIGIN: http://localhost:3000
      JWT_PRIVATE_KEY_PATH: /run/secrets/jwt_private_key
      JWT_PUBLIC_KEY_PATH: /run/secrets/jwt_public_key
      ALLOWED_FOLDER_ROOTS: /data/documents
    volumes:
      - lancedb-data:/data/lancedb
      - tantivy-data:/data/tantivy
      - doc-storage:/data/documents
    depends_on:
      postgres:
        condition: service_healthy
    secrets:
      - jwt_private_key
      - jwt_public_key
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 5s
      retries: 3

  frontend:
    build:
      context: ..
      dockerfile: docker/Dockerfile.frontend
    ports:
      - "3000:80"
    environment:
      VITE_API_BASE_URL: http://python-api:8000/api/v1
      VITE_GOOGLE_CLIENT_ID: ${GOOGLE_CLIENT_ID}
    depends_on:
      - python-api
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: kg
      POSTGRES_USER: kg
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pg-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U kg"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

volumes:
  lancedb-data:
  tantivy-data:
  doc-storage:
  pg-data:

secrets:
  jwt_private_key:
    file: ./secrets/jwt_private_key.pem
  jwt_public_key:
    file: ./secrets/jwt_public_key.pem
```

### Dockerfile.api

```dockerfile
# Stage 1: Build Rust core
FROM rust:1.82-slim AS rust-builder
WORKDIR /build/rust-core
RUN apt-get update && apt-get install -y python3-dev python3-pip && rm -rf /var/lib/apt/lists/*
RUN pip3 install maturin
COPY rust-core/ .
RUN maturin build --release --strip

# Stage 2: Python API with Rust extension
FROM python:3.12-slim AS api
WORKDIR /app
RUN apt-get update && apt-get install -y libssl-dev curl && rm -rf /var/lib/apt/lists/*

COPY --from=rust-builder /build/rust-core/target/wheels/*.whl /tmp/
RUN pip install /tmp/*.whl

COPY python-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY python-api/ .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

### Dockerfile.frontend

```dockerfile
# Stage 1: Build React app
FROM node:22-alpine AS builder
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# Stage 2: Serve with Nginx
FROM nginx:1.27-alpine AS production
COPY --from=builder /app/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/nginx.conf
EXPOSE 80
```

---

## 6. CI/CD (GitHub Actions)

### rust-ci.yml (key steps)

```yaml
- name: Run Clippy
  run: cargo clippy --all-features -- -D warnings

- name: Run tests
  run: cargo test --all-features -- --test-threads=4

- name: Run benchmarks (dry run)
  run: cargo bench --no-run

- name: Check formatting
  run: cargo fmt --check
```

### python-ci.yml (key steps)

```yaml
- name: Lint with ruff
  run: ruff check python-api/

- name: Type check with mypy
  run: mypy python-api/app/

- name: Run pytest
  run: pytest python-api/tests/ --cov=app --cov-report=xml
```
