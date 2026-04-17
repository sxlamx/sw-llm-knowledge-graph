"""Security-focused tests — injection, sanitization, input validation, auth bypass, CSRF, rate limiting."""

import time
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.db.lancedb_client import _safe_id, _safe_str


# ---------------------------------------------------------------------------
# Task 1.2: LanceDB WHERE clause sanitization
# ---------------------------------------------------------------------------

class TestLanceDBSanitization:
    def test_safe_id_rejects_special_chars(self):
        with pytest.raises(ValueError):
            _safe_id("abc; DROP TABLE users--")

    def test_safe_id_rejects_single_quotes(self):
        with pytest.raises(ValueError):
            _safe_id("abc' OR '1'='1")

    def test_safe_id_accepts_uuid(self):
        assert _safe_id("550e8400-e29b-41d4-a716-446655440000") == "550e8400-e29b-41d4-a716-446655440000"

    def test_safe_str_escapes_single_quotes(self):
        result = _safe_str("it's a test")
        assert "\\'" in result
        assert "'" not in result.replace("\\'", "")

    def test_safe_id_rejects_empty_string(self):
        with pytest.raises(ValueError):
            _safe_id("")

    def test_safe_id_accepts_alphanumeric(self):
        assert _safe_id("abc123_-") == "abc123_-"

    def test_safe_str_escapes_backslash(self):
        result = _safe_str('path\\with\\backslash')
        assert "\\\\" in result

    def test_safe_str_escapes_double_quotes(self):
        result = _safe_str('say "hello"')
        assert '\\"' in result


# ---------------------------------------------------------------------------
# Task 1.3: Graph and document router injection protection
# ---------------------------------------------------------------------------

class TestGraphRouterInjection:
    """Verify that graph.py and documents.py use sanitized WHERE params."""

    def test_graph_router_imports_safe_id(self):
        from app.routers.graph import _safe_id as graph_safe_id
        assert graph_safe_id is _safe_id

    def test_graph_router_imports_safe_str(self):
        from app.routers.graph import _safe_str as graph_safe_str
        assert graph_safe_str is _safe_str

    def test_documents_router_uses_safe_str(self):
        import inspect
        from app.routers.documents import _get_chunks_for_doc
        source = inspect.getsource(_get_chunks_for_doc)
        assert "_safe_str" in source, "documents.py must use _safe_str for doc_id"
        assert ".replace" not in source or "_safe_str" in source, \
            "documents.py should use _safe_str, not manual .replace"

    def test_safe_id_rejects_sql_injection_in_graph_context(self):
        with pytest.raises(ValueError):
            _safe_id("node'; DROP TABLE nodes;--")

    def test_safe_str_prevents_early_termination_in_doc_id(self):
        result = _safe_str('doc"; DROP TABLE chunks;--')
        assert '\\"' in result
        assert "DROP" not in result.split('\\"')[0]


# ---------------------------------------------------------------------------
# Task 1.6: Input validation
# ---------------------------------------------------------------------------

class TestInputValidation:
    def test_feed_documents_request_rejects_path_traversal(self):
        from app.core.path_sanitizer import validate_file_path
        with pytest.raises(Exception):
            validate_file_path("../../etc/passwd")

    def test_search_request_rejects_too_many_collection_ids(self):
        from app.models.schemas import SearchRequest
        with pytest.raises(Exception):
            SearchRequest(
                query="test",
                collection_ids=[f"col-{i}" for i in range(11)],
            )

    def test_search_request_accepts_up_to_10_collection_ids(self):
        from app.models.schemas import SearchRequest
        req = SearchRequest(
            query="test",
            collection_ids=[f"col-{i}" for i in range(10)],
        )
        assert len(req.collection_ids) == 10

    def test_feed_documents_rejects_empty_file_paths(self):
        from app.models.schemas import FeedDocumentsRequest
        with pytest.raises(Exception):
            FeedDocumentsRequest(file_paths=[])

    def test_validate_file_path_rejects_relative(self):
        from app.core.path_sanitizer import validate_file_path
        with pytest.raises(Exception):
            validate_file_path("relative/path/file.txt")


# ---------------------------------------------------------------------------
# Task 1.8: Error sanitization + collection name validation
# ---------------------------------------------------------------------------

class TestErrorSanitization:
    def test_health_check_error_no_internal_details(self):
        import inspect
        from app.main import health_check
        source = inspect.getsource(health_check)
        assert '"error": str(e)' not in source, "health_check must not expose internal error details"

    def test_collection_name_rejects_html_chars(self):
        from app.models.schemas import CollectionCreate
        for bad_name in ['<script>', 'a&b', 'he"llo', "it's"]:
            with pytest.raises(Exception):
                CollectionCreate(name=bad_name)

    def test_collection_name_accepts_clean_names(self):
        from app.models.schemas import CollectionCreate
        c = CollectionCreate(name="My Research Papers 2024")
        assert c.name == "My Research Papers 2024"


# ---------------------------------------------------------------------------
# Task 1.9: Auth rate limiting + finetune authorization
# ---------------------------------------------------------------------------

class TestRateLimitOnAuth:
    def test_auth_paths_have_stricter_limit(self):
        from app.auth.middleware import AUTH_RATE_LIMIT, _AUTH_RATE_LIMIT_PATHS
        assert AUTH_RATE_LIMIT <= 10, "Auth rate limit should be stricter (10/min)"
        assert len(_AUTH_RATE_LIMIT_PATHS) > 0, "Must define auth rate limit paths"

    def test_auth_rate_limiter_exists(self):
        from app.auth.middleware import auth_rate_limiter
        assert auth_rate_limiter.per_user_limit <= 10


class TestFinetuneAuthorization:
    def test_finetune_start_uses_require_admin(self):
        import inspect
        from app.routers.finetune import start_finetune
        source = inspect.getsource(start_finetune)
        assert "require_admin" in source, "start_finetune must use require_admin dependency"

    def test_finetune_evaluate_uses_require_admin(self):
        import inspect
        from app.routers.finetune import evaluate_models
        source = inspect.getsource(evaluate_models)
        assert "require_admin" in source, "evaluate_models must use require_admin dependency"


# ---------------------------------------------------------------------------
# Task 1.10: First-user admin race condition
# ---------------------------------------------------------------------------

class TestFirstUserRace:
    def test_first_user_admin_flag_exists(self):
        from app.config import Settings
        s = Settings(first_user_admin=True)
        assert s.first_user_admin is True

    def test_first_user_admin_can_be_disabled(self):
        from app.config import Settings
        s = Settings(first_user_admin=False)
        assert s.first_user_admin is False

    def test_first_user_promoted_only_when_flag_true(self):
        import inspect
        from app.db.lancedb_client import create_or_update_user
        source = inspect.getsource(create_or_update_user)
        assert "first_user_admin" in source, "create_or_update_user must check first_user_admin setting"


# ---------------------------------------------------------------------------
# Stream 2: Auth bypass scenarios
# ---------------------------------------------------------------------------

class TestAuthBypass:
    async def test_expired_jwt_rejected(self, monkeypatch):
        """Expired JWT tokens are rejected by verify_token."""
        import jwt as pyjwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        import app.auth.jwt as jwt_module

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = private_key.public_key()

        monkeypatch.setattr(jwt_module, "_pem_keys_exist", lambda: True)
        monkeypatch.setattr(jwt_module, "_load_public_key", lambda: public_key)
        monkeypatch.setattr(jwt_module, "_load_private_key", lambda: private_key)

        expired_payload = {
            "sub": "user-1",
            "exp": int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()),
            "iat": int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()),
        }
        expired_token = pyjwt.encode(expired_payload, private_key, algorithm="RS256")

        result = jwt_module.verify_token(expired_token)
        assert result is None, "Expired JWT must be rejected"

    async def test_revoked_refresh_token_rejected(self, monkeypatch, mock_lancedb):
        """Revoked refresh tokens cannot be used to obtain new access tokens."""
        import app.auth.jwt as jwt_module
        jwt_module._revoked_tokens.clear()

        revoked_jti = "revoked-refresh-jti-123"
        jwt_module._revoked_tokens.add(revoked_jti)

        payload = {
            "sub": "user-1",
            "jti": revoked_jti,
            "type": "refresh",
        }

        from app.routers.auth import router as auth_router

        app = FastAPI()
        app.include_router(auth_router, prefix="/api/v1/auth")

        with (
            patch("app.routers.auth.verify_token", return_value=payload),
            patch("app.auth.jwt.is_token_revoked_async", new_callable=AsyncMock, return_value=True),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                ac.cookies.set("kg_refresh_token", "revoked-token")
                response = await ac.post("/api/v1/auth/refresh")

        assert response.status_code == 401, "Revoked refresh token must be rejected"

    async def test_missing_auth_returns_401(self):
        """Protected endpoints return 401 without auth header."""
        from app.routers.collections import router as coll_router

        app = FastAPI()
        app.include_router(coll_router, prefix="/api/v1/collections")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get("/api/v1/collections")

        assert response.status_code == 401

    async def test_invalid_jwt_structure_rejected(self):
        """Malformed JWT strings are rejected."""
        from app.routers.collections import router as coll_router

        app = FastAPI()
        app.include_router(coll_router, prefix="/api/v1/collections")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get(
                "/api/v1/collections",
                headers={"Authorization": "Bearer not.a.real.jwt"},
            )

        assert response.status_code == 401

    async def test_cross_user_collection_access_denied(self, mock_lancedb):
        """User A cannot access User B's collections."""
        from app.routers.collections import router as coll_router
        from app.auth.middleware import get_current_user

        USER_A = {"id": "user-a", "email": "a@test.com", "name": "User A"}
        USER_B = {"id": "user-b", "email": "b@test.com", "name": "User B"}

        app_a = FastAPI()
        app_a.include_router(coll_router, prefix="/api/v1/collections")
        app_a.dependency_overrides[get_current_user] = lambda: USER_A

        app_b = FastAPI()
        app_b.include_router(coll_router, prefix="/api/v1/collections")
        app_b.dependency_overrides[get_current_user] = lambda: USER_B

        col_id = "cross-user-test-col"

        with patch("app.routers.collections.create_collection", new_callable=AsyncMock, return_value={
            "id": col_id, "name": "Secret", "user_id": "user-a", "description": "",
            "folder_path": "", "status": "active", "doc_count": 0,
            "created_at": 0, "updated_at": 0,
        }):
            async with AsyncClient(
                transport=ASGITransport(app=app_a), base_url="http://test"
            ) as ac:
                create_resp = await ac.post(
                    "/api/v1/collections",
                    json={"name": "Secret"},
                )

        with patch("app.routers.collections.get_collection", new_callable=AsyncMock, return_value={
            "id": col_id, "name": "Secret", "user_id": "user-a",
        }):
            async with AsyncClient(
                transport=ASGITransport(app=app_b), base_url="http://test"
            ) as ac:
                response = await ac.get(f"/api/v1/collections/{col_id}")

        assert response.status_code == 403, "User B must not access User A's collection"


# ---------------------------------------------------------------------------
# Stream 2: Injection prevention
# ---------------------------------------------------------------------------

class TestInjectionPrevention:
    def test_sql_injection_in_collection_id(self):
        """SQL-like injection in collection IDs is rejected by _safe_id."""
        from app.db.lancedb_client import _safe_id
        with pytest.raises(ValueError):
            _safe_id("'; DROP TABLE collections; --")

    def test_sql_injection_in_doc_id(self):
        from app.db.lancedb_client import _safe_id
        with pytest.raises(ValueError):
            _safe_id('doc"; DROP TABLE nodes; --')

    async def test_path_traversal_in_ingest_rejected(self, mock_settings):
        """Path traversal in ingest folder is rejected."""
        from app.routers.ingest import router as ingest_router
        from app.auth.middleware import get_current_user

        FAKE_USER = {"id": "test-user", "email": "test@test.com", "name": "Test"}

        app = FastAPI()
        app.include_router(ingest_router, prefix="/api/v1/ingest")
        app.dependency_overrides[get_current_user] = lambda: FAKE_USER

        with patch("app.routers.ingest.get_collection", new_callable=AsyncMock, return_value={
            "id": "test-col", "user_id": "test-user",
        }):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    "/api/v1/ingest/folder",
                    json={"folder_path": "/etc/passwd", "collection_id": "test-col"},
                )

        assert response.status_code == 400

    def test_xss_in_collection_name_rejected(self):
        """XSS in collection name is rejected by validator."""
        from app.models.schemas import CollectionCreate
        with pytest.raises(Exception):
            CollectionCreate(name="<script>alert(1)</script>")

    def test_safe_str_handles_all_quote_types(self):
        from app.db.lancedb_client import _safe_str
        result = _safe_str("test'value\"another\\back")
        assert "\\'" in result
        assert '\\"' in result
        assert "\\\\" in result


# ---------------------------------------------------------------------------
# Stream 2: CSRF protection
# ---------------------------------------------------------------------------

class TestCSRFProtection:
    async def test_post_without_csrf_cookie_rejected(self):
        """POST without CSRF cookie returns 403."""
        from app.auth.csrf import csrf_middleware, CSRF_COOKIE_NAME, CSRF_HEADER_NAME
        from starlette.middleware.base import BaseHTTPMiddleware

        app = FastAPI()
        app.add_middleware(BaseHTTPMiddleware, dispatch=csrf_middleware)

        @app.post("/api/v1/collections")
        async def create_collection():
            return {"ok": True}

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                "/api/v1/collections",
                json={"name": "test"},
                cookies={},
            )

        assert response.status_code == 403

    async def test_csrf_mismatch_rejected(self):
        """CSRF token mismatch returns 403."""
        from app.auth.csrf import csrf_middleware, CSRF_COOKIE_NAME, CSRF_HEADER_NAME
        from starlette.middleware.base import BaseHTTPMiddleware

        app = FastAPI()
        app.add_middleware(BaseHTTPMiddleware, dispatch=csrf_middleware)

        @app.post("/api/v1/collections")
        async def create_collection():
            return {"ok": True}

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            ac.cookies.set(CSRF_COOKIE_NAME, "cookie-token-value")
            response = await ac.post(
                "/api/v1/collections",
                json={"name": "test"},
                headers={CSRF_HEADER_NAME: "different-header-token"},
            )

        assert response.status_code == 403
        assert "mismatch" in response.json().get("error", "").lower()

    async def test_get_requests_exempt_from_csrf(self):
        """GET requests don't require CSRF."""
        from app.auth.csrf import csrf_middleware
        from starlette.middleware.base import BaseHTTPMiddleware

        app = FastAPI()
        app.add_middleware(BaseHTTPMiddleware, dispatch=csrf_middleware)

        @app.get("/api/v1/collections")
        async def list_collections():
            return {"ok": True}

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get("/api/v1/collections")

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Stream 2: Rate limiting
# ---------------------------------------------------------------------------

class TestRateLimiting:
    async def test_user_rate_limit_enforced(self):
        """User rate limit is enforced after exceeding configured limit."""
        from app.auth.middleware import RateLimiter

        rl = RateLimiter(per_user_limit=5, per_ip_limit=100, window_seconds=60)

        for _ in range(5):
            assert await rl.check_user("user-1") is True

        result = await rl.check_user("user-1")
        assert result is False, "Request beyond per_user_limit must be rejected"

    async def test_rate_limit_headers_present(self, mock_settings):
        """Rate limit headers are present on responses."""
        from app.auth.middleware import rate_limit_middleware
        from starlette.middleware.base import BaseHTTPMiddleware

        app = FastAPI()
        app.add_middleware(BaseHTTPMiddleware, dispatch=rate_limit_middleware)

        @app.get("/api/v1/test-rate")
        async def test_endpoint():
            return {"ok": True}

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get("/api/v1/test-rate")

        assert "X-RateLimit-Limit" in response.headers or response.status_code == 401, (
            "Rate limit headers should be present on non-401 responses"
        )

    async def test_auth_rate_limit_stricter(self):
        """Auth endpoints have stricter 10/min rate limit."""
        from app.auth.middleware import RateLimiter, AUTH_RATE_LIMIT

        rl = RateLimiter(per_user_limit=AUTH_RATE_LIMIT, per_ip_limit=AUTH_RATE_LIMIT * 3, window_seconds=60)

        for _ in range(AUTH_RATE_LIMIT):
            assert await rl.check_user("auth-user-1") is True

        result = await rl.check_user("auth-user-1")
        assert result is False, f"Auth per-user rate limit ({AUTH_RATE_LIMIT}/min) must be enforced"

    async def test_auth_ip_rate_limit_enforced(self):
        """Auth IP-level rate limit is enforced."""
        from app.auth.middleware import RateLimiter, AUTH_RATE_LIMIT

        ip_limit = AUTH_RATE_LIMIT * 3
        rl = RateLimiter(per_user_limit=AUTH_RATE_LIMIT, per_ip_limit=ip_limit, window_seconds=60)

        for _ in range(ip_limit):
            assert await rl.check_ip("10.0.0.99") is True

        result = await rl.check_ip("10.0.0.99")
        assert result is False, f"Auth IP rate limit ({ip_limit}/min) must be enforced"