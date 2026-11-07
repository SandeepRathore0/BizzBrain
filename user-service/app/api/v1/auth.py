"""Auth router – /api/v1/auth/*"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, TokenClaims
from app.core.rate_limit import rate_limit_by_ip
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    PasswordResetConfirmIn,
    PasswordResetRequestIn,
    RefreshRequest,
    RegisterRequest,
    UpdateMeRequest,
    VerifyEmailRequest,
)
from app.services.auth_service import AuthService
from app.core.config import get_settings

router = APIRouter(prefix="/api/v1", tags=["Auth"])

settings = get_settings()


def _ok(request: Request, data: dict) -> dict:
    return {
        "success": True,
        "data": data,
        "request_id": getattr(request.state, "request_id", "unknown"),
    }


def _token_response(access_token: str, refresh_token_raw: str, request: Request) -> dict:
    s = get_settings()
    return _ok(
        request,
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": s.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "refresh_token": refresh_token_raw,
        },
    )


# ───────────────────────────────────────────────
# Register
# ───────────────────────────────────────────────
@router.post("/auth/register", status_code=201)
async def register(
    body: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _rl=Depends(rate_limit_by_ip("register")),
):
    svc = AuthService(db, request_id=getattr(request.state, "request_id", None))
    user_dict, access_token, raw_rt = await svc.register(
        full_name=body.full_name,
        email=str(body.email),
        password=body.password,
    )
    return {
        "success": True,
        "data": {
            "user": user_dict,
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "refresh_token": raw_rt,
        },
        "request_id": getattr(request.state, "request_id", "unknown"),
    }


# ───────────────────────────────────────────────
# Login
# ───────────────────────────────────────────────
@router.post("/auth/login", status_code=200)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _rl=Depends(rate_limit_by_ip("login")),
):
    user_agent = request.headers.get("User-Agent")
    client_ip = request.client.host if request.client else None
    svc = AuthService(db, request_id=getattr(request.state, "request_id", None))
    user_dict, access_token, raw_rt = await svc.login(
        email=str(body.email),
        password=body.password,
        user_agent=user_agent,
        ip_address=client_ip,
    )
    return {
        "success": True,
        "data": {
            "user": user_dict,
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "refresh_token": raw_rt,
        },
        "request_id": getattr(request.state, "request_id", "unknown"),
    }


# ───────────────────────────────────────────────
# Refresh
# ───────────────────────────────────────────────
@router.post("/auth/refresh", status_code=200)
async def refresh(
    body: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_agent = request.headers.get("User-Agent")
    client_ip = request.client.host if request.client else None
    svc = AuthService(db, request_id=getattr(request.state, "request_id", None))
    new_access, new_raw_rt = await svc.refresh(
        refresh_token_raw=body.refresh_token,
        user_agent=user_agent,
        ip_address=client_ip,
    )
    return _token_response(new_access, new_raw_rt, request)


# ───────────────────────────────────────────────
# Logout
# ───────────────────────────────────────────────
@router.post("/auth/logout", status_code=200)
async def logout(
    body: LogoutRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(get_current_user),
):
    svc = AuthService(db, request_id=getattr(request.state, "request_id", None))
    await svc.logout(refresh_token_raw=body.refresh_token)
    return _ok(request, {"message": "Logged out successfully."})


@router.post("/auth/logout-all", status_code=200)
async def logout_all(
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(get_current_user),
):
    svc = AuthService(db, request_id=getattr(request.state, "request_id", None))
    await svc.logout_all(user_id=token.user_id)
    return _ok(request, {"message": "All sessions revoked."})


# ───────────────────────────────────────────────
# Email verification
# ───────────────────────────────────────────────
@router.post("/auth/verify-email", status_code=200)
async def verify_email(
    body: VerifyEmailRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    svc = AuthService(db, request_id=getattr(request.state, "request_id", None))
    await svc.verify_email(token_raw=body.token)
    return _ok(request, {"message": "Email verified successfully."})


# ───────────────────────────────────────────────
# Password reset
# ───────────────────────────────────────────────
@router.post("/auth/password-reset/request", status_code=200)
async def password_reset_request(
    body: PasswordResetRequestIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _rl=Depends(rate_limit_by_ip("password_reset")),
):
    svc = AuthService(db, request_id=getattr(request.state, "request_id", None))
    await svc.request_password_reset(email=str(body.email))
    # Always return success to avoid leaking whether the email exists
    return _ok(request, {"message": "If this email is registered, a reset link has been sent."})


@router.post("/auth/password-reset/confirm", status_code=200)
async def password_reset_confirm(
    body: PasswordResetConfirmIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    svc = AuthService(db, request_id=getattr(request.state, "request_id", None))
    await svc.confirm_password_reset(token_raw=body.token, new_password=body.new_password)
    return _ok(request, {"message": "Password reset successfully."})


# ───────────────────────────────────────────────
# /users/me
# ───────────────────────────────────────────────
@router.get("/users/me", status_code=200)
async def get_me(
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(get_current_user),
):
    svc = AuthService(db, request_id=getattr(request.state, "request_id", None))
    user_dict = await svc.get_me(user_id=token.user_id)
    return _ok(request, user_dict)


@router.put("/users/me", status_code=200)
async def update_me(
    body: UpdateMeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: TokenClaims = Depends(get_current_user),
):
    svc = AuthService(db, request_id=getattr(request.state, "request_id", None))
    user_dict = await svc.update_me(user_id=token.user_id, full_name=body.full_name)
    return _ok(request, user_dict)
