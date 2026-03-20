# 10 — Authentication and Security

## 1. Authentication Architecture

```
┌──────────────┐         ┌──────────────────┐         ┌──────────────────┐
│   Browser    │         │   FastAPI Backend │         │   Google OAuth   │
│              │         │                  │         │   Servers        │
│ 1. Click     │         │                  │         │                  │
│    "Sign in" │         │                  │         │                  │
│      │       │─────── Google popup ──────►│         │                  │
│      │       │         │                  │─────────► 2. Verify token  │
│      │       │         │                  │◄─────────  (userinfo)      │
│      │       │         │ 3. Create/lookup │         │                  │
│      │       │         │    user in DB    │         │                  │
│      │       │         │ 4. Issue RS256   │         │                  │
│      │       │         │    JWT pair      │         │                  │
│      │       │◄── access_token (JSON) ───│         │                  │
│      │       │◄── refresh_token (cookie)──│         │                  │
│      │       │         │                  │         │                  │
│ 5. API call  │         │                  │         │                  │
│      │Bearer ►         │ 6. Verify JWT    │         │                  │
│      │       │◄── 200 ─ signature + expiry│         │                  │
└──────────────┘         └──────────────────┘         └──────────────────┘
```

---

## 2. JWT Design

### Token Types

| Token | Algorithm | Expiry | Storage |
|-------|-----------|--------|---------|
| Access Token | RS256 | 10 minutes | Redux memory (NOT localStorage) |
| Refresh Token | RS256 | 7 days | HttpOnly cookie (`kg_refresh_token`) |

### JWT Claims

```json
{
  "sub": "user_uuid_v4",
  "email": "user@example.com",
  "name": "Jane Smith",
  "tenant_id": "collection_owner_uuid",
  "roles": ["user"],
  "iat": 1742394000,
  "exp": 1742394600,
  "jti": "unique_token_id_for_revocation"
}
```

### JWT Key Management

```python
# python-api/app/auth/jwt.py
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
import jwt as pyjwt
import uuid
from datetime import datetime, timedelta, timezone

def load_private_key(path: str):
    with open(path, 'rb') as f:
        return serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())

def issue_access_token(user: User, private_key) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "name": user.name,
        "tenant_id": str(user.id),
        "roles": ["user"],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=10)).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return pyjwt.encode(payload, private_key, algorithm="RS256")

def issue_refresh_token(user: User, private_key) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=7)).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return pyjwt.encode(payload, private_key, algorithm="RS256")

def verify_token(token: str, public_key) -> dict:
    return pyjwt.decode(token, public_key, algorithms=["RS256"])
```

### Refresh Token Rotation

On every use of the refresh token, a **new** refresh token is issued and the old `jti` is added
to a server-side blocklist (stored in PostgreSQL or Redis). This limits the blast radius of a
stolen refresh token.

```python
# python-api/app/routers/auth.py
@router.post("/auth/refresh")
async def refresh_token(
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    old_refresh = request.cookies.get("kg_refresh_token")
    if not old_refresh:
        raise HTTPException(401, "No refresh token")

    try:
        payload = verify_token(old_refresh, public_key)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(401, "Refresh token expired")
    except pyjwt.InvalidTokenError:
        raise HTTPException(401, "Invalid refresh token")

    # Check blocklist
    jti = payload["jti"]
    if await is_token_revoked(jti, db):
        raise HTTPException(401, "Refresh token revoked")

    # Rotate: revoke old, issue new
    await revoke_token(jti, db)
    user = await get_user_by_id(payload["sub"], db)
    new_access = issue_access_token(user, private_key)
    new_refresh = issue_refresh_token(user, private_key)

    response.set_cookie(
        key="kg_refresh_token",
        value=new_refresh,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=7 * 24 * 3600,
        path="/api/v1/auth",  # scoped to auth endpoints only
    )
    return {"access_token": new_access, "expires_in": 600}
```

---

## 3. FastAPI JWT Middleware

```python
# python-api/app/auth/middleware.py
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    token = credentials.credentials
    try:
        payload = verify_token(token, public_key)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired", headers={"WWW-Authenticate": "Bearer"})
    except pyjwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token", headers={"WWW-Authenticate": "Bearer"})

    user = await get_user_by_id(payload["sub"], db)
    if not user:
        raise HTTPException(401, "User not found")
    return user
```

---

## 4. Multi-Tenancy Isolation

### LanceDB Table Namespacing

Every LanceDB table is prefixed with the collection UUID:

```
{collection_id}_chunks
{collection_id}_nodes
{collection_id}_edges
{collection_id}_documents
{collection_id}_topics
```

The `IndexManager` maps collection IDs to table handles. It enforces ownership checks before
returning table handles:

```rust
pub async fn get_table(
    &self,
    collection_id: &Uuid,
    table_type: TableType,
    requesting_user_id: &Uuid,
) -> Result<Arc<Table>> {
    // Verify this user owns this collection (checked against PostgreSQL)
    self.verify_ownership(collection_id, requesting_user_id).await?;

    let key = format!("{}_{}", collection_id, table_type.as_str());
    let tables = self.tables.read().await;
    tables.get(&key)
        .ok_or(IndexError::TableNotFound(key))
        .map(Arc::clone)
}
```

### PostgreSQL Row-Level Security

All metadata tables have RLS policies enforced at the database level, providing defense-in-depth:

```sql
-- Enable RLS on collections
ALTER TABLE collections ENABLE ROW LEVEL SECURITY;

CREATE POLICY collections_user_isolation ON collections
    USING (user_id = current_setting('app.current_user_id')::UUID);

-- Enable RLS on ingest_jobs
ALTER TABLE ingest_jobs ENABLE ROW LEVEL SECURITY;

CREATE POLICY ingest_jobs_user_isolation ON ingest_jobs
    USING (
        collection_id IN (
            SELECT id FROM collections
            WHERE user_id = current_setting('app.current_user_id')::UUID
        )
    );
```

Application sets the user context before each query:

```python
async def get_db_with_user(user: User = Depends(get_current_user)):
    async with async_session() as session:
        await session.execute(
            text("SET LOCAL app.current_user_id = :user_id"),
            {"user_id": str(user.id)}
        )
        yield session
```

---

## 5. File Path Sanitization

File ingestion is a high-risk operation. The following protections prevent path traversal:

```rust
// rust-core/src/ingestion/scanner.rs

pub fn validate_path(path: &Path, allowed_root: &Path) -> Result<(), SecurityError> {
    // 1. Resolve to canonical absolute path (follows symlinks)
    let canonical = path.canonicalize()
        .map_err(|_| SecurityError::InvalidPath(path.to_path_buf()))?;

    // 2. Ensure it is within the allowed root
    if !canonical.starts_with(allowed_root) {
        return Err(SecurityError::PathTraversal {
            path: canonical,
            allowed_root: allowed_root.to_path_buf(),
        });
    }

    // 3. No symlinks pointing outside root
    // (canonicalize() already resolved these, so starts_with check covers it)

    // 4. Block sensitive file types
    if let Some(ext) = canonical.extension().and_then(|e| e.to_str()) {
        if BLOCKED_EXTENSIONS.contains(&ext.to_lowercase().as_str()) {
            return Err(SecurityError::BlockedFileType(ext.to_string()));
        }
    }

    Ok(())
}

const BLOCKED_EXTENSIONS: &[&str] = &[
    "exe", "sh", "bat", "cmd", "ps1", "py", "rb", "pl",  // executables/scripts
    "key", "pem", "p12", "pfx",                            // private keys
    "env",                                                  // environment files
    "sqlite", "db",                                         // databases (ingest only text)
];
```

The `allowed_root` is set from the `folder_path` in the `collections` record, which is itself
validated against an allowlist of user-configurable roots.

---

## 6. LLM Prompt Injection Prevention

### Structured Output Only

All LLM calls use `response_format: {"type": "json_object"}` with Pydantic validation. The LLM
cannot inject arbitrary content into the knowledge graph — only data that passes the Pydantic
schema and Rust ontology validator is accepted.

```python
# Correct pattern — structured output with schema enforcement
response = await openai_client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": extraction_prompt}],
    temperature=0.1,
    response_format={"type": "json_object"},  # structured output
    max_tokens=2000,
)
result = ExtractionResult.model_validate_json(response.choices[0].message.content)
# Rust validator then checks ontology compliance
```

### No Free-Form System Prompt Injection

User-provided text (document content, search queries) is NEVER placed in the `system` message.
It goes only in the `user` message, in a clearly delimited section:

```python
# SAFE: user content in delimited user message
messages = [
    {
        "role": "system",
        "content": STATIC_SYSTEM_PROMPT  # never contains user data
    },
    {
        "role": "user",
        "content": f"TEXT TO ANALYZE:\n<document>\n{sanitized_text}\n</document>\n\nJSON OUTPUT:"
    }
]
```

### Input Length Limits

All user-provided text fields are length-limited before being sent to the LLM:

```python
MAX_CHUNK_TOKENS = 2000
MAX_QUERY_LENGTH = 1000  # characters

def sanitize_for_llm(text: str, max_chars: int = 8000) -> str:
    """Truncate and strip control characters from text before LLM submission."""
    text = text[:max_chars]
    # Remove null bytes and other control characters except \n, \t
    text = ''.join(c for c in text if c >= ' ' or c in '\n\t')
    return text
```

---

## 7. Security Headers

```python
# python-api/app/main.py
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],  # e.g., "https://app.kg.example.com"
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "  # MUI requires unsafe-inline or nonce
        "img-src 'self' data: https://lh3.googleusercontent.com; "
        "connect-src 'self' https://accounts.google.com; "
        "frame-ancestors 'none';"
    )
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response
```

---

## 8. SQL Injection Prevention

All database queries use **parameterized queries** via SQLAlchemy async ORM. No raw string
interpolation is used in SQL:

```python
# CORRECT: parameterized query
user = await session.execute(
    select(User).where(User.email == email)  # SQLAlchemy ORM
)

# Also correct for raw SQL when needed:
await session.execute(
    text("SELECT * FROM collections WHERE user_id = :user_id AND name = :name"),
    {"user_id": str(user.id), "name": collection_name}
)

# NEVER do this:
# f"SELECT * FROM collections WHERE name = '{collection_name}'"  # SQL injection
```

---

## 9. Secrets Management

```
Environment variables (required):
  OPENAI_API_KEY          — OpenAI API key
  GOOGLE_CLIENT_ID        — Google OAuth client ID
  GOOGLE_CLIENT_SECRET    — Google OAuth client secret
  DATABASE_URL            — PostgreSQL connection string
  LANCEDB_PATH            — LanceDB storage path
  TANTIVY_PATH            — Tantivy index path
  JWT_PRIVATE_KEY_PATH    — Path to RS256 private key PEM file
  JWT_PUBLIC_KEY_PATH     — Path to RS256 public key PEM file
  ALLOWED_FOLDER_ROOTS    — Comma-separated list of allowed ingest roots

Docker Secrets (production):
  jwt_private_key         — PEM file mounted at /run/secrets/jwt_private_key
```

Rules:
1. **No hardcoded secrets** anywhere in the codebase
2. `.env` files are in `.gitignore` and never committed
3. In production, secrets are mounted via Docker secrets, not environment variables
4. The OpenAI API key is never sent to the frontend
5. JWT private key is loaded once at startup and stored in application memory only

---

## 10. Rate Limiting Implementation

```python
# python-api/app/middleware/rate_limit.py
from collections import defaultdict
import asyncio
import time

class InMemoryRateLimiter:
    def __init__(self, per_user_limit: int = 60, window_seconds: int = 60):
        self.per_user_limit = per_user_limit
        self.window = window_seconds
        self.user_counts: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def check(self, user_id: str) -> bool:
        async with self._lock:
            now = time.time()
            window_start = now - self.window
            # Expire old entries
            self.user_counts[user_id] = [t for t in self.user_counts[user_id] if t > window_start]
            if len(self.user_counts[user_id]) >= self.per_user_limit:
                return False
            self.user_counts[user_id].append(now)
            return True

rate_limiter = InMemoryRateLimiter(per_user_limit=60, window_seconds=60)

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Extract user ID from JWT if present (don't fail on unauth routes)
    user_id = extract_user_id_from_request(request)
    if user_id and not await rate_limiter.check(user_id):
        return JSONResponse(
            status_code=429,
            content={"error": "Rate limit exceeded", "code": "RATE_LIMITED"},
            headers={"Retry-After": "60", "X-RateLimit-Limit": "60"},
        )
    return await call_next(request)
```

For production deployments with multiple API replicas, use Redis-backed rate limiting instead
of in-memory.

---

## 11. Security Checklist

| Control | Implementation | Status |
|---------|---------------|--------|
| Authentication | Google OAuth 2.0 PKCE + RS256 JWT | Required |
| Authorization | JWT middleware on all non-auth routes | Required |
| Multi-tenancy | Table-level isolation + PostgreSQL RLS | Required |
| CORS | Allowlist of frontend origin only | Required |
| HTTPS | TLS 1.3 enforced (nginx/proxy layer) | Required |
| CSRF | SameSite=Strict cookie + no CORS wildcard | Required |
| XSS | React escaping + CSP header | Required |
| SQL injection | SQLAlchemy ORM + parameterized queries | Required |
| Path traversal | canonicalize() + starts_with() check | Required |
| Prompt injection | Structured output only, no free-form system prompt | Required |
| Secrets | Env vars + Docker secrets, no hardcoded values | Required |
| Rate limiting | Per-user 60/min, per-IP 200/min | Required |
| Token rotation | Refresh token rotated on each use | Required |
| Audit log | user_feedback table + structured logging | Recommended |
| Input validation | Pydantic schemas on all API inputs | Required |
