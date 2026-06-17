"""Authentication routes — user registration, login, API key management."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from api.deps import get_current_user
from core.config import settings
from core.auth import (
    create_access_token,
    generate_api_key,
    hash_password,
    verify_password,
)
from core.db import AsyncDatabase
from models.schemas import (
    APIKeyCreate,
    APIKeyListResponse,
    APIKeyResponse,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _db(request: Request) -> AsyncDatabase:
    return request.app.state.task_manager.db


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(body: UserCreate, request: Request):
    """Register a new user account."""
    db = _db(request)
    existing = await db.get_user_by_email(body.email)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    salt, hashed = hash_password(body.password)
    user = await db.create_user(
        email=body.email,
        display_name=body.display_name or body.email.split("@")[0],
        password_salt=salt,
        password_hash=hashed,
        role="user",
    )

    # Create Stripe customer + organization + free subscription
    from core.billing import create_stripe_customer
    from core.quota_sync import sync_quotas_for_user

    stripe_customer_id = await create_stripe_customer(
        email=body.email,
        name=body.display_name or body.email,
    )

    org = await db.create_organization(
        name=f"{body.display_name or body.email} 的组织",
        slug=f"org-{user['id'][:8]}",
        owner_id=user["id"],
        stripe_customer_id=stripe_customer_id,
    )
    await db.add_organization_member(org["id"], user["id"], role="owner")

    plan = await db.get_default_plan()
    if plan:
        await db.create_subscription(
            organization_id=org["id"],
            plan_id=plan["id"],
            billing_interval="monthly",
        )

    await sync_quotas_for_user(db, user["id"])

    token = create_access_token(user_id=user["id"], role=user["role"])
    return TokenResponse(
        access_token=token,
        user=_format_user(user),
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: UserLogin, request: Request):
    """Authenticate with email + password, receive a JWT."""
    db = _db(request)
    user = await db.get_user_by_email(body.email)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not verify_password(body.password, user["password_salt"], user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.get("is_active"):
        raise HTTPException(status_code=403, detail="Account is disabled")

    token = create_access_token(user_id=user["id"], role=user["role"])
    return TokenResponse(
        access_token=token,
        user=_format_user(user),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Return the authenticated user's profile."""
    return _format_user(current_user)


@router.post("/api-keys", response_model=APIKeyResponse, status_code=201)
async def create_api_key(
    body: APIKeyCreate,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Generate a new API key for the current user."""
    db = _db(request)
    raw_key = generate_api_key()
    prefix = raw_key[:8]
    _, hashed = _hash_api_key(raw_key, prefix)

    key_record = await db.create_api_key(
        user_id=current_user["id"],
        key_hash=hashed,
        key_prefix=prefix,
        label=body.label,
    )

    return APIKeyResponse(
        id=key_record["id"],
        key_prefix=prefix,
        label=key_record.get("label", ""),
        full_key=raw_key,
        last_used_at=key_record.get("last_used_at"),
        is_active=bool(key_record.get("is_active", True)),
        created_at=key_record["created_at"],
    )


@router.get("/api-keys", response_model=APIKeyListResponse)
async def list_api_keys(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """List all API keys for the current user."""
    db = _db(request)
    keys = await db.list_api_keys(current_user["id"])
    return APIKeyListResponse(keys=[
        APIKeyResponse(
            id=k["id"],
            key_prefix=k["key_prefix"],
            label=k.get("label", ""),
            full_key=None,
            last_used_at=k.get("last_used_at"),
            is_active=bool(k.get("is_active", True)),
            created_at=k["created_at"],
        )
        for k in keys
    ])


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: str,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Revoke (delete) an API key."""
    db = _db(request)
    ok = await db.revoke_api_key(key_id, current_user["id"])
    if not ok:
        raise HTTPException(status_code=404, detail="API key not found")


def _format_user(u: dict) -> UserResponse:
    return UserResponse(
        id=u["id"],
        email=u["email"],
        display_name=u.get("display_name", ""),
        role=u.get("role", "user"),
        quota_concurrency=u.get("quota_concurrency", 2),
        quota_max_resolution=u.get("quota_max_resolution", 4096),
        quota_max_samples=u.get("quota_max_samples", 512),
        is_active=bool(u.get("is_active", True)),
        created_at=u["created_at"],
        updated_at=u["updated_at"],
    )


def _hash_api_key(key: str, prefix: str) -> tuple[str, str]:
    import hashlib
    salt = prefix.encode("utf-8")
    hashed = hashlib.pbkdf2_hmac("sha256", key.encode("utf-8"), salt, 100_000)
    return prefix, hashed.hex()


# ── Profile management ───────────────────────────────────────────────


class UpdateProfileBody(BaseModel):
    display_name: str | None = None


@router.patch("/profile", response_model=UserResponse)
async def update_profile(
    body: UpdateProfileBody,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Update the current user's profile."""
    db = _db(request)
    kwargs = {}
    if body.display_name is not None:
        kwargs["display_name"] = body.display_name
    if kwargs:
        user = await db.update_user(current_user["id"], **kwargs)
        return _format_user(user)
    return _format_user(current_user)


class ChangePasswordBody(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=8, max_length=128)


@router.post("/change-password")
async def change_password(
    body: ChangePasswordBody,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Change the current user's password."""
    db = _db(request)
    user = await db.get_user(current_user["id"])
    if not verify_password(body.old_password, user["password_salt"], user["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    salt, hashed = hash_password(body.new_password)
    await db.update_user(current_user["id"], password_salt=salt, password_hash=hashed)
    return {"ok": True, "message": "Password changed successfully"}


# ── Password Reset (dev: in-memory tokens) ──────────────────────────

import secrets
_reset_tokens: dict[str, dict] = {}  # token -> {user_id, expires_at}


class ForgotPasswordBody(BaseModel):
    email: str


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordBody, request: Request):
    """Request a password reset. Returns a reset token (dev mode only)."""
    db = _db(request)
    user = await db.get_user_by_email(body.email)
    if not user:
        # Don't reveal whether email exists
        return {"ok": True, "message": "If the email exists, a reset link has been sent (dev token shown in server logs)"}

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc).timestamp() + 3600  # 1 hour
    _reset_tokens[token] = {"user_id": user["id"], "expires_at": expires_at}

    import logging
    logger = logging.getLogger("blenderserver")
    logger.info("Password reset token for %s: %s", body.email, token)

    return {"ok": True, "message": "If the email exists, a reset link has been sent"}


class ResetPasswordBody(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8, max_length=128)


@router.post("/reset-password")
async def reset_password(body: ResetPasswordBody, request: Request):
    """Reset password using a reset token."""
    token_data = _reset_tokens.pop(body.token, None)
    if not token_data:
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    if datetime.now(timezone.utc).timestamp() > token_data["expires_at"]:
        raise HTTPException(status_code=400, detail="Token expired")

    db = _db(request)
    salt, hashed = hash_password(body.new_password)
    await db.update_user(token_data["user_id"], password_salt=salt, password_hash=hashed)
    return {"ok": True, "message": "Password has been reset"}
