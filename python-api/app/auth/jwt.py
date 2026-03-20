"""RS256 JWT token management."""

import jwt as pyjwt
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from app.config import get_settings

settings = get_settings()

_revoked_tokens: set[str] = {}


def _load_private_key() -> Optional[object]:
    if not settings.jwt_private_key.exists():
        return None
    with open(settings.jwt_private_key, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())


def _load_public_key() -> Optional[object]:
    if not settings.jwt_public_key.exists():
        return None
    with open(settings.jwt_public_key, "rb") as f:
        return serialization.load_pem_public_key(f.read(), backend=default_backend())


def issue_access_token(user: dict) -> str:
    private_key = _load_private_key()
    if private_key is None:
        return "dev_token_" + str(uuid.uuid4())

    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["id"],
        "email": user.get("email", ""),
        "name": user.get("name", ""),
        "tenant_id": user["id"],
        "roles": ["user"],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expiry_minutes)).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return pyjwt.encode(payload, private_key, algorithm="RS256")


def issue_refresh_token(user: dict) -> str:
    private_key = _load_private_key()
    if private_key is None:
        return "dev_refresh_" + str(uuid.uuid4())

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
    if public_key is None:
        if token.startswith("dev_token_") or token.startswith("dev_refresh_"):
            payload = {"sub": "dev-user", "email": "dev@example.com", "name": "Dev User"}
            return payload
        return None

    try:
        payload = pyjwt.decode(token, public_key, algorithms=["RS256"])
        if payload.get("jti") in _revoked_tokens:
            return None
        return payload
    except pyjwt.ExpiredSignatureError:
        return None
    except pyjwt.InvalidTokenError:
        return None


def revoke_token(jti: str) -> None:
    _revoked_tokens.add(jti)


def refresh_token_rotated(old_jti: Optional[str]) -> str:
    if old_jti:
        _revoked_tokens.add(old_jti)
    return str(uuid.uuid4())
