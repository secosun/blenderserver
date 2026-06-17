"""FastAPI dependency injection — extract current user from request.

Usage::

    @router.get("/tasks")
    async def list_tasks(current_user: Annotated[dict, Depends(get_current_user)]):
        ...
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.auth import decode_access_token

_bearer = HTTPBearer(auto_error=False)

_X_API_KEY_HEADER = "X-API-Key"


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> dict:
    """Resolve the current user from JWT Bearer token or X-API-Key header."""
    user: dict | None = None

    # 1. Try JWT Bearer
    if credentials:
        payload = decode_access_token(credentials.credentials)
        if payload is not None:
            user_id = payload.get("sub")
            if user_id:
                user = await request.app.state.task_manager.db.get_user(user_id)

    # 2. Try X-API-Key header
    if user is None:
        api_key = request.headers.get(_X_API_KEY_HEADER)
        if api_key:
            from sqlalchemy import text
            rows = await request.app.state.task_manager.db._fetchall(
                text("SELECT * FROM api_keys WHERE is_active = true"),
            )
            for row in rows:
                _, check_hash = _hash_api_key(api_key, row["key_prefix"])
                if hmac.compare_digest(check_hash, row["key_hash"]):
                    user = await request.app.state.task_manager.db.get_user(row["user_id"])
                    if user:
                        await request.app.state.task_manager.db._execute(
                            text("UPDATE api_keys SET last_used_at = :now WHERE id = :id"),
                            {"now": datetime.now(timezone.utc).isoformat(), "id": row["id"]},
                        )
                    break

    if user is None or not user.get("is_active"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired credentials")

    user["_auth_method"] = "jwt" if credentials else "api_key"
    return user


def _hash_api_key(key: str, prefix: str) -> tuple[str, str]:
    salt = prefix.encode("utf-8")
    hashed = hashlib.pbkdf2_hmac("sha256", key.encode("utf-8"), salt, 100_000)
    return prefix, hashed.hex()


async def require_admin(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    """Dependency: require the current user to have admin role."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="管理权限 required")
    return current_user
