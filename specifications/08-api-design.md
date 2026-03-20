# 08 — API Design

## 1. Overview

**Base URL**: `/api/v1`

**Protocol**: HTTPS (TLS 1.3 minimum in production)

**Authentication**: All endpoints except `/auth/*` require `Authorization: Bearer <JWT>`.
The JWT is a RS256-signed access token (10-minute expiry).

**Content Type**: `application/json` for all request/response bodies.

**API Version**: Included in URL path. Future versions will use `/api/v2`.

---

## 2. Standard Error Response

All error responses follow this format:

```json
{
  "error": "Human-readable error message",
  "code": "MACHINE_READABLE_CODE",
  "details": {}
}
```

Common error codes:

| HTTP Status | Code | Description |
|-------------|------|-------------|
| 400 | `VALIDATION_ERROR` | Invalid request body or parameters |
| 401 | `UNAUTHORIZED` | Missing or invalid JWT |
| 403 | `FORBIDDEN` | JWT valid but insufficient permissions |
| 404 | `NOT_FOUND` | Resource does not exist |
| 409 | `CONFLICT` | Resource already exists (e.g., duplicate collection name) |
| 422 | `UNPROCESSABLE` | Semantically invalid request |
| 429 | `RATE_LIMITED` | Too many requests |
| 500 | `INTERNAL_ERROR` | Server-side error |
| 503 | `INDEX_NOT_READY` | Index still building (retry after header provided) |

---

## 3. Rate Limiting

| Scope | Limit | Window |
|-------|-------|--------|
| Per user | 60 requests | 1 minute |
| Per IP | 200 requests | 1 minute |
| Concurrent searches per user | 10 | Simultaneous |
| LLM-heavy endpoints (`/ingest`, `/ontology/generate`) | 5 | 1 minute per user |

Rate limit headers included in all responses:
```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 45
X-RateLimit-Reset: 1742394000
```

---

## 4. Authentication Endpoints

### `POST /auth/google`

Exchange a Google ID token for application JWTs.

**Request:**
```json
{
  "id_token": "eyJhbGci..."
}
```

**Response 200:**
```json
{
  "access_token": "eyJhbGci...",
  "token_type": "bearer",
  "expires_in": 600,
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "name": "Jane Smith",
    "avatar_url": "https://lh3.googleusercontent.com/..."
  }
}
```
The `refresh_token` is set as an `HttpOnly` cookie (`kg_refresh_token`, 7-day expiry, `SameSite=Strict`).

---

### `POST /auth/refresh`

Refresh the access token using the HttpOnly refresh token cookie.

**Request**: No body required. Cookie is read automatically.

**Response 200:**
```json
{
  "access_token": "eyJhbGci...",
  "expires_in": 600
}
```
Issues a new refresh token cookie (rotation on each use).

---

### `POST /auth/logout`

Invalidate the refresh token (server-side blocklist) and clear the cookie.

**Response 204**: No content.

---

## 5. Collections Endpoints

### `GET /collections`

List all collections for the authenticated user.

**Response 200:**
```json
{
  "collections": [
    {
      "id": "uuid",
      "name": "Research Papers",
      "description": "ML research papers from 2020-2024",
      "folder_path": "/home/user/papers",
      "status": "active",
      "doc_count": 347,
      "created_at": "2026-01-15T10:00:00Z",
      "updated_at": "2026-03-01T14:22:00Z"
    }
  ]
}
```

---

### `POST /collections`

Create a new collection.

**Request:**
```json
{
  "name": "Research Papers",
  "description": "Optional description",
  "folder_path": "/home/user/papers"
}
```

**Response 201:**
```json
{
  "id": "uuid",
  "name": "Research Papers",
  "status": "active",
  "doc_count": 0,
  "created_at": "2026-03-19T10:00:00Z"
}
```

---

### `DELETE /collections/{id}`

Delete a collection and tombstone all associated data (documents, chunks, nodes, edges in LanceDB;
job records in PostgreSQL).

**Response 204**: No content.

The deletion is asynchronous. Background job removes LanceDB tables and drops Tantivy index.

---

## 6. Ingestion Endpoints

### `POST /ingest/folder`

Start an ingestion job for a local folder.

**Request:**
```json
{
  "collection_id": "uuid",
  "folder_path": "/home/user/documents/papers",
  "options": {
    "max_cost_usd": 5.00,
    "ocr_enabled": false,
    "max_files": 1000,
    "max_depth": 5,
    "chunk_size_tokens": 512,
    "chunk_overlap_tokens": 50
  }
}
```

**Response 202 Accepted:**
```json
{
  "job_id": "uuid",
  "status": "pending",
  "collection_id": "uuid",
  "created_at": "2026-03-19T10:00:00Z",
  "stream_url": "/api/v1/ingest/jobs/{job_id}/stream"
}
```

---

### `GET /ingest/jobs`

List ingest jobs for the authenticated user.

**Query params**: `?collection_id=uuid&status=running&limit=20&offset=0`

**Response 200:**
```json
{
  "jobs": [
    {
      "id": "uuid",
      "collection_id": "uuid",
      "status": "running",
      "progress": 0.45,
      "total_docs": 100,
      "processed_docs": 45,
      "started_at": "2026-03-19T10:01:00Z"
    }
  ],
  "total": 12
}
```

---

### `GET /ingest/jobs/{job_id}`

Get detailed status of a specific ingest job.

**Response 200:**
```json
{
  "id": "uuid",
  "collection_id": "uuid",
  "status": "running",
  "progress": 0.45,
  "total_docs": 100,
  "processed_docs": 45,
  "current_file": "attention_is_all_you_need.pdf",
  "cost_usd": 0.87,
  "cost_limit_usd": 5.00,
  "started_at": "2026-03-19T10:01:00Z",
  "estimated_completion": "2026-03-19T10:08:00Z",
  "error_msg": null
}
```

---

### `DELETE /ingest/jobs/{job_id}`

Cancel a running ingest job. Completed documents remain in the graph.

**Response 202**: Accepted (cancellation is async).

---

### `GET /ingest/jobs/{job_id}/stream`

Server-Sent Events stream for live progress updates.

**Response**: `Content-Type: text/event-stream`

```
data: {"type":"progress","job_id":"uuid","processed":10,"total":100,"current_file":"paper1.pdf","progress":0.1,"cost_usd":0.12}

data: {"type":"progress","job_id":"uuid","processed":11,"total":100,"current_file":"paper2.pdf","progress":0.11,"cost_usd":0.13}

data: {"type":"graph_update","job_id":"uuid","added_nodes":5,"added_edges":8}

data: {"type":"completed","job_id":"uuid","processed":100,"total":100,"duration_secs":420}
```

---

## 7. Search Endpoints

### `POST /search`

Hybrid search across one or more collections.

**Request:**
```json
{
  "query": "What are the main applications of transformer architecture?",
  "collection_ids": ["uuid1", "uuid2"],
  "topics": ["machine learning", "NLP"],
  "depth": 2,
  "limit": 20,
  "offset": 0,
  "mode": "hybrid",
  "weights": {
    "vector": 0.6,
    "keyword": 0.3,
    "graph": 0.1
  },
  "timeout_ms": 800
}
```

`mode` options: `"hybrid"` (default), `"vector"`, `"keyword"`, `"graph"`

**Response 200:**
```json
{
  "results": [
    {
      "chunk_id": "uuid",
      "doc_id": "uuid",
      "doc_title": "Attention Is All You Need",
      "doc_path": "/papers/attention.pdf",
      "text": "The Transformer model uses self-attention mechanisms...",
      "page": 3,
      "vector_score": 0.91,
      "keyword_score": 0.78,
      "graph_proximity_score": 0.45,
      "final_score": 0.823,
      "topics": ["machine learning", "transformers"],
      "highlights": ["transformer architecture", "self-attention"]
    }
  ],
  "total": 47,
  "offset": 0,
  "limit": 20,
  "latency_ms": 143,
  "search_mode": "hybrid"
}
```

---

### `GET /search/suggestions`

Query autocomplete from topic names and entity labels.

**Query params**: `?q=transform&collection_id=uuid&limit=10`

**Response 200:**
```json
{
  "suggestions": [
    "transformer architecture",
    "transfer learning",
    "transformation functions"
  ]
}
```

---

## 8. Graph Endpoints

### `GET /graph/nodes/{id}`

Get node details and its immediate neighborhood.

**Query params**: `?depth=1&collection_id=uuid`

**Response 200:**
```json
{
  "node": {
    "id": "uuid",
    "label": "OpenAI",
    "entity_type": "Organization",
    "description": "An AI safety company...",
    "aliases": ["Open AI", "OpenAI Inc."],
    "confidence": 0.98,
    "ontology_class": "Organization/Company",
    "created_at": "2026-01-10T00:00:00Z"
  },
  "subgraph": {
    "nodes": [...],
    "edges": [...]
  },
  "linked_chunks": [
    {
      "chunk_id": "uuid",
      "text": "OpenAI was founded in 2015...",
      "doc_title": "AI Company History",
      "page": 1
    }
  ]
}
```

---

### `GET /graph/path`

Find the shortest path between two entities.

**Query params**: `?from=uuid&to=uuid&max_depth=4&collection_id=uuid`

**Response 200:**
```json
{
  "found": true,
  "path": [
    {"node_id": "uuid", "label": "Sam Altman", "entity_type": "Person"},
    {"edge_id": "uuid", "predicate": "works_at", "weight": 0.95},
    {"node_id": "uuid", "label": "OpenAI", "entity_type": "Organization"},
    {"edge_id": "uuid", "predicate": "located_in", "weight": 0.90},
    {"node_id": "uuid", "label": "San Francisco", "entity_type": "Location"}
  ],
  "total_cost": 2.15
}
```

---

### `GET /graph/subgraph`

Get a subgraph filtered by topic or entity type.

**Query params**: `?topic=machine+learning&depth=2&limit=200&collection_id=uuid`

**Response 200:**
```json
{
  "nodes": [...],
  "edges": [...],
  "stats": {
    "node_count": 45,
    "edge_count": 78
  }
}
```

---

### `GET /graph/data`

Paginated full graph for the frontend visualizer.

**Query params**: `?collection_id=uuid&page=0&page_size=500`

**Response 200:**
```json
{
  "nodes": [...],
  "edges": [...],
  "total_nodes": 12453,
  "total_edges": 38291,
  "page": 0,
  "page_size": 500
}
```

---

### `GET /graph/export`

Export the full graph in the specified format.

**Query params**: `?format=json&collection_id=uuid` or `?format=graphml&collection_id=uuid`

**Response 200**: Returns the exported content as a downloadable file.

---

### `PUT /graph/nodes/{id}`

Manually edit a node's properties (human-in-the-loop correction).

**Request:**
```json
{
  "collection_id": "uuid",
  "label": "OpenAI",
  "description": "Updated description...",
  "aliases": ["Open AI", "OpenAI Inc.", "OpenAI LP"],
  "confidence": 1.0
}
```

**Response 200**: Returns the updated node.

---

### `POST /graph/edges`

Manually create a new edge between two entities.

**Request:**
```json
{
  "collection_id": "uuid",
  "source_id": "uuid",
  "target_id": "uuid",
  "predicate": "works_at",
  "weight": 1.0,
  "context": "Manually added by user"
}
```

**Response 201**: Returns the created edge.

---

### `DELETE /graph/edges/{id}`

Delete an edge and store negative feedback to prevent future re-extraction.

**Request body**: `{"collection_id": "uuid"}`

**Response 204**: No content.

---

## 9. Topic Endpoints

### `GET /topics`

List topics for a collection, sorted by frequency.

**Query params**: `?collection_id=uuid&limit=50`

**Response 200:**
```json
{
  "topics": [
    {
      "id": "uuid",
      "name": "machine learning",
      "keywords": ["neural network", "training", "model"],
      "frequency": 347,
      "score": 0.89
    }
  ]
}
```

---

### `GET /topics/{id}/nodes`

Get entities assigned to a specific topic.

**Query params**: `?limit=100&offset=0`

**Response 200:**
```json
{
  "topic": {"id": "uuid", "name": "machine learning"},
  "nodes": [...],
  "total": 156
}
```

---

## 10. Ontology Endpoints

### `GET /ontology`

Get the active ontology for a collection.

**Query params**: `?collection_id=uuid`

**Response 200**: Returns the full ontology JSON schema (see `04-ontology-engine.md`).

---

### `POST /ontology/generate`

Trigger LLM-assisted ontology generation from sample documents.

**Request:**
```json
{
  "collection_id": "uuid",
  "sample_doc_ids": ["uuid1", "uuid2", "uuid3"],
  "model": "gpt-4o"
}
```

**Response 200:**
```json
{
  "proposal": { "version": "1.0", "entity_types": {...}, "relationship_types": {...} },
  "applied": false,
  "message": "Review the proposal and call PUT /ontology to apply"
}
```

---

### `PUT /ontology`

Replace the active ontology. Creates a new version record. Triggers validation of existing graph
against the new ontology (async background task).

**Request**: Full ontology JSON schema body.

**Response 200:** `{"version": "1.1", "applied": true}`

---

### `GET /ontology/versions`

List version history.

**Query params**: `?collection_id=uuid`

**Response 200:**
```json
{
  "versions": [
    {"id": "uuid", "version": "1.1", "is_active": true, "created_at": "..."},
    {"id": "uuid", "version": "1.0", "is_active": false, "created_at": "..."}
  ]
}
```

---

## 11. Documents Endpoints

### `GET /documents`

List documents in a collection.

**Query params**: `?collection_id=uuid&limit=50&offset=0`

**Response 200:**
```json
{
  "documents": [
    {
      "id": "uuid",
      "title": "Attention Is All You Need",
      "file_type": "pdf",
      "path": "/papers/attention.pdf",
      "doc_summary": "This paper introduces the Transformer architecture...",
      "created_at": "2026-01-10T00:00:00Z"
    }
  ],
  "total": 347
}
```

---

### `GET /documents/{id}`

Get a document's metadata and its chunks.

**Response 200:**
```json
{
  "document": {
    "id": "uuid",
    "title": "Attention Is All You Need",
    "file_type": "pdf",
    "path": "/papers/attention.pdf",
    "doc_summary": "This paper introduces the Transformer...",
    "metadata": {"author": "Vaswani et al.", "pages": 15}
  },
  "chunks": [
    {
      "chunk_id": "uuid",
      "position": 0,
      "page": 1,
      "text": "The dominant sequence transduction models...",
      "topics": ["transformers", "attention"]
    }
  ],
  "chunk_count": 28
}
```

---

### `DELETE /documents/{id}`

Delete a document and tombstone all its chunks, nodes, and edges derived exclusively from it.

**Request body**: `{"collection_id": "uuid"}`

**Response 204**: No content.

---

## 12. WebSocket: `WS /ws`

Real-time bidirectional channel for push notifications.

### Client → Server Messages

```json
{"type": "subscribe_job", "job_id": "uuid"}
{"type": "subscribe_collection", "collection_id": "uuid"}
{"type": "unsubscribe", "job_id": "uuid"}
```

### Server → Client Messages

```json
{"type": "progress", "job_id": "uuid", "processed": 45, "total": 100, "current_file": "paper.pdf", "progress": 0.45}
{"type": "graph_update", "collection_id": "uuid", "added_nodes": 5, "added_edges": 8}
{"type": "job_completed", "job_id": "uuid", "processed": 100}
{"type": "job_failed", "job_id": "uuid", "error": "Budget limit exceeded"}
{"type": "index_state_change", "collection_id": "uuid", "state": "compacting"}
```

---

## 13. Health and Metrics Endpoints

### `GET /health`

Service health check (no auth required).

**Response 200:**
```json
{
  "status": "ok",
  "version": "1.0.0",
  "lancedb": "connected",
  "postgres": "connected",
  "index_state": "active"
}
```

### `GET /metrics`

Prometheus-format metrics (requires admin role).

```
# HELP kg_search_latency_ms Search latency in milliseconds
# TYPE kg_search_latency_ms histogram
kg_search_latency_ms_bucket{le="100"} 1234
...

# HELP kg_concurrent_searches Current number of concurrent searches
# TYPE kg_concurrent_searches gauge
kg_concurrent_searches 12

# HELP kg_index_pending_writes Vectors written since last compaction
# TYPE kg_index_pending_writes gauge
kg_index_pending_writes 4521
```
