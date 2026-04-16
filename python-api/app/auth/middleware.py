"""FastAPI middleware for authentication and rate limiting."""

from fastapi import Request, Response, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from collections import defaultdict
from datetime import datetime, timezone
import asyncio
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
    "/api/v1/auth/google/redirect",
    "/api/v1/auth/google/exchange",
    "/api/v1/auth/refresh",
}

_AUTH_RATE_LIMIT_PATHS = {
    "/api/v1/auth/google",
    "/api/v1/auth/google/redirect",
    "/api/v1/auth/google/exchange",
    "/api/v1/auth/refresh",
}

AUTH_RATE_LIMIT = 10


_RATE_LIMIT_EXEMPT = NO_AUTH_PATHS | {"/metrics"}


class RateLimiter:
    """Sliding-window rate limiter (in-memory; for multi-replica use Redis instead).

    Uses asyncio.Lock to prevent concurrent request races from interleaving
    the check-and-append sequence under async concurrency.
    """

    def __init__(self, per_user_limit: int = 60, per_ip_limit: int = 200, window_seconds: int = 60):
        self.per_user_limit = per_user_limit
        self.per_ip_limit = per_ip_limit
        self.window = window_seconds
        self._counts: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def _check(self, key: str, limit: int) -> bool:
        async with self._lock:
            now = time.time()
            window_start = now - self.window
            hits = self._counts[key]
            while hits and hits[0] <= window_start:
                hits.pop(0)
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    async def check_user(self, user_id: str) -> bool:
        return await self._check(f"u:{user_id}", self.per_user_limit)

    async def check_ip(self, ip: str) -> bool:
        return await self._check(f"ip:{ip}", self.per_ip_limit)


class RedisRateLimiter:
    """Sliding-window rate limiter backed by Redis (for multi-replica deployments).

    Uses a sorted-set per key: score = timestamp, member = unique request ID.
    ``ZREMRANGEBYSCORE`` evicts expired entries; ``ZCARD`` returns the current
    count; ``ZADD`` records the new request.  All three commands run inside a
    Redis pipeline for atomicity.
    """

    def __init__(
        self,
        redis_url: str,
        per_user_limit: int = 60,
        per_ip_limit: int = 200,
        window_seconds: int = 60,
    ):
        import redis

        self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self.per_user_limit = per_user_limit
        self.per_ip_limit = per_ip_limit
        self.window = window_seconds

    def _check(self, key: str, limit: int) -> bool:
        now = time.time()
        window_start = now - self.window
        member = f"{now}:{id(key)}:{key}"
        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(key, "-inf", window_start)
        pipe.zcard(key)
        pipe.zadd(key, {member: now})
        pipe.expire(key, self.window + 1)
        results = pipe.execute()
        current_count = results[1]
        return current_count < limit

    def check_user(self, user_id: str) -> bool:
        return self._check(f"rl:u:{user_id}", self.per_user_limit)

    def check_ip(self, ip: str) -> bool:
        return self._check(f"rl:ip:{ip}", self.per_ip_limit)


def _create_rate_limiter() -> RateLimiter | RedisRateLimiter:
    redis_url = settings.redis_url
    if redis_url:
        try:
            limiter = RedisRateLimiter(
                redis_url=redis_url,
                per_user_limit=settings.rate_limit_per_user,
                per_ip_limit=settings.rate_limit_per_user * 3,
                window_seconds=settings.rate_limit_window_seconds,
            )
            limiter._redis.ping()
            logger.info("Rate limiter: Redis backend connected (%s)", redis_url)
            return limiter
        except Exception as exc:
            logger.warning("Redis rate limiter init failed, falling back to in-memory: %s", exc)

    return RateLimiter(
        per_user_limit=settings.rate_limit_per_user,
        per_ip_limit=settings.rate_limit_per_user * 3,
        window_seconds=settings.rate_limit_window_seconds,
    )


rate_limiter = _create_rate_limiter()
auth_rate_limiter = RateLimiter(
    per_user_limit=AUTH_RATE_LIMIT,
    per_ip_limit=AUTH_RATE_LIMIT * 3,
    window_seconds=settings.rate_limit_window_seconds,
)


def _429_response(msg: str) -> JSONResponse:
    headers = {
        "Retry-After": str(settings.rate_limit_window_seconds),
        "X-RateLimit-Limit": str(settings.rate_limit_per_user),
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": str(int(time.time()) + settings.rate_limit_window_seconds),
    }
    return JSONResponse(
        status_code=429,
        content={"error": msg, "code": "RATE_LIMITED"},
        headers=headers,
    )


async def rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    if path in _RATE_LIMIT_EXEMPT or path.startswith("/ws"):
        return await call_next(request)

    client_ip = request.client.host if request.client else "unknown"

    if path in _AUTH_RATE_LIMIT_PATHS:
        if not await auth_rate_limiter.check_ip(client_ip):
            return _429_response("Auth IP rate limit exceeded")
        user_id = getattr(request.state, "user_id", None)
        if user_id and not await auth_rate_limiter.check_user(user_id):
            return _429_response("Auth user rate limit exceeded")
    else:
        if not await rate_limiter.check_ip(client_ip):
            return _429_response("IP rate limit exceeded")
        user_id = getattr(request.state, "user_id", None)
        if user_id and not await rate_limiter.check_user(user_id):
            return _429_response("User rate limit exceeded")

    response = await call_next(request)

    remaining = settings.rate_limit_per_user
    if user_id:
        user_key = f"u:{user_id}"
        if isinstance(rate_limiter, RateLimiter):
            async with rate_limiter._lock:
                hits = rate_limiter._counts.get(user_key, [])
                remaining = max(0, settings.rate_limit_per_user - len(hits))
        else:
            remaining = max(0, settings.rate_limit_per_user)
    ip_remaining = settings.rate_limit_per_user * 3
    if isinstance(rate_limiter, RateLimiter):
        async with rate_limiter._lock:
            ip_key = f"ip:{client_ip}"
            ip_hits = rate_limiter._counts.get(ip_key, [])
            ip_remaining = max(0, rate_limiter.per_ip_limit - len(ip_hits))
    reset_time = int(time.time()) + settings.rate_limit_window_seconds

    effective_remaining = min(remaining, ip_remaining) if user_id else ip_remaining

    response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_per_user)
    response.headers["X-RateLimit-Remaining"] = str(effective_remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_time)

    return response


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

    jti = payload.get("jti")
    if jti:
        from app.db.lancedb_client import is_token_revoked
        if await is_token_revoked(jti):
            return JSONResponse(
                status_code=401,
                content={"error": "Token has been revoked", "code": "UNAUTHORIZED"},
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


async def require_admin(request: Request) -> dict:
    """FastAPI dependency that requires the caller to have role='admin'."""
    from app.db.lancedb_client import get_user_by_id
    user = await get_current_user(request)
    db_user = await get_user_by_id(user["id"])
    if not db_user or db_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return {**user, "role": "admin"}
