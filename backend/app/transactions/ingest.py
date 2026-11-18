"""Idempotent transaction ingestion shared by statements and provider feeds."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.banking import BankAccount
from app.models.transactions import Transaction
from app.notifications.service import notify
from app.transactions.categorizer import Categorizer
from app.transactions.fingerprint import FingerprintBuilder


@dataclass
class IncomingTransaction:
    booking_date: date
    description: str
    debit: Decimal
    credit: Decimal
    currency: str
    value_date: date | None = None
    reference: str | None = None
    running_balance: Decimal | None = None
    provider_transaction_id: str | None = None
    line_number: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def amount(self) -> Decimal:
        return self.credit - self.debit


@dataclass
class IngestResult:
    inserted: int = 0
    duplicates: int = 0
    suspects: int = 0
    inserted_ids: list[uuid.UUID] = field(default_factory=list)


def ingest(db: Session, account: BankAccount, items: list[IncomingTransaction], *, source: str,
           statement_id: uuid.UUID | None = None) -> IngestResult:
    res = IngestResult()
    if not items:
        return res
    fp = FingerprintBuilder()
    cat = Categorizer(db, account.tenant_id)
    for it in items:
        fingerprint = fp.make(account.id, it.booking_date, it.amount, it.description, it.reference)
        values = dict(
            id=uuid.uuid4(), tenant_id=account.tenant_id, account_id=account.id, statement_id=statement_id,
            source=source, provider_transaction_id=it.provider_transaction_id, booking_date=it.booking_date,
            value_date=it.value_date, description=it.description, reference=it.reference, debit=it.debit,
            credit=it.credit, amount=it.amount, currency=it.currency, running_balance=it.running_balance,
            category_id=cat.categorise(it.description, it.amount), fingerprint=fingerprint,
            line_number=it.line_number, raw_data=it.raw, is_demo=account.is_demo,
            reconciliation_status="unreconciled",
        )
        stmt = insert(Transaction).values(**values).on_conflict_do_nothing(
            constraint="uq_transactions_account_fingerprint").returning(Transaction.id)
        new_id = db.execute(stmt).scalar()
        if new_id is None:
            res.duplicates += 1
            # A statement re-delivering a feed transaction: link it to the statement for traceability
            if statement_id is not None:
                db.execute(update(Transaction).where(Transaction.account_id == account.id,
                                                     Transaction.fingerprint == fingerprint,
                                                     Transaction.statement_id.is_(None))
                           .values(statement_id=statement_id))
            continue
        res.inserted += 1
        res.inserted_ids.append(new_id)
    db.flush()
    res.suspects = flag_duplicate_suspects(db, account, res.inserted_ids)
    return res


def flag_duplicate_suspects(db: Session, account: BankAccount, ids: list[uuid.UUID]) -> int:
    """Same account + date + amount + non-empty reference, but a different fingerprint."""
    if not ids:
        return 0
    t1, t2 = Transaction.__table__.alias("t1"), Transaction.__table__.alias("t2")
    q = (select(t1.c.id).select_from(t1.join(t2, and_(
        t1.c.account_id == t2.c.account_id, t1.c.booking_date == t2.c.booking_date, t1.c.amount == t2.c.amount,
        func.upper(t1.c.reference) == func.upper(t2.c.reference), t1.c.id != t2.c.id)))
         .where(t1.c.id.in_(ids), t1.c.reference.is_not(None), t1.c.reference != "").distinct())
    suspect_ids = list(db.scalars(q))
    if suspect_ids:
        db.execute(update(Transaction).where(Transaction.id.in_(suspect_ids)).values(duplicate_suspect=True))
        notify(db, tenant_id=account.tenant_id, type="duplicate_transaction", severity="warning",
               title=f"{len(suspect_ids)} possible duplicate transaction(s) on {account.nickname}",
               body="Transactions share date, amount and reference with an existing entry. Review before "
                    "reconciling.", link=f"/transactions?account_id={account.id}&duplicate_suspect=true",
               entity_type="bank_account", entity_id=account.id)
    return len(suspect_ids)
