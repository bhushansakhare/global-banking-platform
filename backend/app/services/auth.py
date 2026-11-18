"""Authentication: first-run setup, login with lockout, refresh-token rotation, logout."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.audit.service import record as audit
from app.core.config import get_settings
from app.core.errors import Conflict, Unauthorized, ValidationFailed
from app.core.security import (DUMMY_HASH, create_access_token, hash_password, hash_token, needs_rehash,
                               new_refresh_token, validate_password_strength, verify_password)
from app.models.identity import Organization, User, UserSession
from app.services.seed import bootstrap_tenant, seed_demo_institutions, seed_reference_data, system_role


@dataclass
class TokenPair:
    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_at: datetime
    user: User


def setup_required(db: Session) -> bool:
    return (db.scalar(select(func.count(User.id))) or 0) == 0


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "org"
    return f"{s}-{uuid.uuid4().hex[:6]}"


def initial_setup(db: Session, *, organisation: str, full_name: str, email: str, password: str,
                  reporting_currency: str, timezone: str, country_code: str | None, ip: str | None) -> User:
    if not setup_required(db):
        raise Conflict("Initial setup has already been completed")
    try:
        validate_password_strength(password)
    except ValueError as exc:
        raise ValidationFailed(str(exc)) from exc
    seed_reference_data(db)
    seed_demo_institutions(db)
    org = Organization(name=organisation.strip(), slug=_slug(organisation), reporting_currency=reporting_currency,
                       timezone=timezone, country_code=country_code)
    db.add(org)
    db.flush()
    user = User(tenant_id=org.id, email=email.lower().strip(), full_name=full_name.strip(),
                password_hash=hash_password(password), role_id=system_role(db, "owner").id,
                password_changed_at=datetime.now(UTC))
    db.add(user)
    db.flush()
    bootstrap_tenant(db, org.id)
    audit(db, action="setup.complete", tenant_id=org.id, actor_user_id=user.id, actor_label=user.email,
          entity_type="organization", entity_id=org.id, ip=ip)
    return user


def _issue(db: Session, user: User, family: uuid.UUID, ip: str | None, ua: str | None) -> TokenPair:
    s = get_settings()
    raw = new_refresh_token()
    now = datetime.now(UTC)
    sess = UserSession(tenant_id=user.tenant_id, user_id=user.id, family_id=family, refresh_token_hash=hash_token(raw),
                       expires_at=now + timedelta(days=s.refresh_token_days), last_used_at=now, ip_address=ip,
                       user_agent=(ua or "")[:500])
    db.add(sess)
    db.flush()
    perms = sorted(p.code for p in user.role.permissions)
    access, ttl = create_access_token(user_id=user.id, tenant_id=user.tenant_id, session_id=sess.id,
                                      role=user.role.code, permissions=perms)
    return TokenPair(access, ttl, raw, sess.expires_at, user)


def login(db: Session, *, email: str, password: str, ip: str | None, ua: str | None) -> TokenPair:
    s = get_settings()
    user = db.scalar(select(User).where(func.lower(User.email) == email.lower().strip(), User.deleted_at.is_(None))
                     .with_for_update())
    now = datetime.now(UTC)
    if user is None:
        verify_password(password, DUMMY_HASH)  # equalise timing
        audit(db, action="auth.login", tenant_id=None, actor_label=email[:254], outcome="failure",
              meta={"reason": "unknown_user"}, ip=ip, user_agent=ua)
        db.commit()
        raise Unauthorized("Invalid email or password", code="invalid_credentials")
    if user.locked_until and user.locked_until > now:
        audit(db, action="auth.login", tenant_id=user.tenant_id, actor_user_id=user.id, outcome="failure",
              meta={"reason": "locked"}, ip=ip, user_agent=ua)
        db.commit()
        raise Unauthorized("Account temporarily locked after repeated failed sign-in attempts",
                           code="account_locked")
    if not verify_password(password, user.password_hash) or not user.is_active:
        user.failed_login_count += 1
        meta = {"reason": "bad_password" if user.is_active else "inactive", "count": user.failed_login_count}
        if user.failed_login_count >= s.max_failed_logins:
            user.locked_until = now + timedelta(minutes=s.lockout_minutes)
            user.failed_login_count = 0
            meta["locked_until"] = user.locked_until.isoformat()
        audit(db, action="auth.login", tenant_id=user.tenant_id, actor_user_id=user.id, outcome="failure", meta=meta,
              ip=ip, user_agent=ua)
        db.commit()
        raise Unauthorized("Invalid email or password", code="invalid_credentials")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.failed_login_count, user.locked_until, user.last_login_at = 0, None, now
    pair = _issue(db, user, uuid.uuid4(), ip, ua)
    audit(db, action="auth.login", tenant_id=user.tenant_id, actor_user_id=user.id, actor_label=user.email,
          ip=ip, user_agent=ua)
    return pair


def refresh(db: Session, *, refresh_token: str, ip: str | None, ua: str | None) -> TokenPair:
    s = get_settings()
    now = datetime.now(UTC)
    sess = db.scalar(select(UserSession).where(UserSession.refresh_token_hash == hash_token(refresh_token))
                     .with_for_update())
    if sess is None:
        raise Unauthorized("Invalid refresh token", code="invalid_refresh")
    if sess.revoked_at is not None:
        # token reuse → likely theft: revoke the whole family
        db.execute(update(UserSession).where(UserSession.family_id == sess.family_id,
                                             UserSession.revoked_at.is_(None))
                   .values(revoked_at=now, revoke_reason="reuse_detected"))
        audit(db, action="auth.refresh_reuse", tenant_id=sess.tenant_id, actor_user_id=sess.user_id,
              outcome="failure", ip=ip, user_agent=ua)
        db.commit()
        raise Unauthorized("Session revoked", code="session_revoked")
    if sess.expires_at < now or sess.last_used_at < now - timedelta(minutes=s.session_idle_minutes):
        sess.revoked_at, sess.revoke_reason = now, "expired"
        db.commit()
        raise Unauthorized("Session expired", code="session_expired")
    user = db.get(User, sess.user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        raise Unauthorized("User is not active")
    pair = _issue(db, user, sess.family_id, ip, ua)
    new = db.scalar(select(UserSession).where(UserSession.refresh_token_hash == hash_token(pair.refresh_token)))
    sess.revoked_at, sess.revoke_reason, sess.replaced_by_id = now, "rotated", new.id if new else None
    return pair


def logout(db: Session, *, refresh_token: str | None, session_id: uuid.UUID | None, user_id: uuid.UUID | None,
           tenant_id: uuid.UUID | None) -> None:
    now = datetime.now(UTC)
    fam = None
    if refresh_token:
        sess = db.scalar(select(UserSession).where(UserSession.refresh_token_hash == hash_token(refresh_token)))
        fam = sess.family_id if sess else None
    if fam is None and session_id:
        sess = db.get(UserSession, session_id)
        fam = sess.family_id if sess else None
    if fam:
        db.execute(update(UserSession).where(UserSession.family_id == fam, UserSession.revoked_at.is_(None))
                   .values(revoked_at=now, revoke_reason="logout"))
    audit(db, action="auth.logout", tenant_id=tenant_id, actor_user_id=user_id)


def change_password(db: Session, *, user: User, current: str, new: str) -> None:
    if not verify_password(current, user.password_hash):
        raise ValidationFailed("Current password is incorrect")
    try:
        validate_password_strength(new)
    except ValueError as exc:
        raise ValidationFailed(str(exc)) from exc
    user.password_hash = hash_password(new)
    user.password_changed_at = datetime.now(UTC)
    db.execute(update(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
               .values(revoked_at=datetime.now(UTC), revoke_reason="password_changed"))
    audit(db, action="auth.password_change", tenant_id=user.tenant_id, actor_user_id=user.id)
