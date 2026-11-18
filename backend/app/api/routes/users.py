from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import func, select, update

from app.api import serializers as S
from app.api.deps import DB, Principal, require
from app.audit.service import record as audit
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.security import hash_password, validate_password_strength
from app.models.identity import Role, User, UserSession
from app.schemas.requests import UserCreate, UserUpdate
from app.services.seed import system_role

router = APIRouter(prefix="/users", tags=["users"])


@router.get("")
def list_users(db: DB, p: Principal = require("users:manage")) -> list[dict]:
    users = db.scalars(select(User).where(User.tenant_id == p.tenant_id, User.deleted_at.is_(None))
                       .order_by(User.full_name)).all()
    return [S.user(u) for u in users]


@router.get("/roles")
def roles(db: DB, p: Principal = require("settings:read")) -> list[dict]:
    rs = db.scalars(select(Role).where(Role.tenant_id.is_(None)).order_by(Role.code)).all()
    return [{"code": r.code, "name": r.name, "permissions": sorted(x.code for x in r.permissions)} for r in rs]


@router.post("", status_code=201)
def create_user(body: UserCreate, db: DB, p: Principal = require("users:manage")) -> dict:
    if body.role == "owner" and p.role != "owner":
        raise Forbidden("Only an owner can create another owner")
    try:
        validate_password_strength(body.password)
    except ValueError as exc:
        raise ValidationFailed(str(exc)) from exc
    if db.scalar(select(User.id).where(func.lower(User.email) == body.email.lower())):
        raise Conflict("A user with this email already exists")
    u = User(tenant_id=p.tenant_id, email=body.email.lower(), full_name=body.full_name,
             password_hash=hash_password(body.password), role_id=system_role(db, body.role).id,
             password_changed_at=datetime.now(UTC))
    db.add(u)
    db.flush()
    db.refresh(u)
    audit(db, action="user.create", tenant_id=p.tenant_id, actor_user_id=p.user_id, entity_type="user",
          entity_id=u.id, after={"email": u.email, "role": body.role}, ip=p.ip)
    return S.user(u)


@router.patch("/{user_id}")
def update_user(user_id: uuid.UUID, body: UserUpdate, db: DB, p: Principal = require("users:manage")) -> dict:
    u = db.scalar(select(User).where(User.id == user_id, User.tenant_id == p.tenant_id, User.deleted_at.is_(None)))
    if u is None:
        raise NotFound("User not found")
    before = {"role": u.role.code, "is_active": u.is_active, "full_name": u.full_name}
    if body.role and body.role != u.role.code:
        if (body.role == "owner" or u.role.code == "owner") and p.role != "owner":
            raise Forbidden("Only an owner can change owner roles")
        if u.id == p.user_id:
            raise ValidationFailed("You cannot change your own role")
        u.role_id = system_role(db, body.role).id
    if body.is_active is not None:
        if u.id == p.user_id and not body.is_active:
            raise ValidationFailed("You cannot deactivate yourself")
        u.is_active = body.is_active
        if not body.is_active:
            db.execute(update(UserSession).where(UserSession.user_id == u.id, UserSession.revoked_at.is_(None))
                       .values(revoked_at=datetime.now(UTC), revoke_reason="deactivated"))
    if body.full_name:
        u.full_name = body.full_name
    db.flush()
    db.refresh(u)
    audit(db, action="user.update", tenant_id=p.tenant_id, actor_user_id=p.user_id, entity_type="user",
          entity_id=u.id, before=before, after=body.model_dump(exclude_unset=True), ip=p.ip)
    return S.user(u)
