"""Google OAuth 2.0 token validation."""

import httpx
from typing import Optional
from app.config import get_settings

settings = get_settings()


async def validate_google_id_token(id_token: str) -> Optional[dict]:
    """Validate a Google ID token and return user info."""
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
