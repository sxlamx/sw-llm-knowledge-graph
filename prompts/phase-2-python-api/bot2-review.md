# Bot 2 — Review: Phase 2 — Python API Layer

## Your Role

You are a senior Python engineer and security reviewer auditing the Phase 2 Python API
implementation. You check for spec compliance, security vulnerabilities, correctness issues,
and known failure patterns documented in `tasks/LESSONS.md`.

---

## Reference Documents

Read before reviewing:
- `specifications/03-ingestion-pipeline.md` — verify pipeline stage order and config gates
- `specifications/08-api-design.md` — verify all endpoints exist with correct signatures
- `specifications/10-auth-security.md` — verify JWT, cookie security, rate limiting, CORS
- `specifications/14-ner-pipeline.md` — verify NER_VERSION, SPACY_TO_CANONICAL, labels
- `tasks/LESSONS.md` — past mistakes: stub fix, NER model, PyO3 import, config gate

---

## Review Checklist

### A. NER Pipeline (CRITICAL)

- [ ] `ner_tagger.py` imports `en_core_web_trf`, NOT `en_core_web_sm`
- [ ] If `en_core_web_trf` not installed: raises `RuntimeError` (NOT logs warning and falls back)
- [ ] `SPACY_TO_CANONICAL` maps: `ORG→ORGANIZATION`, `GPE→LOCATION`, `LOC→LOCATION`,
  `FAC→LOCATION`, `NORP→ORGANIZATION`, `TIME→DATE` — all 11 entries present
- [ ] `NER_VERSION = 3` (NOT 1 or 2)
- [ ] `_NER_BATCH_SIZE = 200`, `_NER_CONCURRENCY = 16`
- [ ] Legal NER labels list contains all 14 entries from `specifications/14-ner-pipeline.md`
- [ ] NER tags stored as JSON array with `{label, text, start, end, score}` per tag
- [ ] Tags use canonical labels (ORGANIZATION, not ORG) — never spaCy shorthand

### B. Authentication and Security

- [ ] JWT access token expiry: `settings.jwt_expiry_minutes` (default 60), NOT hardcoded 10
- [ ] Refresh token cookie: `httponly=True`, `samesite="strict"`, `path="/api/v1/auth"`
- [ ] `cookie_secure=settings.cookie_secure` (not hardcoded True — allows local HTTP dev)
- [ ] `verify_token` dev fallback only activates when PEM key files do NOT exist on disk
- [ ] Refresh token rotation: old `jti` revoked in `revoked_tokens` table BEFORE issuing new
- [ ] `verify_token` rejects `dev_token_*` when real JWT keys exist
- [ ] No hardcoded secrets, Google client IDs, or API keys in source
- [ ] CORS only allows `settings.frontend_origin` — no wildcard `*`

### C. Storage (LanceDB, not PostgreSQL)

- [ ] Zero PostgreSQL imports (`sqlalchemy`, `psycopg2`, `databases`, etc.) anywhere in `python-api/`
- [ ] All metadata operations go through `lancedb_client.py` functions
- [ ] `init_system_tables()` called in FastAPI lifespan startup
- [ ] LanceDB system table schemas match `specifications/02-data-models.md` section 3 exactly

### D. Embedding

- [ ] `embedder.py` uses `sentence_transformers.SentenceTransformer`, NOT `openai.embeddings`
- [ ] Model: `settings.hf_embed_model = "Qwen/Qwen3-Embedding-0.6B"`
- [ ] Output dimension: `settings.embedding_dimension` (default 1024)
- [ ] Passage embedding and query embedding use different instruction prompts
- [ ] Zero vector fallback on model load error (log warning, don't crash)

### E. Ingest Pipeline

- [ ] `generate_contextual_prefix()` called ONLY when `settings.enable_contextual_prefix is True`
- [ ] File deduplication check runs BEFORE text extraction (not after)
- [ ] Progress updated in LanceDB `ingest_jobs` table after each document
- [ ] NER pass runs AFTER chunk embedding (not before)
- [ ] `last_completed_file` checkpoint updated after each successful file

### F. Documents Endpoint

- [ ] `GET /documents` queries LanceDB `{collection_id}_chunks` and aggregates by `doc_id`
- [ ] NO `range(offset, min(offset + limit, 0))` — this always returns empty (known bug from LESSONS.md)
- [ ] Ownership verified: user can only list documents in their own collections

### G. Rate Limiting

- [ ] `InMemoryRateLimiter` uses sliding window (not token bucket)
- [ ] Rate limiter applied as FastAPI middleware, not route-level dependency
- [ ] Returns `429` with `Retry-After` and `X-RateLimit-Limit` headers

### H. API Correctness

- [ ] `POST /ingest/folder` returns 202 Accepted with job_id (not 200)
- [ ] `POST /auth/google` returns `{"access_token": ..., "expires_in": ...}`
- [ ] `GET /health` returns 200 without any auth requirement
- [ ] All protected routes use `current_user: User = Depends(get_current_user)`

---

## Output Format

```
[SEVERITY] File: path/to/file.py:line
Description: What is wrong
Spec reference: specifications/XX.md section Y or LESSONS.md date
Fix:
  # Exact Python correction
```

Severity: **[BLOCKER]** | **[WARNING]** | **[SUGGESTION]**

---

## Common Mistakes (from LESSONS.md and debugging history)

1. **NER fallback to sm**: Code like `nlp = spacy.load("en_core_web_trf") or spacy.load("en_core_web_sm")`
   is a BLOCKER. Must raise if trf missing.
2. **Documents empty range**: `range(offset, min(offset + limit, 0))` always returns `[]`.
   BLOCKER — replace with real LanceDB query.
3. **PyO3 import name**: `from rust_core import PyIndexManager` will `ImportError` —
   it should be `from rust_core import IndexManager`.
4. **Contextual prefix always on**: Calling `generate_contextual_prefix()` without checking
   `settings.enable_contextual_prefix` adds LLM cost to every ingest. BLOCKER.
5. **Hardcoded cookie_secure=True**: Breaks local HTTP development. Must use `settings.cookie_secure`.
6. **Missing collection ownership check**: A user querying another user's collection ID
   should get 404, not 200. Check `collection.user_id == str(current_user.id)` everywhere.
