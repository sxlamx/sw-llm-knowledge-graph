"""Tests for CSRF protection middleware."""

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import AsyncClient, ASGITransport
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth.csrf import (
    csrf_middleware,
    generate_csrf_token,
    set_csrf_cookie,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    CSRF_EXEMPT_PATHS,
    SAFE_METHODS,
)


def _csrf_app():
    app = FastAPI()

    @app.post("/api/v1/protected")
    async def protected():
        return {"ok": True}

    @app.get("/api/v1/protected")
    async def protected_get():
        return {"ok": True}

    app.add_middleware(BaseHTTPMiddleware, dispatch=csrf_middleware)
    return app


class TestCSRFMiddleware:
    async def test_safe_methods_pass_without_csrf(self):
        app = _csrf_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get("/api/v1/protected")
        assert response.status_code == 200

    async def test_post_without_csrf_cookie_returns_403(self):
        app = _csrf_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post("/api/v1/protected")
        assert response.status_code == 403
        assert "CSRF" in response.json().get("error", "")

    async def test_post_with_matching_csrf_passes(self):
        app = _csrf_app()
        token = generate_csrf_token()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            ac.cookies.set(CSRF_COOKIE_NAME, token)
            response = await ac.post(
                "/api/v1/protected",
                headers={CSRF_HEADER_NAME: token},
            )
        assert response.status_code == 200

    async def test_post_with_mismatched_csrf_returns_403(self):
        app = _csrf_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            ac.cookies.set(CSRF_COOKIE_NAME, "cookie_token")
            response = await ac.post(
                "/api/v1/protected",
                headers={CSRF_HEADER_NAME: "header_token"},
            )
        assert response.status_code == 403
        assert "mismatch" in response.json().get("error", "").lower()

    async def test_exempt_paths_bypass_csrf(self):
        for path in CSRF_EXEMPT_PATHS:
            exempt_app = FastAPI()
            exempt_app.add_middleware(BaseHTTPMiddleware, dispatch=csrf_middleware)

            @exempt_app.post(path)
            async def handler():
                return {"ok": True}

            async with AsyncClient(
                transport=ASGITransport(app=exempt_app), base_url="http://test"
            ) as ac:
                response = await ac.post(path)
            assert response.status_code == 200, f"CSRF should exempt {path}"


class TestCSRFTokenGeneration:
    def test_generate_csrf_token_returns_string(self):
        token = generate_csrf_token()
        assert isinstance(token, str)
        assert len(token) > 20

    def test_generate_csrf_token_unique_each_call(self):
        tokens = {generate_csrf_token() for _ in range(10)}
        assert len(tokens) == 10


class TestCSRFCookie:
    def test_set_csrf_cookie_sets_correct_name(self):
        from starlette.responses import Response as StarletteResponse

        response = StarletteResponse()
        token = "test-csrf-token"
        set_csrf_cookie(response, token)

        set_cookie_header = response.headers.get("set-cookie", "")
        assert CSRF_COOKIE_NAME in set_cookie_header
        assert token in set_cookie_header