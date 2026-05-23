"""Authentication & authorisation — JWT tokens, API keys, password hashing.

Three authentication schemes:
1. **JWT Bearer token** — issued on login, for interactive web/mobile use.
2. **API Key (X-API-Key header)** — for programmatic / MCP access.
3. **Worker Callback Secret** — already handled in ``worker/callback.py``.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import jwt as _jwt
except ImportError:
    _jwt = None  # type: ignore[assignment]

from core.config import settings


# ======================================================================
# Password hashing (sha256-crypt — no bcrypt dependency needed)
# ======================================================================

_SALT_LENGTH = 32


def _random_salt() -> str:
    return secrets.token_hex(_SALT_LENGTH)


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Return ``(salt, hashed)`` using 100 000 rounds of SHA-256 HMAC."""
    salt = salt or _random_salt()
    key = salt.encode("utf-8")
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), key, 100_000)
    return salt, hashed.hex()


def verify_password(password: str, salt: str, stored_hash: str) -> bool:
    _, check = hash_password(password, salt)
    return hmac.compare_digest(check, stored_hash)


# ======================================================================
# API key utilities
# ======================================================================


def generate_api_key() -> str:
    """Generate a random API key prefixed with ``crdr_``."""
    return f"crdr_{secrets.token_urlsafe(48)}"


def mask_api_key(key: str) -> str:
    """Return a masked version for logging — only last 4 chars visible."""
    if len(key) <= 8:
        return "****"
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


# ======================================================================
# JWT
# ======================================================================


def _jwt_available() -> bool:
    if _jwt is None:
        raise RuntimeError(
            "PyJWT is not installed. Run: pip install pyjwt"
        )
    return True


def create_access_token(
    user_id: str,
    role: str = "user",
    expires_delta: timedelta | None = None,
) -> str:
    """Create a signed JWT access token."""
    _jwt_available()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(hours=settings.jwt_expiry_hours)
    )
    payload: dict[str, Any] = {
        "sub": user_id,
        "role": role,
        "iat": datetime.now(timezone.utc),
        "exp": expire,
        "jti": str(uuid.uuid4()),
    }
    return _jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Decode and validate a JWT token. Returns payload or ``None``."""
    _jwt_available()
    try:
        payload = _jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        return payload
    except Exception:
        return None
