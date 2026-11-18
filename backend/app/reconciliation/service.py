"""Persistence and workflow around the reconciliation engine."""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit.service import record as audit
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.models.banking import AccountBalance, BankAccount
from app.models.reconciliation import ReconciliationException, ReconciliationMatch, ReconciliationSession
from app.models.statements import Statement
from app.models.system import SystemSetting
from app.models.transactions import BookEntry, Transaction
from app.notifications.service import notify
from app.reconciliation.engine import ENGINE_VERSION, BankItem, BookItem, Rules, reconcile


def tenant_rules(db: Session, tenant_id: uuid.UUID) -> Rules:
    s = db.scalar(select(SystemSetting).where(SystemSetting.tenant_id == tenant_id,
                                              SystemSetting.key == "reconciliation_rules"))
    return Rules.from_dict(s.value if s else None)


def _pick_statement(db: Session, account_id: uuid.UUID, start: date, end: date) -> Statement | None:
    return db.scalar(select(Statement).where(
        Statement.account_id == account_id, Statement.deleted_at.is_(None), Statement.duplicate_of_id.is_(None),
        Statement.period_start == start, Statement.period_end == end,
        Statement.processing_status.in_(("processed", "processed_with_warnings", "needs_review")))
        .order_by(Statement.created_at.desc()).limit(1))


def run_reconciliation(db: Session, *, tenant_id: uuid.UUID, account_id: uuid.UUID, period_start: date | None = None,
                       period_end: date | None = None, statement_id: uuid.UUID | None = None,
                       trigger: str = "manual", user_id: uuid.UUID | None = None, job_id: uuid.UUID | None = None,
                       rules_override: dict | None = None) -> ReconciliationSession:
    account = db.scalar(select(BankAccount).where(BankAccount.id == account_id, BankAccount.tenant_id == tenant_id,
                                                  BankAccount.deleted_at.is_(None)))
    if account is None:
        raise NotFound("Account not found")
    statement: Statement | None = None
    if statement_id:
        statement = db.scalar(select(Statement).where(Statement.id == statement_id, Statement.tenant_id == tenant_id))
        if statement is None or statement.period_start is None or statement.period_end is None:
            raise ValidationFailed("Statement not found or has no processed period")
        period_start, period_end = statement.period_start, statement.period_end
    if period_start is None or period_end is None:
        raise ValidationFailed("Provide a statement or a period")
    if period_end < period_start:
        raise ValidationFailed("Period end must not be before period start")
    statement = statement or _pick_statement(db, account_id, period_start, period_end)

    rules = Rules.from_dict({**tenant_rules(db, tenant_id).to_dict(), **(rules_override or {})})
    session = ReconciliationSession(tenant_id=tenant_id, account_id=account_id,
                                    statement_id=statement.id if statement else None, period_start=period_start,
                                    period_end=period_end, status="running", trigger=trigger, rules=rules.to_dict(),
                                    engine_version=ENGINE_VERSION, started_by=user_id, job_id=job_id)
    db.add(session)
    db.flush()

    txns = db.scalars(select(Transaction).where(Transaction.account_id == account_id,
                                                Transaction.booking_date.between(period_start, period_end))).all()
    pad = timedelta(days=rules.date_tolerance_days)
    books = db.scalars(select(BookEntry).where(BookEntry.account_id == account_id, BookEntry.deleted_at.is_(None),
                                               BookEntry.entry_date.between(period_start - pad, period_end + pad))
                       ).all()
    # Book entries already matched to transactions outside this period are not available
    txn_ids = {t.id for t in txns}

    # carry forward previous manual decisions
    pre = db.execute(select(ReconciliationMatch.transaction_id, ReconciliationMatch.book_entry_id)
                     .where(ReconciliationMatch.tenant_id == tenant_id, ReconciliationMatch.status == "manual",
                            ReconciliationMatch.transaction_id.in_(txn_ids) if txn_ids else False)).all()

    external = None
    if statement and statement.closing_balance is not None:
        ledger = db.scalar(select(AccountBalance).where(
            AccountBalance.account_id == account_id, AccountBalance.balance_type.in_(("ledger", "current")),
            AccountBalance.source == "provider",
            AccountBalance.as_of.between(datetime.combine(period_end, datetime.min.time(), UTC),
                                         datetime.combine(period_end + timedelta(days=1), datetime.min.time(), UTC)))
            .order_by(AccountBalance.as_of.desc()).limit(1))
        if ledger:
            external = (ledger.amount, f"provider {ledger.balance_type} balance at {ledger.as_of:%Y-%m-%d %H:%M}")

    result = reconcile(
        [BankItem(t.id, t.booking_date, t.amount, t.description, t.reference, t.running_balance, t.line_number)
         for t in txns],
        [BookItem(b.id, b.entry_date, b.amount, b.description, b.reference) for b in books],
        rules,
        opening=statement.opening_balance if statement else None,
        closing=statement.closing_balance if statement else None,
        expected_count=statement.transaction_count if statement else None,
        pre_matched=[(a, b) for a, b in pre if a and b],
        external_closing=external,
        book_period=(period_start, period_end),
    )

    for m in result.matches:
        db.add(ReconciliationMatch(tenant_id=tenant_id, session_id=session.id, transaction_id=m.bank_id,
                                   book_entry_id=m.book_id, match_type=m.match_type, status=m.status, score=m.score,
                                   amount_difference=m.amount_difference, date_difference_days=m.date_difference_days,
                                   reasons=m.reasons, original_status=m.status))
    for e in result.exceptions:
        db.add(ReconciliationException(tenant_id=tenant_id, session_id=session.id, exception_type=e.exception_type,
                                       severity=e.severity, transaction_id=e.bank_id, book_entry_id=e.book_id,
                                       amount=e.amount, message=e.message, details=e.details))

    # workflow status columns (original values untouched)
    status_by_txn = {m.bank_id: ("matched" if m.status in ("confirmed", "manual") else "suggested")
                     for m in result.matches}
    status_by_book = {m.book_id: ("matched" if m.status in ("confirmed", "manual") else "suggested")
                      for m in result.matches}
    for t in txns:
        new = status_by_txn.get(t.id, "exception" if t.id in result.unmatched_bank else "unreconciled")
        if t.reconciliation_status != new:
            db.execute(update(Transaction).where(Transaction.id == t.id).values(reconciliation_status=new))
    for b in books:
        if b.id in status_by_book or b.id in result.unmatched_book:
            b.reconciliation_status = status_by_book.get(b.id, "exception")
        # padding entries from neighbouring periods keep their own status

    session.bank_transaction_count, session.book_entry_count = len(txns), len(books)
    session.matched_count, session.suggested_count = result.confirmed, result.suggested
    session.unmatched_bank_count, session.unmatched_book_count = len(result.unmatched_bank), len(result.unmatched_book)
    session.exception_count = len(result.exceptions)
    session.opening_balance = statement.opening_balance if statement else None
    session.closing_balance = statement.closing_balance if statement else None
    session.expected_closing_balance, session.balance_difference = result.expected_closing, result.balance_difference
    denom = max(len(txns), 1) if books else 0
    session.match_rate = (Decimal(result.confirmed) / Decimal(denom)).quantize(Decimal("0.0001")) if denom else None
    critical = result.has_critical
    open_items = result.suggested + len(result.unmatched_bank) + len(result.unmatched_book)
    session.status = "balanced" if not critical and open_items == 0 and not any(
        e.severity == "warning" for e in result.exceptions) else "completed_with_exceptions"
    session.completed_at = datetime.now(UTC)
    session.summary = {"has_book_side": bool(books), "has_statement": statement is not None,
                       "critical_exceptions": sum(1 for e in result.exceptions if e.severity == "critical"),
                       "note": None if books else "No book entries in period — balance and integrity checks only."}
    db.flush()

    if session.status != "balanced":
        notify(db, tenant_id=tenant_id, type="reconciliation_mismatch",
               severity="error" if critical else "warning",
               title=f"Reconciliation exceptions on {account.nickname}",
               body=f"{session.exception_count} exception(s), {session.suggested_count} suggested match(es) for "
                    f"{period_start:%d %b %Y} – {period_end:%d %b %Y}.",
               link=f"/reconciliation/{session.id}", entity_type="reconciliation_session", entity_id=session.id,
               dedupe_key=f"recon:{account_id}:{period_start}:{period_end}:{session.exception_count}")
    audit(db, action="reconciliation.run", tenant_id=tenant_id, actor_user_id=user_id,
          actor_type="user" if user_id else "automation", entity_type="reconciliation_session", entity_id=session.id,
          after={"status": session.status, "matched": session.matched_count, "exceptions": session.exception_count,
                 "period": f"{period_start}..{period_end}"})
    return session


# ---- manual decisions (auditable, never destructive) -----------------------------
def decide_match(db: Session, *, tenant_id: uuid.UUID, match_id: uuid.UUID, decision: str, user_id: uuid.UUID,
                 note: str | None) -> ReconciliationMatch:
    m = db.scalar(select(ReconciliationMatch).where(ReconciliationMatch.id == match_id,
                                                    ReconciliationMatch.tenant_id == tenant_id).with_for_update())
    if m is None:
        raise NotFound("Match not found")
    if decision not in ("confirm", "reject"):
        raise ValidationFailed("Decision must be 'confirm' or 'reject'")
    if m.status in ("rejected",) or (m.decided_at and m.status == "manual"):
        raise Conflict("This match has already been decided")
    before = {"status": m.status}
    m.status = "manual" if decision == "confirm" else "rejected"
    m.decided_by, m.decided_at, m.decision_note = user_id, datetime.now(UTC), note
    if m.transaction_id:
        db.execute(update(Transaction).where(Transaction.id == m.transaction_id).values(
            reconciliation_status="manual" if decision == "confirm" else "exception"))
    if m.book_entry_id:
        be = db.get(BookEntry, m.book_entry_id)
        if be:
            be.reconciliation_status = "manual" if decision == "confirm" else "exception"
    audit(db, action=f"reconciliation.match.{decision}", tenant_id=tenant_id, actor_user_id=user_id,
          entity_type="reconciliation_match", entity_id=m.id, before=before,
          after={"status": m.status, "note": note})
    return m


def manual_match(db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID, transaction_id: uuid.UUID,
                 book_entry_id: uuid.UUID, user_id: uuid.UUID, note: str | None) -> ReconciliationMatch:
    s = db.scalar(select(ReconciliationSession).where(ReconciliationSession.id == session_id,
                                                      ReconciliationSession.tenant_id == tenant_id))
    t = db.scalar(select(Transaction).where(Transaction.id == transaction_id, Transaction.tenant_id == tenant_id))
    b = db.scalar(select(BookEntry).where(BookEntry.id == book_entry_id, BookEntry.tenant_id == tenant_id))
    if not (s and t and b):
        raise NotFound("Session, transaction or book entry not found")
    if t.account_id != s.account_id or b.account_id != s.account_id:
        raise ValidationFailed("Transaction and book entry must belong to the session's account")
    m = ReconciliationMatch(tenant_id=tenant_id, session_id=s.id, transaction_id=t.id, book_entry_id=b.id,
                            match_type="manual", status="manual", score=Decimal("1"),
                            amount_difference=t.amount - b.amount,
                            date_difference_days=abs((t.booking_date - b.entry_date).days),
                            reasons=["manual match"], decided_by=user_id, decided_at=datetime.now(UTC),
                            decision_note=note, original_status="manual")
    db.add(m)
    db.execute(update(Transaction).where(Transaction.id == t.id).values(reconciliation_status="manual"))
    b.reconciliation_status = "manual"
    for exc in db.scalars(select(ReconciliationException).where(
            ReconciliationException.session_id == s.id, ReconciliationException.status == "open",
            (ReconciliationException.transaction_id == t.id) | (ReconciliationException.book_entry_id == b.id))):
        exc.status, exc.resolved_by, exc.resolved_at = "resolved", user_id, datetime.now(UTC)
        exc.resolution_note = f"Resolved by manual match {m.id}"
    db.flush()
    audit(db, action="reconciliation.match.manual", tenant_id=tenant_id, actor_user_id=user_id,
          entity_type="reconciliation_match", entity_id=m.id,
          after={"transaction": str(t.id), "book_entry": str(b.id), "note": note})
    return m


def resolve_exception(db: Session, *, tenant_id: uuid.UUID, exception_id: uuid.UUID, status: str,
                      user_id: uuid.UUID, note: str) -> ReconciliationException:
    e = db.scalar(select(ReconciliationException).where(ReconciliationException.id == exception_id,
                                                        ReconciliationException.tenant_id == tenant_id))
    if e is None:
        raise NotFound("Exception not found")
    if status not in ("resolved", "ignored", "open"):
        raise ValidationFailed("Invalid status")
    if not note or len(note.strip()) < 3:
        raise ValidationFailed("A resolution note is required")
    before = {"status": e.status}
    e.status, e.resolution_note = status, note.strip()
    e.resolved_by, e.resolved_at = (user_id, datetime.now(UTC)) if status != "open" else (None, None)
    audit(db, action="reconciliation.exception.update", tenant_id=tenant_id, actor_user_id=user_id,
          entity_type="reconciliation_exception", entity_id=e.id, before=before, after={"status": status, "note": note})
    return e
