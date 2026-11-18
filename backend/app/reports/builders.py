"""Report data builders. Each returns a ReportData made of tabular sections."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.balances.service import convert, latest_balances
from app.models.automation import AutomationJob
from app.models.banking import BankAccount
from app.models.identity import Organization
from app.models.reconciliation import ReconciliationException, ReconciliationSession
from app.models.statements import Statement
from app.models.transactions import Transaction, TransactionCategory


@dataclass
class Section:
    name: str
    columns: list[str]
    rows: list[list[Any]]


@dataclass
class ReportData:
    title: str
    subtitle: str
    sections: list[Section] = field(default_factory=list)
    contains_demo: bool = False


REPORT_TYPES = {
    "daily": "Daily banking report", "weekly": "Weekly banking report", "monthly": "Monthly banking report",
    "bank": "Bank-wise report", "account": "Account-wise report", "transactions": "Transaction report",
    "balances": "Balance report", "credit_debit": "Credit / debit report",
    "reconciliation": "Reconciliation report", "failed_automation": "Failed automation report",
    "missing_statements": "Missing statement report", "currency": "Currency report",
}


def _period(report_type: str, p: dict[str, Any]) -> tuple[date, date]:
    today = datetime.now(UTC).date()
    if report_type == "daily":
        d = date.fromisoformat(p["date"]) if p.get("date") else today
        return d, d
    if report_type == "weekly":
        end = date.fromisoformat(p["date_to"]) if p.get("date_to") else today
        return end - timedelta(days=6), end
    if report_type == "monthly":
        ref = date.fromisoformat(p["month"] + "-01") if p.get("month") else date(today.year, today.month, 1)
        nxt = date(ref.year + (ref.month == 12), ref.month % 12 + 1, 1)
        return ref, nxt - timedelta(days=1)
    start = date.fromisoformat(p["date_from"]) if p.get("date_from") else today - timedelta(days=30)
    end = date.fromisoformat(p["date_to"]) if p.get("date_to") else today
    return start, end


def _accounts(db: Session, tenant_id: uuid.UUID, p: dict[str, Any]) -> list[BankAccount]:
    q = select(BankAccount).where(BankAccount.tenant_id == tenant_id, BankAccount.deleted_at.is_(None))
    if p.get("account_id"):
        q = q.where(BankAccount.id == uuid.UUID(str(p["account_id"])))
    if p.get("institution_id"):
        q = q.where(BankAccount.institution_id == uuid.UUID(str(p["institution_id"])))
    return list(db.scalars(q.order_by(BankAccount.nickname)))


def _f(v: Decimal | None) -> float | None:
    return float(v) if v is not None else None


def _account_activity(db: Session, accounts: list[BankAccount], start: date, end: date) -> Section:
    ids = [a.id for a in accounts]
    agg = {r.account_id: r for r in db.execute(
        select(Transaction.account_id, func.count(Transaction.id).label("n"),
               func.coalesce(func.sum(Transaction.credit), 0).label("cr"),
               func.coalesce(func.sum(Transaction.debit), 0).label("dr"),
               func.sum(case((Transaction.reconciliation_status.in_(("matched", "manual")), 1), else_=0)).label("rec"))
        .where(Transaction.account_id.in_(ids), Transaction.booking_date.between(start, end))
        .group_by(Transaction.account_id))} if ids else {}
    rows = []
    for a in accounts:
        r = agg.get(a.id)
        rows.append([a.nickname, a.institution.name, a.account_number_masked, a.currency, r.n if r else 0,
                     _f(r.cr) if r else 0.0, _f(r.dr) if r else 0.0, _f(r.cr - r.dr) if r else 0.0,
                     r.rec if r else 0, "DEMO" if a.is_demo else "Live"])
    return Section("Account activity", ["Account", "Bank", "Number", "Currency", "Transactions", "Credits",
                                        "Debits", "Net", "Reconciled", "Data"], rows)


def _balances_section(db: Session, tenant_id: uuid.UUID, accounts: list[BankAccount], reporting: str) -> Section:
    bal = latest_balances(db, tenant_id, [a.id for a in accounts]) if accounts else []
    by_acc: dict[uuid.UUID, dict[str, Any]] = {}
    for b in bal:
        by_acc.setdefault(b.account_id, {})[b.balance_type] = b
    rows = []
    for a in accounts:
        d = by_acc.get(a.id, {})
        primary = d.get("available") or d.get("current") or d.get("ledger") or d.get("statement_closing")
        conv, rate = (convert(db, tenant_id, primary.amount, primary.currency, reporting, primary.as_of.date())
                      if primary else (None, None))
        rows.append([a.nickname, a.currency,
                     _f(d["available"].amount) if "available" in d else None,
                     _f(d["ledger"].amount) if "ledger" in d else None,
                     _f(d["current"].amount) if "current" in d else None,
                     _f(d["statement_closing"].amount) if "statement_closing" in d else None,
                     primary.as_of.strftime("%Y-%m-%d %H:%M UTC") if primary else None,
                     primary.source if primary else None, _f(conv),
                     f"{rate['rate']} ({rate['rate_date']})" if rate else ("—" if a.currency == reporting else
                                                                          "no rate"),
                     "DEMO" if a.is_demo else "Live"])
    return Section("Balances", ["Account", "Currency", "Available", "Ledger", "Current", "Statement closing",
                                "As of", "Source", f"In {reporting}", "FX rate", "Data"], rows)


def _transactions_section(db: Session, accounts: list[BankAccount], start: date, end: date,
                          limit: int = 20000) -> Section:
    ids = [a.id for a in accounts]
    names = {a.id: a.nickname for a in accounts}
    rows = []
    if ids:
        q = (select(Transaction, TransactionCategory.name).outerjoin(TransactionCategory,
                                                                     TransactionCategory.id == Transaction.category_id)
             .where(Transaction.account_id.in_(ids), Transaction.booking_date.between(start, end))
             .order_by(Transaction.booking_date, Transaction.line_number).limit(limit))
        for t, cat in db.execute(q):
            rows.append([t.booking_date.isoformat(), t.value_date.isoformat() if t.value_date else None,
                         names.get(t.account_id), t.description, t.reference, _f(t.debit) or None,
                         _f(t.credit) or None, t.currency, _f(t.running_balance), cat, t.reconciliation_status,
                         t.source])
    return Section("Transactions", ["Date", "Value date", "Account", "Description", "Reference", "Debit", "Credit",
                                    "Currency", "Balance", "Category", "Reconciliation", "Source"], rows)


def build(db: Session, tenant_id: uuid.UUID, report_type: str, p: dict[str, Any]) -> ReportData:
    if report_type not in REPORT_TYPES:
        raise ValueError(f"Unknown report type {report_type}")
    org = db.get(Organization, tenant_id)
    reporting = (p.get("reporting_currency") or (org.reporting_currency if org else "GBP")).upper()
    start, end = _period(report_type, p)
    accounts = _accounts(db, tenant_id, p)
    title = REPORT_TYPES[report_type]
    sub = f"{org.name if org else ''} · {start:%d %b %Y}" + (f" – {end:%d %b %Y}" if end != start else "") + \
        f" · generated {datetime.now(UTC):%d %b %Y %H:%M} UTC"
    data = ReportData(title, sub, contains_demo=any(a.is_demo for a in accounts))

    if report_type in ("daily", "weekly", "monthly", "account"):
        data.sections += [_account_activity(db, accounts, start, end), _balances_section(db, tenant_id, accounts,
                                                                                         reporting)]
        if report_type != "account":
            data.sections.append(_transactions_section(db, accounts, start, end))
    elif report_type == "bank":
        grouped: dict[str, list[BankAccount]] = {}
        for a in accounts:
            grouped.setdefault(a.institution.name, []).append(a)
        rows = []
        for bank, accs in sorted(grouped.items()):
            sec = _account_activity(db, accs, start, end)
            rows.append([bank, len(accs), ", ".join(sorted({a.currency for a in accs})),
                         sum(r[4] for r in sec.rows), sum(r[5] or 0 for r in sec.rows), sum(r[6] or 0 for r in sec.rows)])
        data.sections.append(Section("Banks", ["Bank", "Accounts", "Currencies", "Transactions", "Credits", "Debits"],
                                     rows))
        data.sections.append(_balances_section(db, tenant_id, accounts, reporting))
    elif report_type == "transactions":
        data.sections.append(_transactions_section(db, accounts, start, end))
    elif report_type == "balances":
        data.sections.append(_balances_section(db, tenant_id, accounts, reporting))
    elif report_type == "credit_debit":
        ids = [a.id for a in accounts]
        rows = [[r.d.isoformat(), r.n, _f(r.cr), _f(r.dr), _f(r.cr - r.dr)] for r in db.execute(
            select(Transaction.booking_date.label("d"), func.count().label("n"),
                   func.coalesce(func.sum(Transaction.credit), 0).label("cr"),
                   func.coalesce(func.sum(Transaction.debit), 0).label("dr"))
            .where(Transaction.account_id.in_(ids), Transaction.booking_date.between(start, end))
            .group_by(Transaction.booking_date).order_by(Transaction.booking_date))] if ids else []
        data.sections.append(Section("Daily credits and debits (original currencies)",
                                     ["Date", "Transactions", "Credits", "Debits", "Net"], rows))
        data.sections.append(_account_activity(db, accounts, start, end))
    elif report_type == "reconciliation":
        names = {a.id: a.nickname for a in accounts}
        sess = db.scalars(select(ReconciliationSession).where(
            ReconciliationSession.tenant_id == tenant_id, ReconciliationSession.account_id.in_(list(names)),
            ReconciliationSession.created_at >= datetime.combine(start, datetime.min.time(), UTC))
            .order_by(ReconciliationSession.created_at.desc())).all() if names else []
        data.sections.append(Section("Reconciliation sessions", [
            "Run at", "Account", "Period", "Status", "Bank txns", "Book entries", "Matched", "Suggested",
            "Exceptions", "Opening", "Closing", "Expected closing", "Difference"],
            [[s.created_at.strftime("%Y-%m-%d %H:%M"), names.get(s.account_id),
              f"{s.period_start} – {s.period_end}", s.status, s.bank_transaction_count, s.book_entry_count,
              s.matched_count, s.suggested_count, s.exception_count, _f(s.opening_balance), _f(s.closing_balance),
              _f(s.expected_closing_balance), _f(s.balance_difference)] for s in sess]))
        exc = db.scalars(select(ReconciliationException).where(
            ReconciliationException.session_id.in_([s.id for s in sess]),
            ReconciliationException.status == "open").limit(5000)).all() if sess else []
        data.sections.append(Section("Open exceptions", ["Type", "Severity", "Amount", "Message"],
                                     [[e.exception_type, e.severity, _f(e.amount), e.message] for e in exc]))
    elif report_type == "failed_automation":
        names = {a.id: a.nickname for a in _accounts(db, tenant_id, {})}
        jobs = db.scalars(select(AutomationJob).where(
            AutomationJob.tenant_id == tenant_id, AutomationJob.status.in_(("failed", "awaiting_user")),
            AutomationJob.created_at >= datetime.combine(start, datetime.min.time(), UTC))
            .order_by(AutomationJob.created_at.desc())).all()
        data.sections.append(Section("Failed and blocked jobs", ["Created", "Job", "Account", "Status", "Attempts",
                                                                 "Error code", "Error"],
                                     [[j.created_at.strftime("%Y-%m-%d %H:%M"), j.job_type,
                                       names.get(j.account_id) if j.account_id else None, j.status, j.attempts,
                                       j.error_code, j.error_message] for j in jobs]))
    elif report_type == "missing_statements":
        today = datetime.now(UTC).date()
        expected_end = date(today.year, today.month, 1) - timedelta(days=1)
        rows = []
        for a in accounts:
            last = a.last_statement_date
            if last is None or last < expected_end:
                rows.append([a.nickname, a.institution.name, a.statement_period,
                             last.isoformat() if last else "never", expected_end.isoformat(),
                             (expected_end - last).days if last else None])
        data.sections.append(Section("Accounts with missing statements",
                                     ["Account", "Bank", "Period", "Last statement", "Expected through",
                                      "Days behind"], rows))
        stmts = db.scalars(select(Statement).where(Statement.tenant_id == tenant_id,
                                                   Statement.processing_status.in_(("failed", "needs_review",
                                                                                    "needs_ocr")))).all()
        names = {a.id: a.nickname for a in accounts}
        data.sections.append(Section("Statements needing attention", ["Account", "Period", "Status", "Received"],
                                     [[names.get(s.account_id), f"{s.period_start} – {s.period_end}",
                                       s.processing_status, s.created_at.strftime("%Y-%m-%d")]
                                      for s in stmts if s.account_id in names]))
    elif report_type == "currency":
        bal = _balances_section(db, tenant_id, accounts, reporting)
        by_ccy: dict[str, list[Any]] = {}
        for row in bal.rows:
            primary = next((v for v in (row[2], row[4], row[3], row[5]) if v is not None), None)
            agg = by_ccy.setdefault(row[1], [row[1], 0, 0.0, 0.0, True])
            agg[1] += 1
            agg[2] += primary or 0
            if row[8] is None and row[1] != reporting:
                agg[4] = False
            else:
                agg[3] += row[8] or 0
        data.sections.append(Section(f"Balances by currency (reporting currency {reporting})",
                                     ["Currency", "Accounts", "Original amount", f"Converted to {reporting}",
                                      "All rates available"],
                                     [[c, n, round(o, 2), round(cv, 2), "yes" if ok else "NO — partial"]
                                      for c, n, o, cv, ok in by_ccy.values()]))
        data.sections.append(bal)
    return data
