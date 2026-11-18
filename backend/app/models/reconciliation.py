from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TenantScoped, Timestamps, UUIDPk


class ReconciliationSession(UUIDPk, TenantScoped, Timestamps, Base):
    """One deterministic run. Results are immutable; manual decisions are recorded separately on matches."""

    __tablename__ = "reconciliation_sessions"
    __table_args__ = (Index("ix_recon_sessions_account_created", "account_id", "created_at"),)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bank_accounts.id"), index=True)
    statement_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("statements.id"))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(30), default="running")
    # running|balanced|completed_with_exceptions|failed
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # manual|automation
    rules: Mapped[dict] = mapped_column(JSONB, default=dict)
    engine_version: Mapped[str] = mapped_column(String(20))
    bank_transaction_count: Mapped[int] = mapped_column(Integer, default=0)
    book_entry_count: Mapped[int] = mapped_column(Integer, default=0)
    matched_count: Mapped[int] = mapped_column(Integer, default=0)
    suggested_count: Mapped[int] = mapped_column(Integer, default=0)
    unmatched_bank_count: Mapped[int] = mapped_column(Integer, default=0)
    unmatched_book_count: Mapped[int] = mapped_column(Integer, default=0)
    exception_count: Mapped[int] = mapped_column(Integer, default=0)
    opening_balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    closing_balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    expected_closing_balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    balance_difference: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    match_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    started_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)


class ReconciliationMatch(UUIDPk, TenantScoped, Timestamps, Base):
    __tablename__ = "reconciliation_matches"
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),
                                                  ForeignKey("reconciliation_sessions.id", ondelete="CASCADE"),
                                                  index=True)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("transactions.id"),
                                                             index=True)
    book_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("book_entries.id"),
                                                            index=True)
    match_type: Mapped[str] = mapped_column(String(30))  # reference_amount|amount_date|amount_tolerance|manual
    status: Mapped[str] = mapped_column(String(20))  # confirmed|suggested|rejected|manual
    score: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    amount_difference: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal(0))
    date_difference_days: Mapped[int] = mapped_column(Integer, default=0)
    reasons: Mapped[list] = mapped_column(JSONB, default=list)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    original_status: Mapped[str] = mapped_column(String(20))


class ReconciliationException(UUIDPk, TenantScoped, Timestamps, Base):
    __tablename__ = "reconciliation_exceptions"
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),
                                                  ForeignKey("reconciliation_sessions.id", ondelete="CASCADE"),
                                                  index=True)
    exception_type: Mapped[str] = mapped_column(String(40))
    # balance_mismatch|running_balance_break|duplicate_reference|duplicate_fingerprint|unmatched_bank|
    # unmatched_book|amount_mismatch|date_discrepancy|count_mismatch
    severity: Mapped[str] = mapped_column(String(10))  # info|warning|critical
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("transactions.id"))
    book_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("book_entries.id"))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|resolved|ignored
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)
