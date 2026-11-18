"""ConnectionAuthorizationService — choose a connection method for an EXISTING account, start the
provider-hosted authorisation and verify it. Accounts are never deleted or recreated here.

Security: these provider-hosted consent flows never ask for a bank password. Optional saved browser
logins are managed separately in browser_banking.py. For the RBI Account Aggregator the only input is
the customer's mobile number (their AA handle), stored encrypted and never logged or returned.
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal
from app.audit.service import record as audit
from app.automation import queue
from app.automation.handlers import connection_context, settings_for
from app.automation.state import compute_health
from app.core.config import get_settings
from app.core.crypto import decrypt_json, encrypt_json
from app.core.errors import Conflict, ValidationFailed
from app.integrations.base.adapter import ConfigurationMissing, ProviderError
from app.integrations.methods import resolve_provider
from app.integrations.registry import get_adapter
from app.models.banking import AccountConsent, BankAccount, BankConnection, BankProvider
from app.services.banking import get_account

_MOBILE = re.compile(r"^(?:\+?91)?([6-9]\d{9})$")


def _method_of(prov: BankProvider) -> str:
    return get_adapter(prov.code).connection_method.value


def switch_method(db: Session, p: Principal, account_id: uuid.UUID, provider_code: str) -> BankAccount:
    acc = get_account(db, p.tenant_id, account_id)
    if acc.connection and acc.connection.provider.code == provider_code and acc.connection.status != "disconnected":
        return acc
    prov, opt = resolve_provider(db, provider_code=provider_code, country_code=acc.country_code,
                                 institution=acc.institution)
    if prov.is_demo != acc.is_demo:
        raise ValidationFailed("Demo and real connections cannot be mixed on one account")
    old = acc.connection
    adapter = get_adapter(prov.code)
    manual = prov.code == "manual_upload"
    conn = BankConnection(tenant_id=p.tenant_id, institution_id=acc.institution_id, provider_id=prov.id,
                          status="active" if manual else "pending", is_demo=prov.is_demo,
                          connection_method=_method_of(prov), environment=adapter.environment())
    db.add(conn)
    db.flush()
    acc.connection_id = conn.id
    acc.connection = conn
    acc.external_account_id = None
    acc.last_sync_error = None
    acc.next_sync_at = None
    acc.connection_health = "not_applicable" if manual else "unknown"
    audit(db, action="account.connection_method.change", tenant_id=p.tenant_id, actor_user_id=p.user_id,
          entity_type="bank_account", entity_id=acc.id,
          before={"provider": old.provider.code if old else None, "connection_id": str(old.id) if old else None},
          after={"provider": prov.code, "connection_id": str(conn.id), "method": conn.connection_method,
                 "availability": opt["status"]}, ip=p.ip)
    # the previous connection stays on record (history); statement upload remains available as fallback
    return acc


def start_authorization(db: Session, p: Principal, account_id: uuid.UUID, *, mobile: str | None = None,
                        redirect_url: str | None = None) -> dict[str, Any]:
    acc = get_account(db, p.tenant_id, account_id)
    conn = acc.connection
    if conn is None:
        raise ValidationFailed("Choose a connection method first", code="no_connection")
    code = conn.provider.code
    if code == "manual_upload":
        raise ValidationFailed("Manual statement import needs no authorisation", code="not_applicable")
    adapter = get_adapter(code)
    if not adapter.is_configured():
        raise ValidationFailed(f"{conn.provider.name} is awaiting provider setup on this platform "
                               "(administrator must add provider credentials).", code="provider_setup_required")

    if code == "india_account_aggregator":
        m = _MOBILE.match(re.sub(r"[\s\-]", "", mobile or ""))
        if not m:
            raise ValidationFailed("Enter the 10-digit mobile number registered with your bank / Account Aggregator",
                                   code="invalid_mobile")
        number = m.group(1)
        try:
            res = adapter.create_consent(  # type: ignore[attr-defined]
                mobile=number, account_type=acc.account_type,
                redirect_url=redirect_url or get_settings().setu_aa_redirect_url, reference=str(conn.id))
        except ConfigurationMissing as exc:
            raise ValidationFailed(str(exc), code=exc.code) from exc
        except ProviderError as exc:
            raise ValidationFailed(f"Account Aggregator rejected the consent request: {exc}", code=exc.code) from exc
        creds = decrypt_json(conn.encrypted_credentials, str(p.tenant_id)) if conn.encrypted_credentials else {}
        creds["vua"] = number
        conn.encrypted_credentials = encrypt_json(creds, str(p.tenant_id))
        conn.credentials_authorised_by = p.user_id
        conn.external_reference, conn.status = res["consent_id"], "pending"
        conn.last_error = None
        for old in db.scalars(select(AccountConsent).where(AccountConsent.connection_id == conn.id,
                                                           AccountConsent.status == "pending")):
            old.status = "revoked"
            old.revoked_at = datetime.now(UTC)
        db.add(AccountConsent(tenant_id=p.tenant_id, connection_id=conn.id, consent_reference=res["consent_id"],
                              scopes=["profile", "summary", "transactions"], status="pending",
                              authorisation_url=res["url"]))
        audit(db, action="connection.consent_requested", tenant_id=p.tenant_id, actor_user_id=p.user_id,
              entity_type="bank_connection", entity_id=conn.id,
              meta={"provider": code, "environment": adapter.environment()})  # mobile number never audited
        return {"authorisation_url": res["url"], "status": "pending", "provider": code,
                "environment": adapter.environment(),
                "instructions": "Open the Account Aggregator page, verify with the OTP sent by the AA, select your "
                                "bank account and approve the consent. Then return here and click 'I have approved'."}
    if code == "demo":
        conn.status = "active"
        if not conn.encrypted_credentials:
            conn.encrypted_credentials = encrypt_json({"scenario": "ok"}, str(p.tenant_id))
        return {"status": "active", "provider": code, "environment": "demo"}
    raise ValidationFailed(f"Use the {conn.provider.name} consent flow for this connection",
                           code="use_provider_flow", details={"provider": code})


def verify_authorization(db: Session, p: Principal | None, account_id: uuid.UUID, *, tenant_id: uuid.UUID | None = None,
                         trigger_sync: bool = True) -> BankAccount:
    """Ask the provider for the real authorisation status. Connection becomes active only if the provider says so."""
    tid = p.tenant_id if p else tenant_id
    assert tid is not None
    acc = get_account(db, tid, account_id)
    conn = acc.connection
    if conn is None or conn.status == "disconnected":
        raise Conflict("Account has no active connection")
    adapter = get_adapter(conn.provider.code)
    was_active = conn.status == "active"
    try:
        st = adapter.check_connection(connection_context(conn, acc))
        conn.status, conn.last_error = st.status, (None if st.ok else st.message)
    except ProviderError as exc:
        st = None
        conn.status, conn.last_error = "error", str(exc)
    conn.last_checked_at = datetime.now(UTC)
    consent = db.scalar(select(AccountConsent).where(AccountConsent.connection_id == conn.id,
                                                     AccountConsent.consent_reference == conn.external_reference)
                        .order_by(AccountConsent.created_at.desc()).limit(1)) if conn.external_reference else None
    if consent is not None and st is not None:
        if st.ok and consent.status != "granted":
            consent.status, consent.granted_at = "granted", datetime.now(UTC)
        elif st.status == "consent_expired":
            consent.status = "expired"
        if st.consent_expires_at:
            consent.expires_at = st.consent_expires_at
    acc.connection_health = compute_health(acc)
    if p:
        audit(db, action="connection.authorisation_checked", tenant_id=tid, actor_user_id=p.user_id,
              entity_type="bank_connection", entity_id=conn.id, after={"status": conn.status})
    if conn.status == "active" and not was_active and trigger_sync:
        s = settings_for(db, tid)
        queue.enqueue(db, tenant_id=tid, job_type="collect_account", account_id=acc.id,
                      payload={"lookback_days": max(s.lookback_days, 30), "initial_sync": True}, trigger="manual",
                      idempotency_key=f"collect:{acc.id}:initial:{conn.external_reference or conn.id}",
                      max_attempts=s.max_attempts, created_by=p.user_id if p else None)
    return acc


def handle_aa_notification(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Webhook from the AA gateway. The payload is only a *hint*: state is always re-read from the provider."""
    consent_id = str(payload.get("consentId") or (payload.get("data") or {}).get("consentId") or "")
    if not consent_id:
        return {"handled": False, "reason": "no consent id"}
    conn = db.scalar(select(BankConnection).where(BankConnection.external_reference == consent_id,
                                                  BankConnection.deleted_at.is_(None)))
    if conn is None:
        return {"handled": False, "reason": "unknown consent"}
    acc = db.scalar(select(BankAccount).where(BankAccount.connection_id == conn.id, BankAccount.deleted_at.is_(None)))
    if acc is None:
        return {"handled": False, "reason": "no account"}
    verify_authorization(db, None, acc.id, tenant_id=conn.tenant_id)
    # a data-ready notification lets a waiting collect job re-check immediately
    job = queue.active_collect_job(db, acc.id)
    if job is not None and job.status == "queued" and job.run_after > datetime.now(UTC):
        job.run_after = datetime.now(UTC)
    audit(db, action="connection.provider_notification", tenant_id=conn.tenant_id, entity_type="bank_connection",
          entity_id=conn.id, actor_type="system", meta={"type": str(payload.get("type", ""))[:60]})
    return {"handled": True}
