"""FastAPI middleware for authentication and rate limiting."""

from fastapi import Request, Response, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from collections import defaultdict
from datetime import datetime, timezone
import time
import logging

from app.auth.jwt import verify_token
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

NO_AUTH_PATHS = {
    "/health",
    "/api/v1/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/auth/google",
    "/api/v1/auth/refresh",
}


_RATE_LIMIT_EXEMPT = NO_AUTH_PATHS | {"/metrics"}


class RateLimiter:
    """Sliding-window rate limiter (in-memory; for multi-replica use Redis instead)."""

    def __init__(self, per_user_limit: int = 60, per_ip_limit: int = 200, window_seconds: int = 60):
        self.per_user_limit = per_user_limit
        self.per_ip_limit = per_ip_limit
        self.window = window_seconds
        self._counts: dict[str, list[float]] = defaultdict(list)

    def _check(self, key: str, limit: int) -> bool:
        now = time.time()
        window_start = now - self.window
        hits = self._counts[key]
        # Evict expired entries
        while hits and hits[0] <= window_start:
            hits.pop(0)
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True

    def check_user(self, user_id: str) -> bool:
        return self._check(f"u:{user_id}", self.per_user_limit)

    def check_ip(self, ip: str) -> bool:
        return self._check(f"ip:{ip}", self.per_ip_limit)


rate_limiter = RateLimiter(
    per_user_limit=settings.rate_limit_per_user,
    per_ip_limit=settings.rate_limit_per_user * 3,
    window_seconds=settings.rate_limit_window_seconds,
)


def _429_response(msg: str) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"error": msg, "code": "RATE_LIMITED"},
        headers={
            "Retry-After": str(settings.rate_limit_window_seconds),
            "X-RateLimit-Limit": str(settings.rate_limit_per_user),
        },
    )


async def rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    if path in _RATE_LIMIT_EXEMPT or path.startswith("/ws"):
        return await call_next(request)

    # Per-IP check (always enforced)
    client_ip = request.client.host if request.client else "unknown"
    if not rate_limiter.check_ip(client_ip):
        return _429_response("IP rate limit exceeded")

    # Per-user check (only for authenticated requests; user_id set by auth_middleware)
    user_id = getattr(request.state, "user_id", None)
    if user_id and not rate_limiter.check_user(user_id):
        return _429_response("User rate limit exceeded")

    return await call_next(request)


async def auth_middleware(request: Request, call_next):
    if request.url.path in NO_AUTH_PATHS:
        return await call_next(request)

    if request.url.path.startswith("/ws"):
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"error": "Missing or invalid Authorization header", "code": "UNAUTHORIZED"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth_header[7:]
    payload = verify_token(token)

    if payload is None:
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid or expired token", "code": "UNAUTHORIZED"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    request.state.user_id = payload.get("sub")
    request.state.user_email = payload.get("email")
    request.state.user_name = payload.get("name")

    return await call_next(request)


async def get_current_user(request: Request) -> dict:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "id": user_id,
        "email": getattr(request.state, "user_email", ""),
        "name": getattr(request.state, "user_name", ""),
    }
