"""Clearly labelled demo environment.

Creates demo connections/accounts on the Demo Bank Simulator, demo FX rates and a
deterministic demo cash-book so reconciliation has something to match. Everything
is flagged ``is_demo`` and appears with a DEMO badge in every screen and report.
"""
from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.api.deps import Principal
from app.audit.service import record as audit
from app.core.config import get_settings
from app.core.crypto import encrypt_json
from app.core.errors import Forbidden
from app.integrations.providers.demo import DEMO_INSTITUTIONS, _ledger
from app.models.banking import BankAccount, BankConnection, BankProvider, ExchangeRate, Institution
from app.models.identity import Organization
from app.models.transactions import BookEntry
from app.services.seed import seed_demo_institutions

DEMO_RATES_TO_GBP = {"USD": "0.7900", "EUR": "0.8550", "INR": "0.009450", "AED": "0.2150"}
DEMO_RATES_USD = {"GBP": "1.2650", "EUR": "1.0820", "INR": "0.011960", "AED": "0.2723"}


def seed_demo(db: Session, p: Principal) -> dict[str, int]:
    if not get_settings().demo_allowed:
        raise Forbidden("Demo data is disabled in this environment")
    seed_demo_institutions(db)
    org = db.get(Organization, p.tenant_id)
    demo = db.scalar(select(BankProvider).where(BankProvider.code == "demo"))
    created = 0
    for spec in DEMO_INSTITUTIONS:
        inst = db.scalar(select(Institution).where(Institution.provider_id == demo.id,
                                                   Institution.external_id == spec.external_id,
                                                   Institution.tenant_id.is_(None)))
        exists = db.scalar(select(BankAccount.id).where(BankAccount.tenant_id == p.tenant_id,
                                                        BankAccount.institution_id == inst.id,
                                                        BankAccount.deleted_at.is_(None)))
        if exists:
            continue
        conn = BankConnection(tenant_id=p.tenant_id, institution_id=inst.id, provider_id=demo.id, status="active",
                              is_demo=True, encrypted_credentials=encrypt_json({"scenario": "ok"}, str(p.tenant_id)),
                              last_success_at=None)
        db.add(conn)
        db.flush()
        ccy = spec.currencies[0]
        for ext, name, mask, kind, thr in (("primary", "Operating", "••••4821", "current", "25000"),
                                           ("reserve", "Reserve", "••••9034", "savings", None)):
            acc = BankAccount(tenant_id=p.tenant_id, institution_id=inst.id, connection_id=conn.id,
                              nickname=f"{spec.name.split(' Demo')[0]} {name} ({ccy})", holder_name="Demo Organisation",
                              account_type=kind, currency=ccy, country_code=spec.country_code,
                              account_number_masked=mask, external_account_id=ext, automation_enabled=True,
                              low_balance_threshold=Decimal(thr) * (80 if ccy == "INR" else 1) if thr else None,
                              is_demo=True, connection_health="healthy",
                              notes="DEMO account on the Demo Bank Simulator — synthetic data")
            db.add(acc)
            db.flush()
            created += 1
            if ext == "primary":
                _seed_book(db, acc, spec.external_id)

    reporting = org.reporting_currency if org else "GBP"
    rates = DEMO_RATES_TO_GBP if reporting == "GBP" else DEMO_RATES_USD if reporting == "USD" else {}
    today = datetime.now(UTC).date()
    for base, rate in rates.items():
        if base == reporting:
            continue
        db.execute(insert(ExchangeRate).values(
            tenant_id=p.tenant_id, base_currency=base, quote_currency=reporting, rate=Decimal(rate),
            rate_date=today, source="demo (illustrative)").on_conflict_do_nothing())
    if org:
        org.demo_mode = True
    audit(db, action="demo.seed", tenant_id=p.tenant_id, actor_user_id=p.user_id, meta={"accounts": created})
    return {"accounts_created": created}


def _seed_book(db: Session, acc: BankAccount, inst_ext: str) -> None:
    """Cash-book mirroring the bank ledger with realistic imperfections (deterministic)."""
    today = datetime.now(UTC).date()
    start = today - timedelta(days=75)
    _, rows = _ledger(f"{inst_ext}:primary", acc.currency, today)
    rnd = random.Random(f"book:{inst_ext}")
    for d, _vd, desc, ref, amt, _bal in rows:
        if d < start:
            continue
        roll = rnd.random()
        if roll < 0.05:
            continue  # missing in book → unmatched bank item
        entry_date, reference, amount, description = d, ref, amt, desc
        if roll < 0.12:
            reference = None
            entry_date = d + timedelta(days=rnd.choice([-2, -1, 1, 2]))
        elif roll < 0.15:
            amount = (amt * Decimal("1.0")) + Decimal(rnd.choice(["-0.50", "1.00", "-10.00"]))
        db.add(BookEntry(tenant_id=acc.tenant_id, account_id=acc.id, entry_date=entry_date, description=description,
                         reference=reference, external_ref=f"DEMO-{ref}", amount=amount, currency=acc.currency,
                         source="demo", is_demo=True))
    for i in range(3):  # book-only items (e.g. un-presented cheques)
        d = start + timedelta(days=10 + i * 17)
        db.add(BookEntry(tenant_id=acc.tenant_id, account_id=acc.id, entry_date=d,
                         description=f"Unpresented cheque {100200 + i}", reference=f"CHQ{100200 + i}",
                         external_ref=f"DEMO-CHQ{100200 + i}", amount=Decimal(-(1250 + i * 310)), currency=acc.currency,
                         source="demo", is_demo=True))
    db.flush()


def clear_demo(db: Session, p: Principal) -> dict[str, int]:
    now = datetime.now(UTC)
    accs = db.scalars(select(BankAccount).where(BankAccount.tenant_id == p.tenant_id, BankAccount.is_demo.is_(True),
                                                BankAccount.deleted_at.is_(None))).all()
    for a in accs:
        a.deleted_at, a.automation_enabled = now, False
        if a.connection:
            a.connection.status, a.connection.deleted_at = "disconnected", now
    db.execute(update(BookEntry).where(BookEntry.tenant_id == p.tenant_id, BookEntry.is_demo.is_(True),
                                       BookEntry.deleted_at.is_(None)).values(deleted_at=now))
    org = db.get(Organization, p.tenant_id)
    if org:
        org.demo_mode = False
    audit(db, action="demo.clear", tenant_id=p.tenant_id, actor_user_id=p.user_id, meta={"accounts": len(accs)})
    return {"accounts_removed": len(accs)}


__all__ = ["seed_demo", "clear_demo", "date"]
