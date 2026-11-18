from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response
from sqlalchemy import func, or_, select

from app.api import serializers as S
from app.api.deps import DB, Principal, require
from app.audit.service import record as audit
from app.core.config import get_settings
from app.core.errors import NotFound, ValidationFailed
from app.integrations.base.adapter import Capability, ProviderError
from app.integrations.registry import get_adapter
from app.models.banking import BankAccount, BankProvider, Country, Institution
from app.schemas.requests import InstitutionCreate

router = APIRouter(tags=["institutions"])


@router.get("/countries")
def countries(db: DB, p: Principal = require("accounts:read")) -> list[dict]:
    return [{"code": c.code, "name": c.name, "default_currency": c.default_currency, "region": c.region,
             "open_banking_framework": c.open_banking_framework}
            for c in db.scalars(select(Country).order_by(Country.name))]


@router.get("/banks/providers", summary="Integration provider catalogue with real capability/status metadata")
def providers(db: DB, p: Principal = require("accounts:read")) -> list[dict]:
    q = select(BankProvider).order_by(BankProvider.provider_type, BankProvider.name)
    items = [S.provider(x) for x in db.scalars(q)]
    s = get_settings()
    configured = {"plaid": bool(s.plaid_client_id and s.plaid_secret),
                  "gocardless_bad": bool(s.gocardless_secret_id and s.gocardless_secret_key),
                  "manual_upload": True, "demo": s.demo_allowed}
    for it in items:
        it["configured"] = configured.get(it["code"], False)
    return items


@router.get("/institutions")
def list_institutions(db: DB, p: Principal = require("accounts:read"), country: str | None = None,
                      provider: str | None = None, q: str | None = Query(default=None, max_length=100),
                      include_demo: bool = True, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)
                      ) -> dict:
    base = (select(Institution).join(BankProvider, BankProvider.id == Institution.provider_id)
            .where(Institution.deleted_at.is_(None),
                   or_(Institution.tenant_id.is_(None), Institution.tenant_id == p.tenant_id)))
    if country:
        base = base.where(Institution.country_code == country.upper())
    if provider:
        base = base.where(BankProvider.code == provider)
    if q:
        base = base.where(Institution.name.ilike(f"%{q}%"))
    if not include_demo or not get_settings().demo_allowed:
        base = base.where(Institution.is_demo.is_(False))
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(base.order_by(Institution.is_demo.desc(), Institution.name)
                      .offset((page - 1) * page_size).limit(page_size)).all()
    counts = dict(db.execute(select(BankAccount.institution_id, func.count()).where(
        BankAccount.tenant_id == p.tenant_id, BankAccount.deleted_at.is_(None)).group_by(BankAccount.institution_id)
    ).all())
    items = [{**S.institution(i), "account_count": counts.get(i.id, 0)} for i in rows]
    return S.page(items, total, page, page_size)


@router.post("/institutions", status_code=201, summary="Add a bank that is accessed by statement upload")
def create_institution(body: InstitutionCreate, db: DB, p: Principal = require("accounts:write")) -> dict:
    if db.get(Country, body.country_code) is None:
        raise ValidationFailed("Unknown country")
    manual = db.scalar(select(BankProvider).where(BankProvider.code == "manual_upload"))
    ext = f"custom:{uuid.uuid4().hex[:12]}"
    inst = Institution(tenant_id=p.tenant_id, provider_id=manual.id, country_code=body.country_code, name=body.name,
                       external_id=ext, bic=body.bic, supported_currencies=body.supported_currencies,
                       supports_statements=True)
    db.add(inst)
    db.flush()
    db.refresh(inst)
    audit(db, action="institution.create", tenant_id=p.tenant_id, actor_user_id=p.user_id,
          entity_type="institution", entity_id=inst.id, after={"name": inst.name, "country": inst.country_code})
    return S.institution(inst)


@router.delete("/institutions/{institution_id}", status_code=204, response_class=Response, response_model=None)
def delete_institution(institution_id: uuid.UUID, db: DB, p: Principal = require("accounts:write")) -> None:
    inst = db.scalar(select(Institution).where(Institution.id == institution_id, Institution.tenant_id == p.tenant_id))
    if inst is None:
        raise NotFound("Custom institution not found")
    if db.scalar(select(func.count()).where(BankAccount.institution_id == inst.id, BankAccount.deleted_at.is_(None))):
        raise ValidationFailed("Institution still has accounts")
    from datetime import UTC, datetime
    inst.deleted_at = datetime.now(UTC)
    audit(db, action="institution.delete", tenant_id=p.tenant_id, actor_user_id=p.user_id, entity_type="institution",
          entity_id=inst.id)


@router.post("/institutions/sync", summary="Import institution list from a configured provider")
def sync_institutions(db: DB, provider: str, country: str, p: Principal = require("connections:manage")) -> dict:
    prov = db.scalar(select(BankProvider).where(BankProvider.code == provider))
    if prov is None:
        raise NotFound("Provider not found")
    adapter = get_adapter(provider)
    if not adapter.supports(Capability.INSTITUTIONS):
        raise ValidationFailed("Provider does not publish an institution list")
    try:
        items = adapter.get_institutions(country.upper())
    except ProviderError as exc:
        raise ValidationFailed(str(exc), code=exc.code) from exc
    created = 0
    for it in items:
        exists = db.scalar(select(Institution.id).where(Institution.provider_id == prov.id,
                                                        Institution.external_id == it.external_id,
                                                        Institution.tenant_id.is_(None)))
        if exists:
            continue
        if db.get(Country, it.country_code) is None:
            continue
        db.add(Institution(tenant_id=None, provider_id=prov.id, country_code=it.country_code, name=it.name,
                           external_id=it.external_id, bic=it.bic, logo_url=it.logo_url,
                           supported_currencies=it.currencies, supports_balances=it.supports_balances,
                           supports_transactions=it.supports_transactions, supports_statements=it.supports_statements,
                           is_demo=prov.is_demo))
        created += 1
    audit(db, action="institution.sync", tenant_id=p.tenant_id, actor_user_id=p.user_id,
          meta={"provider": provider, "country": country, "created": created})
    return {"fetched": len(items), "created": created}
