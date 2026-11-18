"""Password hashing, JWT access tokens and opaque refresh tokens."""
from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)

PASSWORD_POLICY = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{12,128}$")


def validate_password_strength(password: str) -> None:
    if not PASSWORD_POLICY.match(password):
        raise ValueError(
            "Password must be 12–128 characters and include upper-case, lower-case letters and a digit"
        )


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


# A pre-computed hash so failed lookups take comparable time (user enumeration defence)
DUMMY_HASH = _hasher.hash("dummy-password-for-timing-Equalisation1")


def create_access_token(*, user_id: uuid.UUID, tenant_id: uuid.UUID, session_id: uuid.UUID,
                        role: str, permissions: list[str]) -> tuple[str, int]:
    s = get_settings()
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=s.access_token_minutes)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "sid": str(session_id),
        "role": role,
        "perms": permissions,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "iss": s.jwt_issuer,
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256"), s.access_token_minutes * 60


def decode_access_token(token: str) -> dict[str, Any]:
    s = get_settings()
    payload = jwt.decode(token, s.jwt_secret, algorithms=["HS256"], issuer=s.jwt_issuer,
                         options={"require": ["exp", "sub", "tid", "sid"]})
    if payload.get("typ") != "access":
        raise jwt.InvalidTokenError("Wrong token type")
    return payload


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)
