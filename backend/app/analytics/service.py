"""Dashboard and analytics aggregations.

Amounts are always kept per original currency. Consolidated figures are produced only
through explicit exchange rates and returned together with the rates used; accounts
without a rate are listed as ``unconverted`` instead of being silently summed.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.balances.service import convert, latest_balances
from app.models.automation import AutomationJob, AutomationSettings
from app.models.banking import BankAccount, BankConnection
from app.models.identity import Organization
from app.models.reconciliation import ReconciliationSession
from app.models.statements import Statement
from app.models.transactions import Transaction, TransactionCategory


class Fx:
    def __init__(self, db: Session, tenant_id: uuid.UUID, reporting: str) -> None:
        self.db, self.tenant_id, self.reporting = db, tenant_id, reporting
        self._cache: dict[str, tuple[Decimal | None, dict | None]] = {}

    def rate(self, ccy: str) -> tuple[Decimal | None, dict | None]:
        if ccy == self.reporting:
            return Decimal(1), None
        if ccy not in self._cache:
            conv, meta = convert(self.db, self.tenant_id, Decimal("1000000"), ccy, self.reporting,
                                 datetime.now(UTC).date())
            self._cache[ccy] = ((conv / Decimal("1000000")) if conv is not None else None, meta)
        return self._cache[ccy]

    def to_reporting(self, amount: Decimal, ccy: str) -> Decimal | None:
        r, _ = self.rate(ccy)
        return None if r is None else (amount * r).quantize(Decimal("0.01"))

    def used(self) -> list[dict]:
        return [m for _, m in self._cache.values() if m]

    def missing(self) -> list[str]:
        return sorted(c for c, (r, _) in self._cache.items() if r is None)


def _f(v: Decimal | None) -> float | None:
    return float(v) if v is not None else None


def _org(db: Session, tenant_id: uuid.UUID) -> Organization:
    org = db.get(Organization, tenant_id)
    assert org is not None
    return org


def _automation_counts(db: Session, tenant_id: uuid.UUID, accounts: list[BankAccount]) -> dict[str, int]:
    from app.automation.state import views_for

    counts: dict[str, int] = {}
    for v in views_for(db, tenant_id, accounts).values():
        counts[v["status"]] = counts.get(v["status"], 0) + 1
    return counts


def summary(db: Session, tenant_id: uuid.UUID) -> dict[str, Any]:
    org = _org(db, tenant_id)
    fx = Fx(db, tenant_id, org.reporting_currency)
    accounts = list(db.scalars(select(BankAccount).where(BankAccount.tenant_id == tenant_id,
                                                         BankAccount.deleted_at.is_(None))))
    acc_ids = [a.id for a in accounts]
    conns = list(db.scalars(select(BankConnection).where(BankConnection.tenant_id == tenant_id,
                                                         BankConnection.deleted_at.is_(None))))
    active_conns = [c for c in conns if c.status == "active"]
    failed_conns = [c for c in conns if c.status in ("error", "requires_auth", "consent_expired")]

    # balances by type
    bal = latest_balances(db, tenant_id, acc_ids) if acc_ids else []
    by_type: dict[str, dict[str, Any]] = {}
    for t in ("available", "ledger", "current"):
        items = [b for b in bal if b.balance_type == t]
        per_ccy: dict[str, Decimal] = defaultdict(Decimal)
        for b in items:
            per_ccy[b.currency] += b.amount
        converted, unconverted = Decimal(0), []
        for c, amt in per_ccy.items():
            v = fx.to_reporting(amt, c)
            if v is None:
                unconverted.append(c)
            else:
                converted += v
        by_type[t] = {"accounts": len(items), "by_currency": {c: _f(v) for c, v in per_ccy.items()},
                      "total_reporting": _f(converted) if items else None, "unconverted_currencies": unconverted,
                      "oldest_as_of": min((b.as_of for b in items), default=None)}
    # reported = best available per account
    best: dict[uuid.UUID, Any] = {}
    pref = ["available", "current", "ledger", "statement_closing"]
    for b in bal:
        if b.balance_type in pref and (b.account_id not in best or pref.index(b.balance_type) <
                                       pref.index(best[b.account_id].balance_type)):
            best[b.account_id] = b
    rep_total, rep_unconv = Decimal(0), set()
    for b in best.values():
        v = fx.to_reporting(b.amount, b.currency)
        if v is None:
            rep_unconv.add(b.currency)
        else:
            rep_total += v

    since = datetime.now(UTC).date() - timedelta(days=30)
    flows = db.execute(select(Transaction.currency, func.sum(Transaction.credit), func.sum(Transaction.debit),
                              func.count(Transaction.id))
                       .where(Transaction.tenant_id == tenant_id, Transaction.booking_date >= since)
                       .group_by(Transaction.currency)).all()
    cr = dr = Decimal(0)
    flow_unconv = []
    for ccy, c, d, _ in flows:
        vc, vd = fx.to_reporting(c or 0, ccy), fx.to_reporting(d or 0, ccy)
        if vc is None or vd is None:
            flow_unconv.append(ccy)
            continue
        cr, dr = cr + vc, dr + vd
    today = datetime.now(UTC).date()
    today_count = db.scalar(select(func.count(Transaction.id)).where(Transaction.tenant_id == tenant_id,
                                                                     Transaction.booking_date == today)) or 0
    last_day = db.scalar(select(func.max(Transaction.booking_date)).where(Transaction.tenant_id == tenant_id))
    last_day_count = db.scalar(select(func.count(Transaction.id)).where(Transaction.tenant_id == tenant_id,
                                                                        Transaction.booking_date == last_day)) \
        if last_day else 0

    recon = dict(db.execute(select(Transaction.reconciliation_status, func.count())
                            .where(Transaction.tenant_id == tenant_id).group_by(Transaction.reconciliation_status)).all())
    s = db.scalar(select(AutomationSettings).where(AutomationSettings.tenant_id == tenant_id))
    jobs24 = dict(db.execute(select(AutomationJob.status, func.count()).where(
        AutomationJob.tenant_id == tenant_id, AutomationJob.created_at >= datetime.now(UTC) - timedelta(hours=24))
        .group_by(AutomationJob.status)).all())
    pending_st = db.scalar(select(func.count(Statement.id)).where(
        Statement.tenant_id == tenant_id, Statement.deleted_at.is_(None),
        Statement.processing_status.in_(("pending", "processing", "needs_review", "needs_ocr")))) or 0
    last_recon = db.scalars(select(ReconciliationSession).where(ReconciliationSession.tenant_id == tenant_id)
                            .order_by(ReconciliationSession.created_at.desc()).limit(50)).all()

    return {
        "reporting_currency": org.reporting_currency,
        "demo_accounts": sum(1 for a in accounts if a.is_demo),
        "live_accounts": sum(1 for a in accounts if not a.is_demo),
        "connected_banks": len({c.institution_id for c in active_conns}),
        "total_accounts": len(accounts),
        "balances": by_type,
        "reported_balance": {"total_reporting": _f(rep_total) if best else None, "accounts": len(best),
                             "unconverted_currencies": sorted(rep_unconv)},
        "credits_30d": _f(cr), "debits_30d": _f(dr), "flows_unconverted_currencies": flow_unconv,
        "transactions_30d": sum(n for *_, n in flows),
        "transactions_today": today_count, "last_activity_date": last_day, "last_activity_count": last_day_count,
        "reconciliation": {"by_status": recon, "unmatched": recon.get("exception", 0),
                           "suggested": recon.get("suggested", 0),
                           "sessions_balanced": sum(1 for x in last_recon if x.status == "balanced"),
                           "sessions_with_exceptions": sum(1 for x in last_recon
                                                           if x.status == "completed_with_exceptions")},
        "automation": {"master_enabled": bool(s and s.master_enabled), "paused": bool(s and s.paused),
                       "next_run_at": s.next_run_at if s else None, "last_run_at": s.last_run_at if s else None,
                       "jobs_24h": jobs24},
        "failed_connections": [{"id": str(c.id), "institution": c.institution.name, "status": c.status,
                                "last_error": c.last_error} for c in failed_conns],
        "account_automation": _automation_counts(db, tenant_id, accounts),
        "pending_statements": pending_st,
        "fx_rates_used": fx.used(), "fx_missing": fx.missing(),
    }


def balance_history(db: Session, tenant_id: uuid.UUID, days: int = 90) -> dict[str, Any]:
    org = _org(db, tenant_id)
    fx = Fx(db, tenant_id, org.reporting_currency)
    start = datetime.now(UTC).date() - timedelta(days=days)
    accounts = {a.id: a for a in db.scalars(select(BankAccount).where(BankAccount.tenant_id == tenant_id,
                                                                      BankAccount.deleted_at.is_(None)))}
    rows = db.execute(select(Transaction.account_id, Transaction.booking_date, Transaction.running_balance)
                      .where(Transaction.tenant_id == tenant_id, Transaction.running_balance.is_not(None),
                             Transaction.booking_date >= start - timedelta(days=31))
                      .order_by(Transaction.account_id, Transaction.booking_date, Transaction.line_number.nulls_last(),
                                Transaction.created_at)).all()
    eod: dict[uuid.UUID, dict[date, Decimal]] = defaultdict(dict)
    for acc, d, bal in rows:
        eod[acc][d] = bal
    series = []
    current: dict[uuid.UUID, Decimal] = {}
    for acc, days_map in eod.items():
        prior = [d for d in days_map if d < start]
        if prior:
            current[acc] = days_map[max(prior)]
    d = start
    today = datetime.now(UTC).date()
    while d <= today:
        for acc, days_map in eod.items():
            if d in days_map:
                current[acc] = days_map[d]
        total = Decimal(0)
        for acc, v in current.items():
            conv = fx.to_reporting(v, accounts[acc].currency) if acc in accounts else None
            if conv is not None:
                total += conv
        # no data yet → a gap, never a fabricated zero balance
        series.append({"date": d.isoformat(), "balance": _f(total) if current else None})
        d += timedelta(days=1)
    return {"reporting_currency": org.reporting_currency, "series": series, "fx_missing": fx.missing(),
            "basis": "End-of-day running balances from transactions, converted at the latest available rate"}


def cash_flow(db: Session, tenant_id: uuid.UUID, days: int = 30) -> dict[str, Any]:
    org = _org(db, tenant_id)
    fx = Fx(db, tenant_id, org.reporting_currency)
    start = datetime.now(UTC).date() - timedelta(days=days - 1)
    rows = db.execute(select(Transaction.booking_date, Transaction.currency, func.sum(Transaction.credit),
                             func.sum(Transaction.debit))
                      .where(Transaction.tenant_id == tenant_id, Transaction.booking_date >= start)
                      .group_by(Transaction.booking_date, Transaction.currency)).all()
    agg: dict[date, list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0)])
    for d, ccy, c, dr in rows:
        vc, vd = fx.to_reporting(c or 0, ccy), fx.to_reporting(dr or 0, ccy)
        if vc is not None and vd is not None:
            agg[d][0] += vc
            agg[d][1] += vd
    out = []
    d = start
    while d <= datetime.now(UTC).date():
        inflow, outflow = agg.get(d, [Decimal(0), Decimal(0)])
        out.append({"date": d.isoformat(), "inflow": _f(inflow), "outflow": _f(outflow), "net": _f(inflow - outflow)})
        d += timedelta(days=1)
    return {"reporting_currency": org.reporting_currency, "series": out, "fx_missing": fx.missing()}


def monthly_activity(db: Session, tenant_id: uuid.UUID, months: int = 12) -> dict[str, Any]:
    org = _org(db, tenant_id)
    fx = Fx(db, tenant_id, org.reporting_currency)
    today = datetime.now(UTC).date()
    y, m = today.year, today.month - (months - 1)
    while m <= 0:
        m, y = m + 12, y - 1
    first = date(y, m, 1)
    month = func.date_trunc("month", Transaction.booking_date)
    rows = db.execute(select(month, Transaction.currency, func.sum(Transaction.credit), func.sum(Transaction.debit),
                             func.count()).where(Transaction.tenant_id == tenant_id, Transaction.booking_date >= first)
                      .group_by(month, Transaction.currency).order_by(month)).all()
    agg: dict[str, dict[str, Any]] = {}
    for m, ccy, c, d, n in rows:
        key = m.strftime("%Y-%m")
        e = agg.setdefault(key, {"month": key, "credits": Decimal(0), "debits": Decimal(0), "count": 0})
        vc, vd = fx.to_reporting(c or 0, ccy), fx.to_reporting(d or 0, ccy)
        if vc is not None and vd is not None:
            e["credits"] += vc
            e["debits"] += vd
        e["count"] += n
    return {"reporting_currency": org.reporting_currency,
            "series": [{**v, "credits": _f(v["credits"]), "debits": _f(v["debits"])} for v in agg.values()],
            "fx_missing": fx.missing()}


def distributions(db: Session, tenant_id: uuid.UUID) -> dict[str, Any]:
    org = _org(db, tenant_id)
    fx = Fx(db, tenant_id, org.reporting_currency)
    accounts = {a.id: a for a in db.scalars(select(BankAccount).where(BankAccount.tenant_id == tenant_id,
                                                                      BankAccount.deleted_at.is_(None)))}
    pref = ["available", "current", "ledger", "statement_closing"]
    best: dict[uuid.UUID, Any] = {}
    for b in latest_balances(db, tenant_id, list(accounts)) if accounts else []:
        if b.balance_type in pref and (b.account_id not in best or pref.index(b.balance_type) <
                                       pref.index(best[b.account_id].balance_type)):
            best[b.account_id] = b
    banks: dict[str, Decimal] = defaultdict(Decimal)
    ccys: dict[str, dict[str, Decimal]] = {}
    for acc_id, b in best.items():
        a = accounts[acc_id]
        conv = fx.to_reporting(b.amount, b.currency)
        if conv is not None:
            banks[a.institution.name] += conv
        e = ccys.setdefault(b.currency, {"original": Decimal(0), "converted": Decimal(0)})
        e["original"] += b.amount
        if conv is not None:
            e["converted"] += conv
    since = datetime.now(UTC).date() - timedelta(days=30)
    cats = db.execute(select(func.coalesce(TransactionCategory.name, "Uncategorised"),
                             func.coalesce(TransactionCategory.colour, "#94A3B8"), Transaction.currency,
                             func.sum(Transaction.debit), func.sum(Transaction.credit))
                      .outerjoin(TransactionCategory, TransactionCategory.id == Transaction.category_id)
                      .where(Transaction.tenant_id == tenant_id, Transaction.booking_date >= since)
                      .group_by(TransactionCategory.name, TransactionCategory.colour, Transaction.currency)).all()
    cat_agg: dict[str, dict[str, Any]] = {}
    for name, colour, ccy, d, c in cats:
        e = cat_agg.setdefault(name, {"name": name, "colour": colour, "debits": Decimal(0), "credits": Decimal(0)})
        vd, vc = fx.to_reporting(d or 0, ccy), fx.to_reporting(c or 0, ccy)
        e["debits"] += vd or 0
        e["credits"] += vc or 0
    return {
        "reporting_currency": org.reporting_currency,
        "banks": [{"name": k, "value": _f(v)} for k, v in sorted(banks.items(), key=lambda x: -x[1])],
        "currencies": [{"currency": k, "original": _f(v["original"]),
                        "converted": _f(v["converted"]) if fx.rate(k)[0] is not None else None}
                       for k, v in sorted(ccys.items())],
        "categories": sorted(({**v, "debits": _f(v["debits"]), "credits": _f(v["credits"])}
                              for v in cat_agg.values()), key=lambda x: -(x["debits"] or 0)),
        "fx_rates_used": fx.used(), "fx_missing": fx.missing(),
    }


def reconciliation_performance(db: Session, tenant_id: uuid.UUID, weeks: int = 12) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(weeks=weeks)
    wk = func.date_trunc("week", ReconciliationSession.created_at)
    rows = db.execute(select(wk, func.count(), func.sum(ReconciliationSession.matched_count),
                             func.sum(ReconciliationSession.bank_transaction_count),
                             func.sum(ReconciliationSession.exception_count))
                      .where(ReconciliationSession.tenant_id == tenant_id, ReconciliationSession.created_at >= since)
                      .group_by(wk).order_by(wk)).all()
    return {"series": [{"week": w.strftime("%Y-%m-%d"), "sessions": n, "matched": m or 0, "bank": b or 0,
                        "exceptions": e or 0, "match_rate": round((m or 0) / b, 4) if b else None}
                       for w, n, m, b, e in rows]}
