"""Dynamic bank profiles and user-controlled local browser connections."""
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select

from app.api.deps import DB, Principal, require
from app.audit.service import record as audit
from app.automation import queue
from app.core.config import get_settings
from app.core.crypto import decrypt_json, encrypt_json
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.integrations.banks.profiles import BrowserProfile, find_profile, profiles_for
from app.models.banking import BankConnection, BankProvider
from app.models.system import SystemSetting
from app.services.banking import get_account

router = APIRouter(tags=["browser-banking"])


class BrowserStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_key: str = Field(pattern=r"^[a-z0-9_]{2,60}$")
    customer_id: str | None = Field(default=None, max_length=120)
    date_from: date | None = None
    date_to: date | None = None
    login_only: bool = False
    authenticate: bool = False


class SavedBankLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_key: str = Field(pattern=r"^[a-z0-9_]{2,60}$")
    customer_id: str = Field(min_length=1, max_length=120)
    password: SecretStr | None = Field(default=None, min_length=1, max_length=1024)


def scoped_credentials(account, profile):
    credentials = decrypt_json(account.browser_identifier_encrypted, str(account.tenant_id)) \
        if account.browser_identifier_encrypted else {}
    if credentials.get("scope") == profile.credential_scope:
        return credentials
    # Legacy records held an identifier only. Never reuse a password across scopes.
    if "scope" not in credentials:
        return {"customer_id": credentials.get("customer_id", "")}
    return {}


def validate_account_profile(account, profile):
    if account.is_demo:
        raise ValidationFailed("Real browser connections cannot be used for demo accounts")
    if profile.country != account.country_code or (profile.currencies and account.currency not in profile.currencies):
        raise ValidationFailed("Bank profile country/currency does not match this account")
    if account.account_type not in profile.account_types:
        raise ValidationFailed("Bank profile does not support this account type")


@router.put("/accounts/{account_id}/browser/credentials")
def save_bank_login(account_id: uuid.UUID, body: SavedBankLogin, db: DB,
                    p: Principal = require("connections:manage")) -> dict:
    account = get_account(db, p.tenant_id, account_id)
    db.execute(select(type(account).id).where(type(account).id == account.id).with_for_update())
    profile = profile_for(db, p.tenant_id, body.profile_key)
    validate_account_profile(account, profile)
    if body.password is not None and not profile.public()["capabilities"]["saved_login"]:
        raise Conflict("Password login fields have not been reviewed for this bank", code="login_review_required")
    customer_id = body.customer_id.strip()
    if not customer_id:
        raise ValidationFailed("Enter your Customer ID / User ID")
    credentials = scoped_credentials(account, profile)
    # Changing customer identity must not silently retain another user's password.
    if credentials.get("customer_id") != customer_id:
        credentials.pop("password", None)
    credentials.update(customer_id=customer_id, scope=profile.credential_scope, profile_key=profile.key)
    if body.password is not None:
        credentials["password"] = body.password.get_secret_value()
    account.browser_identifier_encrypted = encrypt_json(credentials, str(p.tenant_id))
    audit(db, action="browser.credentials.save", tenant_id=p.tenant_id, actor_user_id=p.user_id,
          entity_type="bank_account", entity_id=account.id, meta={"profile_key": profile.key})
    return {"saved": True, "has_customer_id": True, "has_password": bool(credentials.get("password"))}


@router.delete("/accounts/{account_id}/browser/credentials")
def forget_bank_login(account_id: uuid.UUID, db: DB, p: Principal = require("connections:manage")) -> dict:
    account = get_account(db, p.tenant_id, account_id)
    account.browser_identifier_encrypted = None
    audit(db, action="browser.credentials.forget", tenant_id=p.tenant_id, actor_user_id=p.user_id,
          entity_type="bank_account", entity_id=account.id)
    return {"saved": False, "has_customer_id": False, "has_password": False}


def profile_for(db, tenant_id, key):
    try:
        return find_profile(db, tenant_id, key)
    except StopIteration:
        raise NotFound("Bank browser profile not found") from None


@router.get("/banks/browser-profiles")
def list_profiles(db: DB, p: Principal = require("accounts:read"), country: str | None = None,
                  q: str = Query(default="", max_length=100), currency: str | None = None,
                  account_type: str | None = None) -> list[dict]:
    return [x.public() for x in profiles_for(db, p.tenant_id)
            if (not country or x.country == country.upper())
            and (not currency or currency.upper() in x.currencies)
            and (not account_type or account_type in x.account_types)
            and (not q or q.lower() in " ".join([x.key, x.name, x.website, *x.aliases]).lower())]


@router.put("/banks/browser-profiles/{key}")
def save_profile(key: str, body: BrowserProfile, db: DB, p: Principal = require("settings:manage")) -> dict:
    if key != body.key:
        raise ValidationFailed("Profile key does not match the URL")
    row = db.scalar(select(SystemSetting).where(SystemSetting.tenant_id == p.tenant_id,
                                                SystemSetting.key == f"browser_profile:{key}").with_for_update())
    if row is None:
        row = SystemSetting(tenant_id=p.tenant_id, key=f"browser_profile:{key}")
        db.add(row)
    row.value = body.model_dump(mode="json")
    row.updated_by = p.user_id
    audit(db, action="browser.profile.save", tenant_id=p.tenant_id, actor_user_id=p.user_id,
          entity_type="browser_profile", meta={"key": key, "permission": body.permission, "reviewed": body.reviewed})
    return body.public()


@router.get("/accounts/{account_id}/browser")
def browser_status(account_id: uuid.UUID, db: DB, p: Principal = require("accounts:read")) -> dict:
    a = get_account(db, p.tenant_id, account_id)
    options = [x for x in profiles_for(db, p.tenant_id) if x.country == a.country_code]
    stored = decrypt_json(a.browser_identifier_encrypted, str(p.tenant_id)) if a.browser_identifier_encrypted else {}
    preferred_key = stored.get("profile_key") or a.browser_profile_key
    preferred = next((x for x in options if x.key == preferred_key), None)
    if preferred is None:
        name = a.institution.name.casefold().strip()
        preferred = next((x for x in options if name in [x.name.casefold(), *[s.casefold() for s in x.aliases]]), None)
    from app.models.automation import AutomationJob
    job = db.scalar(select(AutomationJob).where(AutomationJob.tenant_id == p.tenant_id,
                                               AutomationJob.account_id == a.id,
                                               AutomationJob.job_type.in_(["open_bank_login", "collect_account"]))
                    .order_by(AutomationJob.created_at.desc()).limit(1))
    credentials = scoped_credentials(a, preferred) if preferred else {}
    return {"enabled": get_settings().browser_automation_enabled,
            "profile_key": preferred.key if preferred else None,
            "has_customer_id": bool(credentials.get("customer_id")),
            "has_password": bool(credentials.get("password")),
            "profiles": [x.public() for x in options],
            "job": {"id": str(job.id), "status": job.status, "message": job.progress_message,
                    "error": job.error_message, "result": job.result, "error_code": job.error_code,
                    "requires_user_action": job.requires_user_action} if job else None}


@router.post("/accounts/{account_id}/browser/start", status_code=202)
def start_browser(account_id: uuid.UUID, body: BrowserStart, db: DB,
                  p: Principal = require("connections:manage")) -> dict:
    if not get_settings().browser_automation_enabled:
        raise Conflict("Enable BROWSER_AUTOMATION_ENABLED on the local Windows desktop first",
                       code="browser_disabled")
    a = get_account(db, p.tenant_id, account_id)
    # Serialise connection changes and queue creation for this account.
    db.execute(select(type(a).id).where(type(a).id == a.id).with_for_update())
    profile = profile_for(db, p.tenant_id, body.profile_key)
    if profile.country != a.country_code or (profile.currencies and a.currency not in profile.currencies):
        raise ValidationFailed("Bank profile country/currency does not match this account")
    if a.account_type not in profile.account_types:
        raise ValidationFailed("Bank profile does not support this account type")
    if a.is_demo:
        raise ValidationFailed("Real browser connections cannot be used for demo accounts")
    if not body.login_only and not profile.retrieval_supported:
        raise Conflict("This bank's statement workflow is unverified. Open official login or use statement upload.",
                       code="browser_profile_review_required")
    credentials = scoped_credentials(a, profile)
    if body.authenticate:
        if body.customer_id is not None and body.customer_id != credentials.get("customer_id"):
            raise ValidationFailed("Save the changed Customer ID and password before authenticating")
        if not profile.public()["capabilities"]["saved_login"]:
            raise Conflict("Login fields require review before saved authentication", code="login_review_required")
        if not credentials.get("customer_id") or not credentials.get("password"):
            raise ValidationFailed("Save your Customer ID and password before pressing Authenticate",
                                   code="saved_login_required")
    from app.models.automation import AutomationJob
    active = db.scalar(select(AutomationJob).where(AutomationJob.account_id == a.id,
                       AutomationJob.status.in_(queue.ACTIVE_STATES),
                       AutomationJob.job_type.in_(["collect_account", "open_bank_login"])).limit(1))
    if active:
        if active.status == "awaiting_user" and ((body.login_only and active.payload.get("profile_key") == profile.key)
                                                 or (not body.login_only and a.browser_profile_key == profile.key)):
            active.payload = {**active.payload, "interactive_approved": not body.login_only or body.authenticate,
                              "authenticate": body.authenticate}
            active.status, active.run_after, active.requires_user_action = "queued", queue.now(), None
            active.error_code = active.error_message = None
        return {"job_id": str(active.id), "created": False, "status": active.status}
    end = body.date_to or datetime.now(ZoneInfo(a.timezone or "UTC")).date()
    start = body.date_from or end - timedelta(days=30)
    if start > end or (end - start).days > 730:
        raise ValidationFailed("Choose a statement period of at most 730 days")
    if body.customer_id is not None:
        if credentials.get("customer_id") != body.customer_id:
            credentials.pop("password", None)
        credentials.update(customer_id=body.customer_id, scope=profile.credential_scope, profile_key=profile.key)
        a.browser_identifier_encrypted = encrypt_json(credentials, str(p.tenant_id))
    if not body.login_only:
        a.browser_profile_key = profile.key
        provider = db.scalar(select(BankProvider).where(BankProvider.code == "browser_automation"))
        if provider is None:
            raise Conflict("Restart the platform to initialise the browser provider")
        if a.connection is None or a.connection.provider.code != provider.code:
            conn = BankConnection(tenant_id=p.tenant_id, institution_id=a.institution_id, provider_id=provider.id,
                                  status="requires_auth", connection_method="permitted_browser_automation",
                                  environment="production", is_demo=False)
            db.add(conn)
            db.flush()
            a.connection_id = conn.id
            a.connection = conn
        a.connection_health = "unknown"
    job, created = queue.enqueue(db, tenant_id=p.tenant_id,
                                job_type="open_bank_login" if body.login_only else "collect_account",
                                account_id=a.id, payload={"profile_key": profile.key,
                                "date_from": start.isoformat(), "date_to": end.isoformat(), "authenticate": body.authenticate},
                                max_attempts=1, created_by=p.user_id)
    audit(db, action="browser.login.open" if body.login_only else "browser.sync.start", tenant_id=p.tenant_id,
          actor_user_id=p.user_id, entity_type="bank_account", entity_id=a.id, meta={"profile_key": profile.key})
    return {"job_id": str(job.id), "created": created, "status": job.status}
