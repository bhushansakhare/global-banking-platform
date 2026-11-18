"""Request dependencies: DB session, current user, RBAC and request context."""
from __future__ import annotations

import ipaddress
import uuid
from dataclasses import dataclass, field
from typing import Annotated

import jwt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, Unauthorized
from app.core.security import decode_access_token
from app.database.session import get_db
from app.models.identity import User, UserSession

DB = Annotated[Session, Depends(get_db)]


@dataclass
class Principal:
    user: User
    tenant_id: uuid.UUID
    session_id: uuid.UUID
    role: str
    permissions: set[str] = field(default_factory=set)
    ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    def has(self, perm: str) -> bool:
        return perm in self.permissions


def client_ip(request: Request) -> str | None:
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


def get_principal(request: Request, db: DB) -> Principal:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise Unauthorized("Authentication required")
    try:
        claims = decode_access_token(header[7:].strip())
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Access token expired", code="token_expired") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid access token") from exc

    session = db.get(UserSession, uuid.UUID(claims["sid"]))
    if session is None or session.revoked_at is not None:
        raise Unauthorized("Session is no longer valid", code="session_revoked")
    user = db.scalar(select(User).where(User.id == uuid.UUID(claims["sub"]), User.deleted_at.is_(None)))
    if user is None or not user.is_active or str(user.tenant_id) != claims["tid"]:
        raise Unauthorized("User is not active")
    perms = {p.code for p in user.role.permissions}
    return Principal(user=user, tenant_id=user.tenant_id, session_id=session.id, role=user.role.code,
                     permissions=perms, ip=client_ip(request), user_agent=request.headers.get("user-agent"),
                     request_id=getattr(request.state, "request_id", None))


CurrentUser = Annotated[Principal, Depends(get_principal)]


def require(*perms: str):
    def _dep(principal: CurrentUser) -> Principal:
        missing = [p for p in perms if not principal.has(p)]
        if missing:
            raise Forbidden("You do not have permission to perform this action", details={"missing": missing})
        return principal

    return Depends(_dep)
