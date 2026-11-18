"""Idempotent reference data: countries, permissions, system roles, provider catalogue."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import PERMISSIONS, SYSTEM_ROLES
from app.integrations.countries.catalogue import COUNTRIES
from app.integrations.registry import PROVIDER_CATALOGUE
from app.models.automation import AutomationSettings
from app.models.banking import BankProvider, Country, Institution
from app.models.identity import Organization, Permission, Role
from app.transactions.categorizer import ensure_default_categories


def seed_reference_data(db: Session) -> None:
    existing = {c.code: c for c in db.scalars(select(Country))}
    for c in COUNTRIES:
        if c["code"] in existing:
            for k, v in c.items():
                setattr(existing[c["code"]], k, v)
        else:
            db.add(Country(**c))

    perms = {p.code: p for p in db.scalars(select(Permission))}
    for code, desc in PERMISSIONS.items():
        if code not in perms:
            perms[code] = Permission(code=code, description=desc)
            db.add(perms[code])
    db.flush()

    for code, spec in SYSTEM_ROLES.items():
        role = db.scalar(select(Role).where(Role.tenant_id.is_(None), Role.code == code))
        if role is None:
            role = Role(tenant_id=None, code=code, name=spec["name"], is_system=True)
            db.add(role)
        role.permissions = [perms[p] for p in dict.fromkeys(spec["permissions"])]

    for spec in PROVIDER_CATALOGUE:
        prov = db.scalar(select(BankProvider).where(BankProvider.code == spec.code))
        values = dict(name=spec.name, provider_type=spec.provider_type, countries=spec.countries,
                      currencies=spec.currencies, capabilities=spec.capabilities, auth_method=spec.auth_method,
                      consent_required=spec.consent_required, refresh_frequency=spec.refresh_frequency,
                      integration_status=spec.integration_status, is_demo=spec.is_demo,
                      documentation_url=spec.documentation_url,
                      min_request_interval_seconds=spec.min_request_interval_seconds, notes=spec.notes)
        if prov is None:
            db.add(BankProvider(code=spec.code, **values))
        else:
            for k, v in values.items():
                setattr(prov, k, v)
    db.flush()


def seed_demo_institutions(db: Session) -> None:
    from app.integrations.providers.demo import DEMO_INSTITUTIONS

    demo = db.scalar(select(BankProvider).where(BankProvider.code == "demo"))
    assert demo is not None
    for inst in DEMO_INSTITUTIONS:
        exists = db.scalar(select(Institution).where(Institution.provider_id == demo.id,
                                                     Institution.external_id == inst.external_id,
                                                     Institution.tenant_id.is_(None)))
        if exists is None:
            db.add(Institution(tenant_id=None, provider_id=demo.id, country_code=inst.country_code, name=inst.name,
                               external_id=inst.external_id, bic=inst.bic, supported_currencies=inst.currencies,
                               supports_balances=True, supports_transactions=True, supports_statements=True,
                               is_demo=True))
    db.flush()


def bootstrap_tenant(db: Session, tenant_id: uuid.UUID) -> None:
    ensure_default_categories(db, tenant_id)
    org = db.get(Organization, tenant_id)
    if db.scalar(select(AutomationSettings).where(AutomationSettings.tenant_id == tenant_id)) is None:
        db.add(AutomationSettings(tenant_id=tenant_id, timezone=org.timezone if org else "Europe/London"))
    db.flush()


def system_role(db: Session, code: str) -> Role:
    role = db.scalar(select(Role).where(Role.tenant_id.is_(None), Role.code == code))
    if role is None:
        raise RuntimeError(f"System role {code} missing — run seed")
    return role
