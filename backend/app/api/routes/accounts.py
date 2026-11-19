from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, Response
from sqlalchemy import func, select

from app.api import serializers as S
from app.api.deps import DB, Principal, require
from app.audit.service import record as audit
from app.automation import service as automation_svc
from app.automation.service import set_account_automation
from app.automation.state import views_for
from app.integrations.methods import methods_for
from app.balances.service import latest_balances
from app.core.errors import ValidationFailed
from app.integrations.base.adapter import ProviderError
from app.integrations.registry import get_adapter
from app.models.automation import AutomationJob
from app.models.banking import AccountBalance, AccountConsent, BankAccount, BankConnection, ExchangeRate
from app.models.statements import Statement
from app.models.transactions import Transaction
from app.schemas.requests import (AccountAutomationAction, AccountCreate, AccountUpdate, AuthorizationStart,
                                  ConnectionMethodChange, DemoScenarioRequest, ExchangeRateCreate,
                                  GoCardlessRequisitionRequest, PlaidExchangeRequest, ToggleRequest)
from app.services import banking
from app.services import connections as conn_svc

router = APIRouter(tags=["accounts"])

from app.services.monitoring import MonitoringSettings, MonitoringUpdate  # noqa: E402


@router.get("/accounts/{account_id}/monitoring")
def get_monitoring(account_id: uuid.UUID, db: DB, p: Principal = require("accounts:read")) -> dict:
    from app.services.monitoring import settings_for, mail_ready
    account = banking.get_account(db, p.tenant_id, account_id)
    return {**settings_for(db, account).model_dump(), "email_configured": mail_ready()}


@router.put("/accounts/{account_id}/monitoring")
def update_monitoring(account_id: uuid.UUID, body: MonitoringUpdate, db: DB,
                      p: Principal = require("accounts:write")) -> dict:
    from app.services.monitoring import save_settings, mail_ready
    account = banking.get_account(db, p.tenant_id, account_id)
    settings = MonitoringSettings.model_validate(body.model_dump(exclude={"low_balance_threshold"}))
    save_settings(db, account, settings, p.user_id)
    if "low_balance_threshold" in body.model_fields_set:
        account.low_balance_threshold = body.low_balance_threshold
    if settings.email_enabled and account.low_balance_threshold is None:
        raise ValidationFailed("Set this account's minimum balance target before enabling balance emails")
    db.flush()
    from app.balances.service import check_low_balance
    check_low_balance(db, account)
    audit(db, action="account.monitoring.update", tenant_id=p.tenant_id, actor_user_id=p.user_id,
          entity_type="bank_account", entity_id=account.id, after=body.model_dump(mode="json"), ip=p.ip)
    return {**settings.model_dump(), "email_configured": mail_ready()}


@router.get("/accounts")
def list_accounts(db: DB, p: Principal = require("accounts:read"), q: str | None = Query(None, max_length=100),
                  currency: str | None = None, country: str | None = None, health: str | None = None,
                  demo: bool | None = None) -> list[dict]:
    query = select(BankAccount).where(BankAccount.tenant_id == p.tenant_id, BankAccount.deleted_at.is_(None))
    if q:
        query = query.where(BankAccount.nickname.ilike(f"%{q}%"))
    if currency:
        query = query.where(BankAccount.currency == currency.upper())
    if country:
        query = query.where(BankAccount.country_code == country.upper())
    if health:
        query = query.where(BankAccount.connection_health == health)
    if demo is not None:
        query = query.where(BankAccount.is_demo.is_(demo))
    accs = db.scalars(query.order_by(BankAccount.nickname)).all()
    bal = latest_balances(db, p.tenant_id, [a.id for a in accs]) if accs else []
    by: dict[uuid.UUID, list] = {}
    for b in bal:
        by.setdefault(b.account_id, []).append(b)
    views = views_for(db, p.tenant_id, list(accs))
    return [S.account(a, by.get(a.id), views.get(a.id)) for a in accs]


def _full(db, p, a: BankAccount) -> dict:
    return S.account(a, latest_balances(db, p.tenant_id, [a.id]), views_for(db, p.tenant_id, [a]).get(a.id))


@router.post("/accounts", status_code=201)
def create_account(body: AccountCreate, db: DB, p: Principal = require("accounts:write")) -> dict:
    acc = banking.create_account(db, p, body)
    db.refresh(acc)
    return _full(db, p, acc)


@router.get("/accounts/{account_id}")
def get_account(account_id: uuid.UUID, db: DB, p: Principal = require("accounts:read")) -> dict:
    a = banking.get_account(db, p.tenant_id, account_id)
    out = _full(db, p, a)
    out["stats"] = {
        "transactions": db.scalar(select(func.count(Transaction.id)).where(Transaction.account_id == a.id)) or 0,
        "statements": db.scalar(select(func.count(Statement.id)).where(Statement.account_id == a.id,
                                                                       Statement.deleted_at.is_(None))) or 0,
        "unreconciled": db.scalar(select(func.count(Transaction.id)).where(
            Transaction.account_id == a.id, Transaction.reconciliation_status.in_(("unreconciled", "exception",
                                                                                    "suggested")))) or 0,
    }
    since = (datetime.now(UTC) - timedelta(days=30)).date()
    cr, dr, cnt = db.execute(select(func.coalesce(func.sum(Transaction.credit), 0),
                                    func.coalesce(func.sum(Transaction.debit), 0), func.count(Transaction.id))
                             .where(Transaction.account_id == a.id, Transaction.tenant_id == p.tenant_id,
                                    Transaction.booking_date >= since, Transaction.currency == a.currency)).one()
    out["stats"]["cash_flow_30d"] = {"credits": S.money(cr), "debits": S.money(dr), "net": S.money(cr - dr),
                                     "count": cnt, "currency": a.currency}
    jobs = db.scalars(select(AutomationJob).where(AutomationJob.account_id == a.id)
                      .order_by(AutomationJob.created_at.desc()).limit(10)).all()
    out["recent_jobs"] = [S.job(j) for j in jobs]
    return out


@router.patch("/accounts/{account_id}")
def update_account(account_id: uuid.UUID, body: AccountUpdate, db: DB, p: Principal = require("accounts:write")
                   ) -> dict:
    a = banking.update_account(db, p, account_id, body)
    return _full(db, p, a)


@router.delete("/accounts/{account_id}", status_code=204, response_class=Response, response_model=None)
def delete_account(account_id: uuid.UUID, db: DB, p: Principal = require("accounts:write")) -> None:
    banking.delete_account(db, p, account_id)


@router.post("/accounts/{account_id}/automation")
def toggle_account_automation(account_id: uuid.UUID, body: ToggleRequest, db: DB,
                              p: Principal = require("automation:manage")) -> dict:
    return _full(db, p, set_account_automation(db, p.tenant_id, p.user_id, account_id, body.enabled))


@router.post("/accounts/{account_id}/automation/action", summary="Enable, pause, resume or disable automation")
def account_automation_action(account_id: uuid.UUID, body: AccountAutomationAction, db: DB,
                              p: Principal = require("automation:manage")) -> dict:
    return _full(db, p, automation_svc.account_action(db, p.tenant_id, p.user_id, account_id, body.action))


@router.post("/accounts/{account_id}/sync", status_code=202, summary="Run sync now for one account")
def account_sync_now(account_id: uuid.UUID, db: DB, p: Principal = require("automation:manage")) -> dict:
    return automation_svc.sync_account_now(db, p.tenant_id, p.user_id, account_id)


@router.get("/accounts/{account_id}/sync-history")
def sync_history(account_id: uuid.UUID, db: DB, p: Principal = require("accounts:read"),
                 page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)) -> dict:
    a = banking.get_account(db, p.tenant_id, account_id)
    q = select(AutomationJob).where(AutomationJob.tenant_id == p.tenant_id, AutomationJob.account_id == a.id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(AutomationJob.created_at.desc()).offset((page - 1) * page_size)
                      .limit(page_size)).all()
    return S.page([S.job(j, a.nickname) for j in rows], total, page, page_size)


# ---- connection methods & authorisation -------------------------------------------
@router.get("/banks/connection-methods", summary="Genuinely available connection methods for a bank")
def connection_methods(db: DB, p: Principal = require("accounts:read"), country: str = Query(..., min_length=2,
                                                                                               max_length=2),
                       institution_id: uuid.UUID | None = None,
                       bank_name: str | None = Query(None, max_length=200)) -> list[dict]:
    inst = banking.visible_institution(db, p.tenant_id, institution_id) if institution_id else None
    return methods_for(db, country_code=country.upper(), institution=inst, bank_name=bank_name, tenant_id=p.tenant_id)


@router.get("/accounts/{account_id}/connection-methods")
def account_connection_methods(account_id: uuid.UUID, db: DB, p: Principal = require("accounts:read")) -> dict:
    a = banking.get_account(db, p.tenant_id, account_id)
    consent = None
    if a.connection is not None:
        c = db.scalar(select(AccountConsent).where(AccountConsent.connection_id == a.connection.id)
                      .order_by(AccountConsent.created_at.desc()).limit(1))
        if c is not None:
            consent = {"status": c.status, "granted_at": c.granted_at, "expires_at": c.expires_at,
                       "authorisation_url": c.authorisation_url if c.status == "pending" else None,
                       "created_at": c.created_at}
    return {"current_provider": a.connection.provider.code if a.connection else None,
            "consent": consent,
            "methods": methods_for(db, country_code=a.country_code, institution=a.institution, tenant_id=p.tenant_id)}


@router.post("/accounts/{account_id}/connection-method", summary="Change how an existing account connects")
def change_connection_method(account_id: uuid.UUID, body: ConnectionMethodChange, db: DB,
                             p: Principal = require("connections:manage")) -> dict:
    a = conn_svc.switch_method(db, p, account_id, body.provider_code)
    db.flush()
    return _full(db, p, a)


@router.post("/accounts/{account_id}/authorize", summary="Start the provider-hosted authorisation")
def start_authorization(account_id: uuid.UUID, body: AuthorizationStart, db: DB,
                        p: Principal = require("connections:manage")) -> dict:
    return conn_svc.start_authorization(db, p, account_id, mobile=body.mobile, redirect_url=body.redirect_url)


@router.post("/accounts/{account_id}/authorize/verify", summary="Check the real authorisation status with the provider")
def verify_authorization(account_id: uuid.UUID, db: DB, p: Principal = require("connections:manage")) -> dict:
    a = conn_svc.verify_authorization(db, p, account_id)
    db.flush()
    return _full(db, p, a)


# ---- connections -----------------------------------------------------------
@router.get("/connections")
def list_connections(db: DB, p: Principal = require("accounts:read")) -> list[dict]:
    cs = db.scalars(select(BankConnection).where(BankConnection.tenant_id == p.tenant_id,
                                                 BankConnection.deleted_at.is_(None))
                    .order_by(BankConnection.created_at)).all()
    return [S.connection(c) for c in cs]


@router.post("/connections/{connection_id}/check")
def check_connection(connection_id: uuid.UUID, db: DB, p: Principal = require("connections:manage")) -> dict:
    return S.connection(banking.check(db, p, connection_id))


@router.post("/connections/{connection_id}/disconnect")
def disconnect(connection_id: uuid.UUID, db: DB, p: Principal = require("connections:manage")) -> dict:
    return S.connection(banking.disconnect(db, p, connection_id))


@router.post("/connections/{connection_id}/reconnect")
def reconnect(connection_id: uuid.UUID, db: DB, p: Principal = require("connections:manage")) -> dict:
    return S.connection(banking.reconnect(db, p, connection_id))


@router.post("/connections/{connection_id}/demo-scenario", summary="Demo only: simulate provider behaviour")
def demo_scenario(connection_id: uuid.UUID, body: DemoScenarioRequest, db: DB,
                  p: Principal = require("connections:manage")) -> dict:
    return S.connection(banking.set_demo_scenario(db, p, connection_id, body.scenario))


@router.post("/connections/{connection_id}/plaid/link-token")
def plaid_link_token(connection_id: uuid.UUID, db: DB, p: Principal = require("connections:manage")) -> dict:
    c = banking.get_connection(db, p.tenant_id, connection_id)
    if c.provider.code != "plaid":
        raise ValidationFailed("Not a Plaid connection")
    try:
        token = get_adapter("plaid").create_link_token(str(p.user_id), [c.institution.country_code])  # type: ignore
    except ProviderError as exc:
        raise ValidationFailed(str(exc), code=exc.code) from exc
    return {"link_token": token}


@router.post("/connections/{connection_id}/plaid/exchange")
def plaid_exchange(connection_id: uuid.UUID, body: PlaidExchangeRequest, db: DB,
                   p: Principal = require("connections:manage")) -> dict:
    c = banking.get_connection(db, p.tenant_id, connection_id)
    if c.provider.code != "plaid":
        raise ValidationFailed("Not a Plaid connection")
    try:
        tokens = get_adapter("plaid").exchange_public_token(body.public_token)  # type: ignore[attr-defined]
    except ProviderError as exc:
        raise ValidationFailed(str(exc), code=exc.code) from exc
    banking.store_credentials(db, p, c, {"access_token": tokens["access_token"]}, external_reference=tokens["item_id"])
    c.status = "active"
    if body.account_external_id:
        for a in db.scalars(select(BankAccount).where(BankAccount.connection_id == c.id)):
            a.external_account_id = body.account_external_id
    db.add(AccountConsent(tenant_id=p.tenant_id, connection_id=c.id, consent_reference=tokens["item_id"],
                          scopes=["balances", "transactions"], status="granted", granted_at=datetime.now(UTC)))
    return S.connection(c)


@router.post("/connections/{connection_id}/gocardless/requisition")
def gocardless_requisition(connection_id: uuid.UUID, body: GoCardlessRequisitionRequest, db: DB,
                           p: Principal = require("connections:manage")) -> dict:
    c = banking.get_connection(db, p.tenant_id, connection_id)
    if c.provider.code != "gocardless_bad":
        raise ValidationFailed("Not a GoCardless connection")
    try:
        req = get_adapter("gocardless_bad").create_requisition(  # type: ignore[attr-defined]
            c.institution.external_id, body.redirect_url, str(c.id))
    except ProviderError as exc:
        raise ValidationFailed(str(exc), code=exc.code) from exc
    c.external_reference, c.status = req["requisition_id"], "pending"
    db.add(AccountConsent(tenant_id=p.tenant_id, connection_id=c.id, consent_reference=req["requisition_id"],
                          scopes=["balances", "details", "transactions"], status="pending",
                          authorisation_url=req["link"], expires_at=datetime.now(UTC) + timedelta(days=90)))
    audit(db, action="connection.consent_requested", tenant_id=p.tenant_id, actor_user_id=p.user_id,
          entity_type="bank_connection", entity_id=c.id)
    return {"authorisation_url": req["link"], "connection": S.connection(c)}


# ---- balances ----------------------------------------------------------------
@router.get("/balances")
def balances(db: DB, p: Principal = require("accounts:read")) -> list[dict]:
    accs = {a.id: a for a in db.scalars(select(BankAccount).where(BankAccount.tenant_id == p.tenant_id,
                                                                  BankAccount.deleted_at.is_(None)))}
    rows = latest_balances(db, p.tenant_id, list(accs)) if accs else []
    out: dict[uuid.UUID, dict] = {aid: {"account_id": str(aid), "account_name": a.nickname, "currency": a.currency,
                                        "institution": a.institution.name, "is_demo": a.is_demo, "balances": {}}
                                  for aid, a in accs.items()}
    for b in rows:
        out[b.account_id]["balances"][b.balance_type] = S.balance(b)
    return list(out.values())


@router.get("/balances/{account_id}/history")
def balance_history(account_id: uuid.UUID, db: DB, p: Principal = require("accounts:read"),
                    balance_type: str | None = None, days: int = Query(90, ge=1, le=730)) -> list[dict]:
    banking.get_account(db, p.tenant_id, account_id)
    q = select(AccountBalance).where(AccountBalance.account_id == account_id, AccountBalance.tenant_id == p.tenant_id,
                                     AccountBalance.as_of >= datetime.now(UTC) - timedelta(days=days))
    if balance_type:
        q = q.where(AccountBalance.balance_type == balance_type)
    return [S.balance(b) for b in db.scalars(q.order_by(AccountBalance.as_of.desc()).limit(2000))]


@router.get("/balances/exchange-rates")
def list_rates(db: DB, p: Principal = require("accounts:read")) -> list[dict]:
    rs = db.scalars(select(ExchangeRate).where(ExchangeRate.tenant_id == p.tenant_id)
                    .order_by(ExchangeRate.rate_date.desc(), ExchangeRate.base_currency).limit(500)).all()
    return [{"id": str(r.id), "base_currency": r.base_currency, "quote_currency": r.quote_currency,
             "rate": str(r.rate), "rate_date": r.rate_date, "source": r.source} for r in rs]


@router.post("/balances/exchange-rates", status_code=201)
def add_rate(body: ExchangeRateCreate, db: DB, p: Principal = require("settings:manage")) -> dict:
    if body.base_currency == body.quote_currency:
        raise ValidationFailed("Base and quote currencies must differ")
    r = ExchangeRate(tenant_id=p.tenant_id, **body.model_dump())
    db.add(r)
    db.flush()
    audit(db, action="fx.rate.create", tenant_id=p.tenant_id, actor_user_id=p.user_id, entity_type="exchange_rate",
          entity_id=r.id, after=body.model_dump(mode="json"))
    return {"id": str(r.id)}
