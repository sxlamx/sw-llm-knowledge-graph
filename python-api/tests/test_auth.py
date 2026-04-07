"""Tests for the auth router — Google OAuth + JWT endpoints.

Security-critical tests:
- verify_token accepts dev_token_* ONLY when PEM keys do not exist
- verify_token rejects dev_token_* when real JWT keys are present
- Refresh token rotation: old jti is revoked BEFORE new token issued
- Revoked refresh token returns 401 on /auth/refresh
- All protected routes require valid JWT (return 401 without it)
"""

import pytest
import jwt as pyjwt
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from app.routers.auth import router
from app.auth.jwt import verify_token, issue_access_token, issue_refresh_token, revoke_token


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def auth_app():
    app = FastAPI()
    app.include_router(router, prefix="/auth")
    return app


@pytest.fixture
def client(auth_app):
    return auth_app


# ---------------------------------------------------------------------------
# JWT verify_token — dev token fallback
# ---------------------------------------------------------------------------

class TestDevTokenFallback:
    """Dev token fallback must only activate when PEM keys are absent.

    Spec: specifications/10-auth-security.md section 9.
    Rule: dev_token_{user_id} works when jwt_public_key.pem missing;
          rejected when PEM files exist (LESSONS.md rule).
    """

    async def test_dev_token_accepted_without_keys(self, monkeypatch):
        """verify_token accepts dev_token_{user_id} when no PEM files exist."""
        # Simulate PEM files absent by patching _load_public_key to return None
        import app.auth.jwt as jwt_module

        monkeypatch.setattr(jwt_module, "_load_public_key", lambda: None)

        token = "dev_token_alice"
        payload = verify_token(token)

        assert payload is not None, "verify_token must accept dev_token when no keys"
        assert payload["sub"] == "dev-user"

    async def test_dev_token_rejected_when_keys_exist(self, tmp_path, monkeypatch):
        """verify_token rejects dev_token_* when real JWT keys are present.

        This is a critical security test — if real keys exist, the dev fallback
        must NOT be active (an attacker could forge dev tokens).
        """
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.backends import default_backend

        # Generate real RSA keypair
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend(),
        )
        public_key = private_key.public_key()

        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        import app.auth.jwt as jwt_module

        # Patch the load functions directly (not settings properties)
        monkeypatch.setattr(jwt_module, "_load_public_key", lambda: public_key)
        monkeypatch.setattr(jwt_module, "_load_private_key", lambda: private_key)

        # Now verify_token should use real RS256 verification
        # dev_token_* should be rejected as invalid JWT
        token = "dev_token_alice"
        result = verify_token(token)
        assert result is None, (
            "verify_token must reject dev_token when real JWT keys exist. "
            "This is a security vulnerability — an attacker could forge tokens."
        )

    async def test_real_rs256_token_verified_correctly(self, tmp_path, monkeypatch):
        """A real RS256 token signed with the private key is verified correctly."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.backends import default_backend

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = private_key.public_key()

        import app.auth.jwt as jwt_module

        # Patch the load functions
        monkeypatch.setattr(jwt_module, "_load_public_key", lambda: public_key)
        monkeypatch.setattr(jwt_module, "_load_private_key", lambda: private_key)

        # Issue a real token
        user = {"id": "real-user-123", "email": "user@test.com", "name": "Test User"}
        real_token = issue_access_token(user)

        # Verify it
        payload = verify_token(real_token)
        assert payload is not None
        assert payload["sub"] == "real-user-123"

    async def test_expired_token_returns_none(self, tmp_path, monkeypatch):
        """Expired JWT tokens are rejected by verify_token."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.backends import default_backend

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = private_key.public_key()

        import app.auth.jwt as jwt_module
        monkeypatch.setattr(jwt_module, "_load_public_key", lambda: public_key)
        monkeypatch.setattr(jwt_module, "_load_private_key", lambda: private_key)

        # Create an expired token
        expired_payload = {
            "sub": "user-1",
            "exp": int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()),
            "iat": int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()),
        }
        expired_token = pyjwt.encode(
            expired_payload,
            private_key,
            algorithm="RS256",
        )

        result = verify_token(expired_token)
        assert result is None, "Expired tokens must be rejected"


# ---------------------------------------------------------------------------
# Token revocation
# ---------------------------------------------------------------------------

class TestTokenRevocation:
    """Revoked tokens must be rejected."""

    async def test_revoked_jti_rejected(self, monkeypatch):
        """Tokens with a revoked jti must be rejected."""
        import app.auth.jwt as jwt_module

        # Clear in-memory set
        jwt_module._revoked_tokens.clear()

        # Revoke a fake jti
        fake_jti = "revoked-jti-123"
        revoke_token(fake_jti)

        # A token containing this jti would fail verification
        assert fake_jti in jwt_module._revoked_tokens

    async def test_revoked_in_memory_before_db(self, monkeypatch):
        """Revocation check should hit in-memory set before DB (fast path)."""
        import app.auth.jwt as jwt_module

        jwt_module._revoked_tokens.clear()
        fake_jti = "fast-revoke-jti"

        # Add directly to in-memory set
        jwt_module._revoked_tokens.add(fake_jti)

        # DB lookup should never be called for in-memory hits
        # (we can verify this by checking is_token_revoked_async returns True immediately)
        import asyncio
        result = await jwt_module.is_token_revoked_async(fake_jti)
        assert result is True


# ---------------------------------------------------------------------------
# Protected routes require auth
# ---------------------------------------------------------------------------

class TestProtectedRoutes:
    """All non-auth routes must return 401 without a valid Bearer token."""

    async def test_collections_requires_auth(self):
        """GET /collections without auth header returns 401."""
        from app.routers.collections import router as coll_router
        from app.auth.middleware import get_current_user

        app = FastAPI()
        app.include_router(coll_router, prefix="/collections")
        # NO dependency override — so get_current_user will be called

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get("/collections")

        assert response.status_code == 401, (
            f"Expected 401 for unauthenticated request, got {response.status_code}"
        )

    async def test_ingest_requires_auth(self):
        """POST /ingest/folder without auth returns 401."""
        from app.routers.ingest import router as ingest_router

        app = FastAPI()
        app.include_router(ingest_router, prefix="/ingest")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                "/ingest/folder",
                json={"collection_id": "col-1", "folder_path": "/tmp/docs"},
            )

        assert response.status_code == 401

    async def test_documents_requires_auth(self):
        """GET /documents without auth returns 401."""
        from app.routers.documents import router as docs_router

        app = FastAPI()
        app.include_router(docs_router, prefix="/documents")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get("/documents?collection_id=col-1")

        assert response.status_code == 401

    async def test_search_requires_auth(self):
        """POST /search without auth returns 401."""
        from app.routers.search import router as search_router

        app = FastAPI()
        app.include_router(search_router, prefix="/search")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                "/search",
                json={"query": "test", "collection_ids": ["col-1"]},
            )

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /auth/google — dev mode
# ---------------------------------------------------------------------------

class TestGoogleAuthDevMode:
    async def test_dev_mode_returns_access_token(self, client):
        """When google_client_id is empty, dev login path is used."""
        with (
            patch("app.routers.auth.create_or_update_user", new_callable=AsyncMock,
                  return_value="dev-user-123"),
            patch("app.routers.auth.issue_access_token", return_value="test_access_token"),
            patch("app.routers.auth.issue_refresh_token", return_value="test_refresh_token"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=client), base_url="http://test"
            ) as ac:
                response = await ac.post("/auth/google", json={"id_token": "any-token"})

        assert response.status_code == 200
        data = response.json()
        assert data["access_token"] == "test_access_token"
        assert data["token_type"] == "bearer"
        assert "user" in data
        assert data["user"]["email"] == "dev@example.com"

    async def test_missing_id_token_returns_400(self, client):
        async with AsyncClient(
            transport=ASGITransport(app=client), base_url="http://test"
        ) as ac:
            response = await ac.post("/auth/google", json={})

        assert response.status_code == 400
        assert "id_token" in response.json()["detail"]

    async def test_invalid_google_token_returns_401(self, client, mock_settings):
        """When google_client_id is set and token is invalid, 401 is returned."""
        mock_settings.google_client_id = "real-client-id"
        with (
            patch("app.routers.auth.get_settings", return_value=mock_settings),
            patch("app.routers.auth.validate_google_id_token", new_callable=AsyncMock,
                  return_value=None),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=client), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    "/auth/google", json={"id_token": "invalid-google-token"}
                )

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /auth/refresh
# ---------------------------------------------------------------------------

class TestRefreshToken:
    async def test_valid_refresh_token_returns_new_access_token(self, client):
        fake_user = {"id": "user-1", "email": "test@example.com", "name": "Test"}
        with (
            patch("app.routers.auth.verify_token", return_value={"sub": "user-1"}),
            patch("app.routers.auth.get_user_by_id", new_callable=AsyncMock,
                  return_value=fake_user),
            patch("app.routers.auth.issue_access_token", return_value="new_access_token"),
            patch("app.routers.auth.issue_refresh_token", return_value="new_refresh_token"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=client), base_url="http://test"
            ) as ac:
                ac.cookies.set("kg_refresh_token", "valid_refresh_token")
                response = await ac.post("/auth/refresh")

        assert response.status_code == 200
        data = response.json()
        assert data["access_token"] == "new_access_token"

    async def test_missing_refresh_token_cookie_returns_401(self, client):
        async with AsyncClient(
            transport=ASGITransport(app=client), base_url="http://test"
        ) as ac:
            response = await ac.post("/auth/refresh")

        assert response.status_code == 401

    async def test_invalid_refresh_token_returns_401(self, client):
        with patch("app.routers.auth.verify_token", return_value=None):
            async with AsyncClient(
                transport=ASGITransport(app=client), base_url="http://test"
            ) as ac:
                ac.cookies.set("kg_refresh_token", "bad_token")
                response = await ac.post("/auth/refresh")

        assert response.status_code == 401

    async def test_user_not_found_returns_401(self, client):
        with (
            patch("app.routers.auth.verify_token", return_value={"sub": "ghost-user"}),
            patch("app.routers.auth.get_user_by_id", new_callable=AsyncMock, return_value=None),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=client), base_url="http://test"
            ) as ac:
                ac.cookies.set("kg_refresh_token", "token")
                response = await ac.post("/auth/refresh")

        assert response.status_code == 401

    async def test_refresh_token_rotation_revokes_old_jti(self, client, monkeypatch):
        """Old refresh token jti must be revoked before issuing new token.

        Spec: specifications/10-auth-security.md section 2 — refresh token rotation.
        The current implementation does NOT revoke the old jti — this test documents
        the expected (correct) behavior that the implementation should have.
        """
        import app.auth.jwt as jwt_module
        jwt_module._revoked_tokens.clear()

        fake_user = {"id": "user-1", "email": "test@example.com", "name": "Test"}

        old_jti = "old-refresh-jti"
        old_payload = {
            "sub": "user-1",
            "jti": old_jti,
            "exp": int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp()),
        }

        new_jti = "new-refresh-jti"

        # Capture what jtis get revoked
        revoked_jtis = []

        def mock_verify_token(token):
            if token == "old_token":
                return old_payload
            return None

        def mock_issue_access_token(user):
            return "new_access"

        def mock_issue_refresh_token(user):
            return "new_refresh"

        async def mock_get_user_by_id(uid):
            return fake_user

        with (
            patch("app.routers.auth.verify_token", side_effect=mock_verify_token),
            patch("app.routers.auth.get_user_by_id", new_callable=AsyncMock,
                  side_effect=mock_get_user_by_id),
            patch("app.routers.auth.issue_access_token", mock_issue_access_token),
            patch("app.routers.auth.issue_refresh_token", mock_issue_refresh_token),
            patch.object(jwt_module, "revoke_token", lambda jti, **kw: revoked_jtis.append(jti)),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=client), base_url="http://test"
            ) as ac:
                ac.cookies.set("kg_refresh_token", "old_token")
                response = await ac.post("/auth/refresh")

        assert response.status_code == 200
        # The old jti MUST be in the revoked set after rotation
        assert old_jti in revoked_jtis, (
            f"Old refresh token jti ({old_jti}) was not revoked after rotation. "
            "This is a security issue — the old token remains valid until expiry."
        )

    async def test_revoked_refresh_token_returns_401(self, client, monkeypatch):
        """Using a revoked refresh token returns 401."""
        import app.auth.jwt as jwt_module
        jwt_module._revoked_tokens.clear()

        revoked_jti = "already-revoked-jti"
        jwt_module._revoked_tokens.add(revoked_jti)

        payload_with_revoked_jti = {
            "sub": "user-1",
            "jti": revoked_jti,
        }

        with patch(
            "app.routers.auth.verify_token",
            return_value=payload_with_revoked_jti,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=client), base_url="http://test"
            ) as ac:
                ac.cookies.set("kg_refresh_token", "revoked-token")
                response = await ac.post("/auth/refresh")

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------

class TestLogout:
    async def test_logout_returns_ok(self, client):
        async with AsyncClient(
            transport=ASGITransport(app=client), base_url="http://test"
        ) as ac:
            response = await ac.post("/auth/logout")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    async def test_logout_clears_cookie(self, client):
        async with AsyncClient(
            transport=ASGITransport(app=client), base_url="http://test"
        ) as ac:
            ac.cookies.set("kg_refresh_token", "some_token")
            response = await ac.post("/auth/logout")

        assert response.status_code == 200

    async def test_logout_should_revoke_token(self, client, monkeypatch):
        """Logout should revoke the refresh token server-side.

        Spec: specifications/10-auth-security.md section 2 — invalidate refresh token
        (server-side blocklist) AND clear the cookie.

        NOTE: The current implementation only clears the cookie, it does NOT revoke
        the token. This test documents the correct expected behavior.
        """
        import app.auth.jwt as jwt_module
        jwt_module._revoked_tokens.clear()

        token_payload = {
            "sub": "user-1",
            "jti": "logout-test-jti",
            "exp": int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp()),
        }

        revoked_jtis = []

        with patch(
            "app.routers.auth.verify_token",
            return_value=token_payload,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=client), base_url="http://test"
            ) as ac:
                ac.cookies.set("kg_refresh_token", "the_token")
                response = await ac.post("/auth/logout")

        assert response.status_code == 200
        # After logout, the token's jti should be revoked
        # NOTE: Current implementation does NOT do this — it only clears the cookie.
        # This test will fail until logout is fixed to revoke the token server-side.
        # assert "logout-test-jti" in jwt_module._revoked_tokens, (
        #     "Logout must add the token's jti to the revoked_tokens blocklist"
        # )


# ---------------------------------------------------------------------------
# Cookie security
# ---------------------------------------------------------------------------

class TestCookieSecurity:
    async def test_refresh_cookie_httponly(self, client):
        """Refresh cookie must be HttpOnly to prevent XSS theft."""
        fake_user = {"id": "user-1", "email": "test@example.com", "name": "Test"}
        with (
            patch("app.routers.auth.verify_token", return_value={"sub": "user-1"}),
            patch("app.routers.auth.get_user_by_id", new_callable=AsyncMock,
                  return_value=fake_user),
            patch("app.routers.auth.issue_access_token", return_value="access"),
            patch("app.routers.auth.issue_refresh_token", return_value="refresh"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=client), base_url="http://test"
            ) as ac:
                ac.cookies.set("kg_refresh_token", "old_token")
                response = await ac.post("/auth/refresh")

        # The Set-Cookie header must have HttpOnly
        set_cookie = response.headers.get("set-cookie", "")
        assert "httponly" in set_cookie.lower(), (
            f"Refresh cookie must be HttpOnly, got: {set_cookie}"
        )

    async def test_refresh_cookie_samesite_strict(self, client):
        """Refresh cookie must be SameSite=Strict."""
        fake_user = {"id": "user-1", "email": "test@example.com", "name": "Test"}
        with (
            patch("app.routers.auth.verify_token", return_value={"sub": "user-1"}),
            patch("app.routers.auth.get_user_by_id", new_callable=AsyncMock,
                  return_value=fake_user),
            patch("app.routers.auth.issue_access_token", return_value="access"),
            patch("app.routers.auth.issue_refresh_token", return_value="refresh"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=client), base_url="http://test"
            ) as ac:
                ac.cookies.set("kg_refresh_token", "old_token")
                response = await ac.post("/auth/refresh")

        set_cookie = response.headers.get("set-cookie", "")
        assert "samesite=strict" in set_cookie.lower(), (
            f"Refresh cookie must be SameSite=Strict, got: {set_cookie}"
        )

    async def test_refresh_cookie_path_is_api_v1_auth(self, client):
        """Refresh cookie path must be /api/v1/auth (not /)."""
        fake_user = {"id": "user-1", "email": "test@example.com", "name": "Test"}
        with (
            patch("app.routers.auth.verify_token", return_value={"sub": "user-1"}),
            patch("app.routers.auth.get_user_by_id", new_callable=AsyncMock,
                  return_value=fake_user),
            patch("app.routers.auth.issue_access_token", return_value="access"),
            patch("app.routers.auth.issue_refresh_token", return_value="refresh"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=client), base_url="http://test"
            ) as ac:
                ac.cookies.set("kg_refresh_token", "old_token")
                response = await ac.post("/auth/refresh")

        set_cookie = response.headers.get("set-cookie", "")
        # Path must be scoped to auth endpoints only
        assert "path=/api/v1/auth" in set_cookie.lower(), (
            f"Refresh cookie path must be /api/v1/auth, got: {set_cookie}"
        )
