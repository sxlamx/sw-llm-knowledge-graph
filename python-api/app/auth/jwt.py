"""RS256 JWT token management."""

import asyncio
import jwt as pyjwt
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from app.config import get_settings

settings = get_settings()

# In-memory fast-path set (supplements DB for the current process lifetime)
_revoked_tokens: set[str] = set()


def _load_private_key() -> object:
    if not settings.jwt_private_key.exists():
        raise RuntimeError(
            f"JWT private key not found at {settings.jwt_private_key}. "
            "Generate keys with: scripts/generate_jwt_keys.sh"
        )
    with open(settings.jwt_private_key, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())


def _load_public_key() -> object:
    if not settings.jwt_public_key.exists():
        raise RuntimeError(
            f"JWT public key not found at {settings.jwt_public_key}. "
            "Generate keys with: scripts/generate_jwt_keys.sh"
        )
    with open(settings.jwt_public_key, "rb") as f:
        return serialization.load_pem_public_key(f.read(), backend=default_backend())


def issue_access_token(user: dict) -> str:
    private_key = _load_private_key()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["id"],
        "email": user.get("email", ""),
        "name": user.get("name", ""),
        "tenant_id": user["id"],
        "roles": user.get("role", "user"),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expiry_minutes)).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return pyjwt.encode(payload, private_key, algorithm="RS256")


def issue_refresh_token(user: dict) -> str:
    private_key = _load_private_key()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["id"],
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=settings.jwt_refresh_expiry_days)).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return pyjwt.encode(payload, private_key, algorithm="RS256")


def verify_token(token: str) -> Optional[dict]:
    public_key = _load_public_key()
    try:
        payload = pyjwt.decode(token, public_key, algorithms=["RS256"])
        if payload.get("jti") in _revoked_tokens:
            return None
        return payload
    except pyjwt.ExpiredSignatureError:
        return None
    except pyjwt.InvalidTokenError:
        return None


def revoke_token(jti: str, expires_at: Optional[int] = None) -> None:
    """Revoke a token by JTI. Persists to DB asynchronously."""
    _revoked_tokens.add(jti)
    # Persist to DB in background (best-effort)
    try:
        from app.db.lancedb_client import revoke_token_db
        expires_us = expires_at or int(
            (datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_expiry_days)).timestamp() * 1_000_000
        )
        asyncio.ensure_future(revoke_token_db(jti, expires_us))
    except RuntimeError:
        # No running event loop (e.g. during tests) — in-memory only is fine
        pass


async def is_token_revoked_async(jti: str) -> bool:
    """Check revocation: fast in-memory path first, then DB fallback."""
    if jti in _revoked_tokens:
        return True
    try:
        from app.db.lancedb_client import is_token_revoked
        return await is_token_revoked(jti)
    except Exception:
        return False


def refresh_token_rotated(old_jti: Optional[str]) -> str:
    if old_jti:
        revoke_token(old_jti)
    return str(uuid.uuid4())


# Alias used by ws.py and middleware
decode_access_token = verify_token
