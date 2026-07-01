"""Google OAuth 2.0 token validation.

Uses the official google-auth library for ID token verification.
Falls back to httpx tokeninfo endpoint if google-auth is not installed.
"""

import asyncio
import logging
from typing import Optional

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


async def validate_google_id_token(id_token: str) -> Optional[dict]:
    """Validate a Google ID token and return user info.

    Primary path: google.oauth2.id_token.verify_oauth2_token (spec requirement).
    Fallback: httpx call to tokeninfo endpoint.
    """
    try:
        from google.oauth2 import id_token as gid_token
        from google.auth.transport.requests import Request

        loop = asyncio.get_running_loop()
        idinfo = await loop.run_in_executor(
            None,
            lambda: gid_token.verify_oauth2_token(
                id_token,
                Request(),
                settings.google_client_id,
            ),
        )
        if idinfo.get("aud") != settings.google_client_id:
            logger.warning("Google token audience mismatch")
            return None
        return {
            "google_sub": idinfo["sub"],
            "email": idinfo.get("email", ""),
            "name": idinfo.get("name", ""),
            "avatar_url": idinfo.get("picture"),
        }
    except ImportError:
        logger.warning("google-auth not installed, falling back to httpx tokeninfo")
        return await _validate_via_httpx(id_token)
    except ValueError as e:
        logger.warning(f"Google ID token validation failed: {e}")
        return None
    except Exception as e:
        logger.warning(f"Google ID token validation error: {e}")
        return await _validate_via_httpx(id_token)


async def _validate_via_httpx(id_token: str) -> Optional[dict]:
    """Fallback: validate via Google's tokeninfo HTTP endpoint."""
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": id_token},
                timeout=10.0,
            )
            if response.status_code != 200:
                return None
            payload = response.json()
            if payload.get("aud") != settings.google_client_id:
                return None
            return {
                "google_sub": payload["sub"],
                "email": payload.get("email", ""),
                "name": payload.get("name", ""),
                "avatar_url": payload.get("picture"),
            }
    except Exception:
        return None


def build_google_auth_url(state: str = "") -> str:
    """Build the Google OAuth authorization URL."""
    import urllib.parse

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": "postmessage",
        "response_type": "id_token",
        "scope": "openid email profile",
        "nonce": state,
        "hd": "default",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"