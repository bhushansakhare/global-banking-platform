"""Balance snapshot storage and queries. Each balance type is stored and reported separately."""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.banking import AccountBalance, BankAccount, ExchangeRate
from app.notifications.service import notify

BALANCE_TYPES = ("available", "ledger", "current", "statement_opening", "statement_closing", "reported")
VERIFICATION_BY_SOURCE = {"provider": "provider_reported", "statement": "statement_derived", "demo": "demo",
                          "manual": "user_entered"}


def record_snapshot(db: Session, *, account: BankAccount, balance_type: str, amount: Decimal, currency: str,
                    as_of: datetime, source: str, source_ref: str | None = None, job_id: uuid.UUID | None = None,
                    raw: dict | None = None, verification_status: str | None = None) -> bool:
    if balance_type not in BALANCE_TYPES:
        raise ValueError(f"Unknown balance type {balance_type}")
    if currency != account.currency:
        # balances are never stored against an account in a different currency (no silent mixing)
        return False
    verification = verification_status or VERIFICATION_BY_SOURCE.get(source, "unverified")
    stmt = insert(AccountBalance).values(
        id=uuid.uuid4(), tenant_id=account.tenant_id, account_id=account.id, balance_type=balance_type,
        amount=amount, currency=currency, as_of=as_of, source=source, source_ref=source_ref, job_id=job_id,
        is_demo=account.is_demo, captured_at=datetime.now(UTC), raw=raw or {}, verification_status=verification,
    ).on_conflict_do_nothing(constraint="uq_account_balances_snapshot")
    res = db.execute(stmt)
    return bool(res.rowcount)


def latest_balances(db: Session, tenant_id: uuid.UUID, account_ids: list[uuid.UUID] | None = None
                    ) -> list[AccountBalance]:
    sub = (select(AccountBalance.account_id, AccountBalance.balance_type,
                  func.max(AccountBalance.as_of).label("as_of"))
           .where(AccountBalance.tenant_id == tenant_id)
           .group_by(AccountBalance.account_id, AccountBalance.balance_type))
    if account_ids:
        sub = sub.where(AccountBalance.account_id.in_(account_ids))
    sub = sub.subquery()
    q = (select(AccountBalance).join(sub, and_(AccountBalance.account_id == sub.c.account_id,
                                               AccountBalance.balance_type == sub.c.balance_type,
                                               AccountBalance.as_of == sub.c.as_of))
         .where(AccountBalance.tenant_id == tenant_id))
    rows = db.scalars(q).all()
    # one per (account,type): prefer provider over statement over others when timestamps tie
    rank = {"provider": 0, "statement": 1, "manual": 2, "demo": 3}
    best: dict[tuple, AccountBalance] = {}
    for r in rows:
        k = (r.account_id, r.balance_type)
        if k not in best or rank.get(r.source, 9) < rank.get(best[k].source, 9):
            best[k] = r
    return list(best.values())


def find_rate(db: Session, tenant_id: uuid.UUID, base: str, quote: str, on: date) -> ExchangeRate | None:
    if base == quote:
        return None
    return db.scalar(select(ExchangeRate).where(ExchangeRate.tenant_id == tenant_id,
                                                ExchangeRate.base_currency == base,
                                                ExchangeRate.quote_currency == quote,
                                                ExchangeRate.rate_date <= on)
                     .order_by(ExchangeRate.rate_date.desc()).limit(1))


def convert(db: Session, tenant_id: uuid.UUID, amount: Decimal, base: str, quote: str, on: date
            ) -> tuple[Decimal | None, dict | None]:
    """Explicit conversion: returns (converted, rate-metadata) or (None, None) when no rate exists.
    Never converts silently — callers must surface the rate metadata."""
    if base == quote:
        return amount, None
    r = find_rate(db, tenant_id, base, quote, on)
    if r is not None:
        return (amount * r.rate).quantize(Decimal("0.01")), {"base": base, "quote": quote, "rate": str(r.rate),
                                                            "rate_date": r.rate_date.isoformat(),
                                                            "source": r.source}
    inv = find_rate(db, tenant_id, quote, base, on)
    if inv is not None and inv.rate:
        return (amount / inv.rate).quantize(Decimal("0.01")), {"base": base, "quote": quote,
                                                              "rate": str((1 / inv.rate).quantize(Decimal("0.0000000001"))),
                                                              "rate_date": inv.rate_date.isoformat(),
                                                              "source": f"inverse:{inv.source}"}
    return None, None


def check_low_balance(db: Session, account: BankAccount) -> None:
    if account.low_balance_threshold is None:
        return
    latest = [b for b in latest_balances(db, account.tenant_id, [account.id])
              if b.balance_type in ("available", "ledger", "current")]
    if not latest:
        return
    b = min(latest, key=lambda x: ("available", "current", "ledger").index(x.balance_type))
    if b.amount < account.low_balance_threshold:
        notify(db, tenant_id=account.tenant_id, type="low_balance", severity="warning",
               title=f"Low balance on {account.nickname}",
               body=f"{b.balance_type.title()} balance {b.amount:,.2f} {b.currency} is below the threshold of "
                    f"{account.low_balance_threshold:,.2f}.",
               link=f"/accounts/{account.id}", entity_type="bank_account", entity_id=account.id,
               dedupe_key=f"low_balance:{account.id}:{b.as_of.date()}")
