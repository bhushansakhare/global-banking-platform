"""Account and connection lifecycle."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import Principal
from app.audit.service import record as audit
from app.core.config import get_settings
from app.core.crypto import decrypt_json, encrypt_json, encrypt_str, keyed_hash, mask_account_number
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.integrations.base.adapter import ProviderError
from app.integrations.registry import get_adapter
from app.models.banking import AccountConsent, BankAccount, BankConnection, Institution
from app.schemas.requests import AccountCreate, AccountUpdate

ACTIVE_ON_CREATE = {"manual_upload", "demo"}


def validate_cron(expr: str) -> None:
    from apscheduler.triggers.cron import CronTrigger
    try:
        CronTrigger.from_crontab(expr)
    except ValueError as exc:
        raise ValidationFailed(f"Invalid schedule (cron expression): {exc}", code="invalid_schedule") from exc


def get_account(db: Session, tenant_id: uuid.UUID, account_id: uuid.UUID) -> BankAccount:
    a = db.scalar(select(BankAccount).where(BankAccount.id == account_id, BankAccount.tenant_id == tenant_id,
                                            BankAccount.deleted_at.is_(None)))
    if a is None:
        raise NotFound("Account not found")
    return a


def visible_institution(db: Session, tenant_id: uuid.UUID, institution_id: uuid.UUID) -> Institution:
    inst = db.scalar(select(Institution).where(Institution.id == institution_id, Institution.deleted_at.is_(None),
                                               or_(Institution.tenant_id.is_(None), Institution.tenant_id == tenant_id)))
    if inst is None:
        raise NotFound("Institution not found")
    return inst


def create_account(db: Session, p: Principal, body: AccountCreate) -> BankAccount:
    inst = visible_institution(db, p.tenant_id, body.institution_id)
    prov = inst.provider
    if body.connection_provider and body.connection_provider != prov.code:
        from app.integrations.methods import resolve_provider
        prov, _ = resolve_provider(db, provider_code=body.connection_provider,
                                   country_code=body.country_code or inst.country_code, institution=inst)
    if prov.is_demo and not get_settings().demo_allowed:
        raise Forbidden("Demo institutions are disabled in this environment")
    if prov.integration_status == "planned":
        raise ValidationFailed(f"{prov.name} is not yet available. Use statement upload for this bank instead.",
                               code="integration_unavailable")
    if body.sync_schedule:
        validate_cron(body.sync_schedule)
    if body.demo_scenario and not prov.is_demo:
        raise ValidationFailed("Demo scenarios only apply to demo institutions")

    number_hash = keyed_hash(body.account_number.replace(" ", "").upper()) if body.account_number else None
    if number_hash and db.scalar(select(BankAccount.id).where(BankAccount.tenant_id == p.tenant_id,
                                                              BankAccount.account_number_hash == number_hash,
                                                              BankAccount.deleted_at.is_(None))):
        raise Conflict("An account with this number already exists")

    creds: dict[str, Any] = {"scenario": body.demo_scenario or "ok"} if prov.is_demo else {}
    adapter = get_adapter(prov.code)
    conn = BankConnection(tenant_id=p.tenant_id, institution_id=inst.id, provider_id=prov.id,
                          status="active" if prov.code in ACTIVE_ON_CREATE else "pending", is_demo=prov.is_demo,
                          encrypted_credentials=encrypt_json(creds, str(p.tenant_id)) if creds else None,
                          connection_method=adapter.connection_method.value, environment=adapter.environment())
    db.add(conn)
    db.flush()
    acc = BankAccount(
        tenant_id=p.tenant_id, institution_id=inst.id, connection_id=conn.id, nickname=body.nickname,
        holder_name=body.holder_name, account_type=body.account_type, currency=body.currency,
        country_code=body.country_code or inst.country_code,
        account_number_encrypted=encrypt_str(body.account_number, str(p.tenant_id)) if body.account_number else None,
        account_number_hash=number_hash,
        account_number_masked=mask_account_number(body.account_number) if body.account_number else "••••",
        external_account_id=body.external_account_id or ("primary" if prov.is_demo else None),
        automation_enabled=body.automation_enabled, low_balance_threshold=body.low_balance_threshold,
        statement_period=body.statement_period, is_demo=prov.is_demo, notes=body.notes,
        # never "healthy" before a verified sync; manual import has no live connection at all
        connection_health="not_applicable" if prov.code == "manual_upload" else "unknown",
        sync_schedule=body.sync_schedule, timezone=body.timezone,
    )
    db.add(acc)
    db.flush()
    audit(db, action="account.create", tenant_id=p.tenant_id, actor_user_id=p.user_id, entity_type="bank_account",
          entity_id=acc.id, after={"nickname": acc.nickname, "institution": inst.name, "currency": acc.currency,
                                   "provider": prov.code, "masked": acc.account_number_masked}, ip=p.ip)
    return acc


def update_account(db: Session, p: Principal, account_id: uuid.UUID, body: AccountUpdate) -> BankAccount:
    a = get_account(db, p.tenant_id, account_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("sync_schedule"):
        validate_cron(changes["sync_schedule"])
    if "sync_schedule" in changes and not changes["sync_schedule"]:
        changes["sync_schedule"] = None
    before = {k: getattr(a, k) for k in changes}
    for k, v in changes.items():
        setattr(a, k, v)
    if "sync_schedule" in changes or "timezone" in changes:
        from app.automation.handlers import settings_for
        from app.scheduler.scheduler import compute_account_next
        a.next_sync_at = compute_account_next(a, settings_for(db, p.tenant_id)) if a.sync_schedule else None
    audit(db, action="account.update", tenant_id=p.tenant_id, actor_user_id=p.user_id, entity_type="bank_account",
          entity_id=a.id, before=before, after=changes, ip=p.ip)
    return a


def delete_account(db: Session, p: Principal, account_id: uuid.UUID) -> None:
    a = get_account(db, p.tenant_id, account_id)
    a.deleted_at = datetime.now(UTC)
    a.automation_enabled = False
    if a.connection:
        disconnect(db, p, a.connection.id, remote=True)
    audit(db, action="account.delete", tenant_id=p.tenant_id, actor_user_id=p.user_id, entity_type="bank_account",
          entity_id=a.id, ip=p.ip)


def get_connection(db: Session, tenant_id: uuid.UUID, connection_id: uuid.UUID) -> BankConnection:
    c = db.scalar(select(BankConnection).where(BankConnection.id == connection_id, BankConnection.tenant_id == tenant_id,
                                               BankConnection.deleted_at.is_(None)))
    if c is None:
        raise NotFound("Connection not found")
    return c


def check(db: Session, p: Principal, connection_id: uuid.UUID) -> BankConnection:
    c = get_connection(db, p.tenant_id, connection_id)
    if c.status == "disconnected":
        raise Conflict("Connection is disconnected")
    adapter = get_adapter(c.provider.code)
    acc = db.scalar(select(BankAccount).where(BankAccount.connection_id == c.id, BankAccount.deleted_at.is_(None)))
    from app.automation.handlers import connection_context
    try:
        st = adapter.check_connection(connection_context(c, acc))
        c.status = st.status
        c.last_error = None if st.ok else st.message
    except ProviderError as exc:
        c.status, c.last_error = "error", str(exc)
    c.last_checked_at = datetime.now(UTC)
    if c.status == "active":
        c.last_success_at = datetime.now(UTC)
    audit(db, action="connection.check", tenant_id=p.tenant_id, actor_user_id=p.user_id,
          entity_type="bank_connection", entity_id=c.id, after={"status": c.status})
    return c


def disconnect(db: Session, p: Principal, connection_id: uuid.UUID, remote: bool = True) -> BankConnection:
    c = get_connection(db, p.tenant_id, connection_id)
    remote_error = None
    if remote and c.encrypted_credentials:
        try:
            from app.automation.handlers import connection_context
            get_adapter(c.provider.code).disconnect_account(connection_context(c, None))
        except ProviderError as exc:
            remote_error = str(exc)
    c.status, c.encrypted_credentials = "disconnected", None
    for consent in db.scalars(select(AccountConsent).where(AccountConsent.connection_id == c.id,
                                                           AccountConsent.status.in_(("pending", "granted")))):
        consent.status, consent.revoked_at = "revoked", datetime.now(UTC)
    for acc in db.scalars(select(BankAccount).where(BankAccount.connection_id == c.id)):
        acc.connection_health = "unknown"
        acc.next_sync_at = None
    audit(db, action="connection.disconnect", tenant_id=p.tenant_id, actor_user_id=p.user_id,
          entity_type="bank_connection", entity_id=c.id, meta={"remote_error": remote_error})
    return c


def reconnect(db: Session, p: Principal, connection_id: uuid.UUID) -> BankConnection:
    c = get_connection(db, p.tenant_id, connection_id)
    if c.provider.code in ACTIVE_ON_CREATE:
        c.status = "active"
        if c.is_demo and not c.encrypted_credentials:
            c.encrypted_credentials = encrypt_json({"scenario": "ok"}, str(p.tenant_id))
    else:
        c.status = "pending"
    audit(db, action="connection.reconnect", tenant_id=p.tenant_id, actor_user_id=p.user_id,
          entity_type="bank_connection", entity_id=c.id)
    return c


def set_demo_scenario(db: Session, p: Principal, connection_id: uuid.UUID, scenario: str) -> BankConnection:
    c = get_connection(db, p.tenant_id, connection_id)
    if not c.is_demo:
        raise ValidationFailed("Scenarios only apply to demo connections")
    creds = decrypt_json(c.encrypted_credentials, str(p.tenant_id)) if c.encrypted_credentials else {}
    creds["scenario"] = scenario
    c.encrypted_credentials = encrypt_json(creds, str(p.tenant_id))
    c.status = "active"
    audit(db, action="connection.demo_scenario", tenant_id=p.tenant_id, actor_user_id=p.user_id,
          entity_type="bank_connection", entity_id=c.id, after={"scenario": scenario})
    return c


def store_credentials(db: Session, p: Principal, c: BankConnection, creds: dict[str, Any],
                      external_reference: str | None = None) -> None:
    """Explicitly authorised by the acting user; encrypted with AES-256-GCM bound to the tenant."""
    c.encrypted_credentials = encrypt_json(creds, str(p.tenant_id))
    c.credentials_authorised_by = p.user_id
    if external_reference:
        c.external_reference = external_reference
    audit(db, action="connection.credentials_stored", tenant_id=p.tenant_id, actor_user_id=p.user_id,
          entity_type="bank_connection", entity_id=c.id, meta={"fields": sorted(creds)})
