"""Auth router — Google OAuth + JWT."""

from fastapi import APIRouter, Request, HTTPException, Response, Depends
from app.auth.google import validate_google_id_token
from app.auth.jwt import issue_access_token, issue_refresh_token, verify_token
from app.auth.middleware import get_current_user
from app.db.lancedb_client import create_or_update_user, get_user_by_id
from app.models.schemas import AuthResponse, RefreshResponse, UserResponse
from app.config import get_settings
import uuid

router = APIRouter()
settings = get_settings()


@router.post("/google", response_model=AuthResponse)
async def google_auth(request: Request, response: Response):
    body = await request.json()
    id_token = body.get("id_token")

    if not id_token:
        raise HTTPException(status_code=400, detail="id_token required")

    if not settings.google_client_id:
        dev_user = {
            "id": "dev-" + str(uuid.uuid4()),
            "google_sub": "dev-sub",
            "email": "dev@example.com",
            "name": "Dev User",
            "avatar_url": None,
        }
        user_id = await create_or_update_user(dev_user)
        dev_user["id"] = user_id

        access_token = issue_access_token(dev_user)
        refresh_token = issue_refresh_token(dev_user)

        response.set_cookie(
            key="kg_refresh_token",
            value=refresh_token,
            httponly=True,
            secure=True,
            samesite="strict",
            max_age=7 * 24 * 3600,
            path="/api/v1/auth",
        )

        return AuthResponse(
            access_token=access_token,
            expires_in=settings.jwt_expiry_minutes * 60,
            user=UserResponse(
                id=user_id,
                email=dev_user["email"],
                name=dev_user["name"],
                avatar_url=None,
            ),
        )

    user_info = await validate_google_id_token(id_token)
    if not user_info:
        raise HTTPException(status_code=401, detail="Invalid Google token")

    user_info["id"] = str(uuid.uuid4())
    user_id = await create_or_update_user(user_info)
    user_info["id"] = user_id

    access_token = issue_access_token(user_info)
    refresh_tok = issue_refresh_token(user_info)

    response.set_cookie(
        key="kg_refresh_token",
        value=refresh_tok,
        httponly=True,
        secure=True,
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

    user = await get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    new_access = issue_access_token(user)

    response.set_cookie(
        key="kg_refresh_token",
        value=issue_refresh_token(user),
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=7 * 24 * 3600,
        path="/api/v1/auth",
    )

    return RefreshResponse(access_token=new_access, expires_in=settings.jwt_expiry_minutes * 60)


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("kg_refresh_token", path="/api/v1/auth")
    return {"status": "ok"}
