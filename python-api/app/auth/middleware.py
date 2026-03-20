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


class RateLimiter:
    def __init__(self, per_user_limit: int = 60, window_seconds: int = 60):
        self.per_user_limit = per_user_limit
        self.window = window_seconds
        self.user_counts: dict[str, list[float]] = defaultdict(list)
        self._lock = None

    async def check(self, user_id: str) -> bool:
        now = time.time()
        window_start = now - self.window
        self.user_counts[user_id] = [t for t in self.user_counts[user_id] if t > window_start]
        if len(self.user_counts[user_id]) >= self.per_user_limit:
            return False
        self.user_counts[user_id].append(now)
        return True

    async def check_ip(self, ip: str) -> bool:
        now = time.time()
        window_start = now - self.window
        self.user_counts[ip] = [t for t in self.user_counts[ip] if t > window_start]
        if len(self.user_counts[ip]) >= self.per_user_limit * 3:
            return False
        self.user_counts[ip].append(now)
        return True


rate_limiter = RateLimiter(
    per_user_limit=settings.rate_limit_per_user,
    window_seconds=settings.rate_limit_window_seconds,
)


async def rate_limit_middleware(request: Request, call_next):
    if request.url.path in NO_AUTH_PATHS or request.url.path.startswith("/ws"):
        return await call_next(request)

    if request.url.path.startswith("/metrics"):
        return await call_next(request)

    client_ip = request.client.host if request.client else "unknown"
    await rate_limiter.check_ip(client_ip)

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
