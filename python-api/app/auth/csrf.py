"""CSRF protection middleware using double-submit cookie pattern."""

from fastapi import Request, Response, HTTPException
from fastapi.responses import JSONResponse
import secrets
import logging

logger = logging.getLogger(__name__)

CSRF_COOKIE_NAME = "kg_csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


async def csrf_middleware(request: Request, call_next):
    """CSRF protection middleware.
    
    For safe methods (GET, HEAD, OPTIONS):
      - Pass through without checking
    
    For unsafe methods (POST, PUT, DELETE, PATCH):
      - Require X-CSRF-Token header matching the csrf_token cookie
      - Return 403 if token missing or mismatched
    """
    if request.method in SAFE_METHODS:
        return await call_next(request)
    
    csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME)
    csrf_header = request.headers.get(CSRF_HEADER_NAME)
    
    if not csrf_cookie:
        return JSONResponse(
            status_code=403,
            content={
                "error": "CSRF token missing",
                "code": "CSRF_MISSING",
            },
        )
    
    if not csrf_header:
        return JSONResponse(
            status_code=403,
            content={
                "error": "CSRF token header missing",
                "code": "CSRF_MISSING_HEADER",
            },
        )
    
    if not secrets.compare_digest(csrf_cookie, csrf_header):
        logger.warning(
            f"CSRF token mismatch for {request.method} {request.url.path}"
        )
        return JSONResponse(
            status_code=403,
            content={
                "error": "CSRF token mismatch",
                "code": "CSRF_MISMATCH",
            },
        )
    
    return await call_next(request)


def generate_csrf_token() -> str:
    """Generate a new CSRF token."""
    return secrets.token_urlsafe(32)


def set_csrf_cookie(response: Response, token: str) -> Response:
    """Set CSRF token cookie on response."""
    from app.config import get_settings
    settings = get_settings()
    
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=False,  # Must be readable by JavaScript
        secure=settings.cookie_secure,
        samesite="strict",
        max_age=3600,  # 1 hour
        path="/",
    )
    return response
