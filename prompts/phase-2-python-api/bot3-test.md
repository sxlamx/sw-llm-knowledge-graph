# Bot 3 — Test: Phase 2 — Python API Layer

## Your Role

You are a QA engineer writing Python tests for the Phase 2 API layer after Bot 2 review.
Tests cover authentication, ingestion pipeline, NER tagging, embedding, and all REST endpoints.

---

## Test Frameworks

- **Unit/Integration**: `pytest` + `pytest-asyncio`
- **HTTP mocking**: `respx` (mock httpx calls to Ollama, external services)
- **Test client**: `httpx.AsyncClient` with FastAPI `app` (no real server)
- **Coverage**: `pytest-cov`, target ≥ 80%

---

## Test File Locations

```
python-api/
  tests/
    conftest.py                   ← fixtures: app client, mock user, mock LanceDB
    test_auth.py                  ← Google OAuth, JWT issue/verify, token rotation
    test_collections.py           ← CRUD + ownership enforcement
    test_ingest.py                ← pipeline stages, NER pass, job progress
    test_documents.py             ← document list endpoint correctness
    test_search.py                ← vector/keyword/hybrid search modes
    test_ner_tagger.py            ← NER labels, SPACY_TO_CANONICAL, version
    test_embedder.py              ← HuggingFace embedder output shape
    test_lancedb_client.py        ← system table init + CRUD operations
```

---

## Key Test Cases

### `test_auth.py`

```python
@pytest.mark.asyncio
async def test_dev_token_accepted_without_keys(client, monkeypatch):
    """verify_token accepts dev_token_alice when no PEM files exist."""
    monkeypatch.setattr("app.auth.jwt.settings.jwt_public_key_pem", "")
    token = "dev_token_test-user-id"
    payload = verify_token(token, public_key=None)
    assert payload["sub"] == "test-user-id"

@pytest.mark.asyncio
async def test_dev_token_rejected_when_keys_exist(tmp_path):
    """verify_token rejects dev_token when real keys are present."""
    # Generate real RSA keys, write to tmp_path, patch settings
    # Attempt verify_token("dev_token_alice") → should raise InvalidTokenError

@pytest.mark.asyncio
async def test_refresh_token_rotation_revokes_old_jti(client):
    """Old refresh token jti should be in revoked_tokens after rotation."""
    # Login → get refresh cookie → call /auth/refresh → verify old jti revoked

@pytest.mark.asyncio
async def test_refresh_with_revoked_token_returns_401(client):
    """Using a revoked refresh token returns 401."""
    # Call /auth/refresh twice with same cookie → second call returns 401

@pytest.mark.asyncio
async def test_protected_endpoint_requires_auth(client):
    response = await client.get("/api/v1/collections")
    assert response.status_code == 401
```

### `test_collections.py`

```python
@pytest.mark.asyncio
async def test_create_collection_appears_in_list(authed_client):
    await authed_client.post("/api/v1/collections", json={"name": "test-col"})
    resp = await authed_client.get("/api/v1/collections")
    names = [c["name"] for c in resp.json()]
    assert "test-col" in names

@pytest.mark.asyncio
async def test_cannot_access_other_user_collection(authed_client, other_user_collection_id):
    """User A cannot view User B's collection."""
    resp = await authed_client.get(f"/api/v1/collections/{other_user_collection_id}")
    assert resp.status_code in (403, 404)

@pytest.mark.asyncio
async def test_delete_collection_removes_record(authed_client):
    # Create, delete, verify gone from GET
```

### `test_ingest.py`

```python
@pytest.mark.asyncio
async def test_ingest_job_created_in_lancedb(authed_client, tmp_pdf_folder):
    resp = await authed_client.post("/api/v1/ingest/folder",
        json={"collection_id": col_id, "folder_path": str(tmp_pdf_folder)})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    job_resp = await authed_client.get(f"/api/v1/ingest/jobs/{job_id}")
    assert job_resp.json()["status"] in ("pending", "running")

@pytest.mark.asyncio
async def test_contextual_prefix_not_called_by_default(monkeypatch, ...):
    """generate_contextual_prefix must not be called when ENABLE_CONTEXTUAL_PREFIX=False."""
    called = []
    monkeypatch.setattr("app.pipeline.ingest_worker.generate_contextual_prefix",
                        lambda *a, **k: called.append(True) or "prefix")
    await run_ingest_pipeline(job)
    assert len(called) == 0

@pytest.mark.asyncio
async def test_duplicate_file_skipped(authed_client, already_indexed_file):
    """File already in chunks table with same path is skipped."""
    # Run ingest twice, verify chunk count doesn't double
```

### `test_ner_tagger.py`

```python
def test_spacy_canonical_mapping_org_to_organization():
    from app.llm.ner_tagger import SPACY_TO_CANONICAL
    assert SPACY_TO_CANONICAL["ORG"] == "ORGANIZATION"
    assert SPACY_TO_CANONICAL["GPE"] == "LOCATION"
    assert SPACY_TO_CANONICAL["LOC"] == "LOCATION"
    assert SPACY_TO_CANONICAL["NORP"] == "ORGANIZATION"
    assert SPACY_TO_CANONICAL["FAC"] == "LOCATION"

def test_ner_version_is_3():
    from app.llm.ner_tagger import NER_VERSION
    assert NER_VERSION == 3

def test_check_ner_ready_raises_if_trf_missing(monkeypatch):
    """Must raise RuntimeError, not fall back to sm."""
    import spacy
    original_load = spacy.load
    def mock_load(name, **kwargs):
        if name == "en_core_web_trf":
            raise OSError("not installed")
        return original_load(name, **kwargs)  # sm would load
    monkeypatch.setattr(spacy, "load", mock_load)
    with pytest.raises(RuntimeError):
        asyncio.run(check_ner_ready())

def test_tag_chunk_returns_canonical_labels():
    """All returned tags use canonical labels, never spaCy shorthand."""
    tags = tag_chunk("Apple Inc was founded by Steve Jobs in California.")
    labels = {t.label for t in tags}
    assert "ORG" not in labels
    assert "GPE" not in labels
    if "ORGANIZATION" in labels or "LOCATION" in labels or "PERSON" in labels:
        pass  # At least one canonical label extracted
```

### `test_embedder.py`

```python
@pytest.mark.asyncio
async def test_embed_texts_returns_correct_dimension():
    embeddings = await embed_texts(["test sentence"])
    assert len(embeddings) == 1
    assert len(embeddings[0]) == settings.embedding_dimension  # 1024

@pytest.mark.asyncio
async def test_embed_query_returns_correct_dimension():
    emb = await embed_query("what is machine learning?")
    assert len(emb) == settings.embedding_dimension

@pytest.mark.asyncio
async def test_embed_empty_list_returns_empty():
    assert await embed_texts([]) == []
```

### `test_documents.py`

```python
@pytest.mark.asyncio
async def test_documents_returns_nonempty_after_ingest(authed_client, ingested_collection):
    resp = await authed_client.get(f"/api/v1/documents?collection_id={ingested_collection}")
    assert resp.status_code == 200
    assert len(resp.json()) > 0

@pytest.mark.asyncio
async def test_documents_uses_real_query_not_stub(authed_client, ingested_collection):
    """Regression test for range(offset, min(offset+limit, 0)) bug."""
    resp = await authed_client.get(
        f"/api/v1/documents?collection_id={ingested_collection}&limit=10&offset=0")
    # If the old stub is present, this would return [] even with data in DB
    assert len(resp.json()) > 0
```

---

## `conftest.py` Fixtures

```python
@pytest.fixture
def app_client():
    """FastAPI TestClient with in-memory LanceDB (temp dir)."""

@pytest.fixture
def authed_client(app_client, tmp_jwt_keys):
    """Client with valid JWT auth header."""

@pytest.fixture
def tmp_pdf_folder(tmp_path):
    """Create a folder with 2 sample PDFs for ingest testing."""
```

---

## Mock Patterns

- **Google OAuth**: Mock `google.oauth2.id_token.verify_oauth2_token` to return fixed payload
- **LanceDB path**: Use `tmp_path` fixture for all LanceDB connections (no real `/data/lancedb`)
- **HuggingFace model**: Monkeypatch `_get_model()` to return a mock that returns `[[0.1]*1024]`
- **Ollama Cloud**: Use `respx.mock` to intercept httpx calls to Ollama base URL

---

## Coverage Targets

- Target ≥ 80% line coverage across all `python-api/app/` modules
- All acceptance criteria from `phase-2-python-api/bot1-build.md` tested
- Auth security: at least 5 negative tests (invalid token, expired token, revoked refresh, ownership denial, dev token with keys)
