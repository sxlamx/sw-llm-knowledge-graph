"""Auth router — Google OAuth + JWT."""

import urllib.parse
import uuid

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app.auth.google import validate_google_id_token
from app.auth.jwt import issue_access_token, issue_refresh_token, verify_token
from app.auth.middleware import get_current_user
from app.config import get_settings
from app.db.lancedb_client import create_or_update_user, get_user_by_google_sub, get_user_by_id, revoke_token_db
from app.models.schemas import AuthResponse, RefreshResponse, UserResponse

router = APIRouter()
settings = get_settings()

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


@router.get("/google/redirect")
async def google_redirect():
    """Redirect the browser to Google's OAuth consent screen."""
    if not settings.google_client_id:
        raise HTTPException(status_code=501, detail="Google OAuth not configured")
    redirect_uri = f"{settings.frontend_origin}/auth/callback/google"
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url=url)


@router.post("/google/exchange", response_model=AuthResponse)
async def google_exchange(request: Request, response: Response):
    """Exchange an OAuth authorization code for a JWT access token."""
    body = await request.json()
    code = body.get("code")
    redirect_uri = body.get("redirect_uri")

    if not code or not redirect_uri:
        raise HTTPException(status_code=400, detail="code and redirect_uri required")
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=501, detail="Google OAuth not configured")

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=15.0,
        )

    if token_resp.status_code != 200:
        raise HTTPException(status_code=401, detail=f"Google token exchange failed: {token_resp.text}")

    token_data = token_resp.json()
    id_token = token_data.get("id_token")
    if not id_token:
        raise HTTPException(status_code=401, detail="No id_token in Google response")

    user_info = await validate_google_id_token(id_token)
    if not user_info:
        raise HTTPException(status_code=401, detail="Invalid Google token")

    user_info["id"] = str(uuid.uuid4())
    user_id = await create_or_update_user(user_info)
    user_info["id"] = user_id

    db_user = await get_user_by_google_sub(user_info["google_sub"])
    status = (db_user or {}).get("status", "active")
    if status == "blocked":
        raise HTTPException(status_code=403, detail="Account has been blocked")
    if status == "pending":
        raise HTTPException(
            status_code=403,
            detail="Account pending approval. Contact an administrator to activate your account.",
        )

    access_token = issue_access_token(user_info)
    refresh_tok = issue_refresh_token(user_info)

    response.set_cookie(
        key="kg_refresh_token",
        value=refresh_tok,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        max_age=7 * 24 * 3600,
        path="/api/v1/auth",
    )

    return AuthResponse(
        access_token=access_token,
        expires_in=settings.jwt_expiry_minutes * 60,
        user=UserResponse(
            id=user_id,
            email=user_info.get("email", ""),
            name=user_info.get("name", ""),
            avatar_url=user_info.get("avatar_url"),
        ),
    )


@router.post("/google", response_model=AuthResponse)
async def google_auth(request: Request, response: Response):
    body = await request.json()
    id_token = body.get("id_token") or body.get("token")

    if not id_token:
        raise HTTPException(status_code=400, detail="id_token required")

    if not settings.google_client_id:
        raise HTTPException(
            status_code=501,
            detail="Google OAuth not configured. Set GOOGLE_CLIENT_ID environment variable."
        )

    user_info = await validate_google_id_token(id_token)
    if not user_info:
        raise HTTPException(status_code=401, detail="Invalid Google token")

    user_info["id"] = str(uuid.uuid4())
    user_id = await create_or_update_user(user_info)
    user_info["id"] = user_id

    # Enforce access control — pending/blocked users cannot log in
    db_user = await get_user_by_google_sub(user_info["google_sub"])
    status = (db_user or {}).get("status", "pending")
    if status == "blocked":
        raise HTTPException(status_code=403, detail="Account has been blocked")
    if status == "pending":
        raise HTTPException(
            status_code=403,
            detail="Account pending approval. Contact an administrator to activate your account.",
        )

    access_token = issue_access_token(user_info)
    refresh_tok = issue_refresh_token(user_info)

    response.set_cookie(
        key="kg_refresh_token",
        value=refresh_tok,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        max_age=7 * 24 * 3600,
        path="/api/v1/auth",
    )

    return AuthResponse(
        access_token=access_token,
        expires_in=settings.jwt_expiry_minutes * 60,
        user=UserResponse(
            id=user_id,
            email=user_info.get("email", ""),
            name=user_info.get("name", ""),
            avatar_url=user_info.get("avatar_url"),
        ),
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_token(request: Request, response: Response):
    refresh = request.cookies.get("kg_refresh_token")

    if not refresh:
        raise HTTPException(status_code=401, detail="No refresh token")

    payload = verify_token(refresh)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    old_jti = payload.get("jti")

    user = await get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    new_access = issue_access_token(user)
    new_refresh = issue_refresh_token(user)

    if old_jti:
        from app.auth.jwt import revoke_token
        revoke_token(old_jti)

    response.set_cookie(
        key="kg_refresh_token",
        value=new_refresh,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        max_age=7 * 24 * 3600,
        path="/api/v1/auth",
    )

    return RefreshResponse(access_token=new_access, expires_in=settings.jwt_expiry_minutes * 60)


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("kg_refresh_token", path="/api/v1/auth")
    return {"status": "ok"}
