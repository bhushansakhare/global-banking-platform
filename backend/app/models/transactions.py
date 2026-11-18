from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, SoftDelete, TenantScoped, Timestamps, UUIDPk


class TransactionCategory(UUIDPk, TenantScoped, Timestamps, Base):
    __tablename__ = "transaction_categories"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)
    name: Mapped[str] = mapped_column(String(80))
    colour: Mapped[str] = mapped_column(String(9), default="#64748B")
    keywords: Mapped[list[str]] = mapped_column(ARRAY(String(80)), default=list)
    direction: Mapped[str] = mapped_column(String(10), default="any")  # credit|debit|any
    priority: Mapped[int] = mapped_column(Integer, default=100)


class Transaction(UUIDPk, TenantScoped, Timestamps, Base):
    """A bank-side transaction exactly as reported by a statement or provider.

    Original values are never modified after insert; corrections live elsewhere.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("account_id", "fingerprint", name="uq_transactions_account_fingerprint"),
        Index("ix_transactions_account_date", "account_id", "booking_date"),
        Index("ix_transactions_tenant_date", "tenant_id", "booking_date"),
        Index("ix_transactions_recon_status", "tenant_id", "reconciliation_status"),
    )
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bank_accounts.id"), index=True)
    statement_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("statements.id"),
                                                           index=True)
    source: Mapped[str] = mapped_column(String(20))  # statement|provider|demo
    provider_transaction_id: Mapped[str | None] = mapped_column(String(255))
    booking_date: Mapped[date] = mapped_column(Date)
    value_date: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str] = mapped_column(Text)
    reference: Mapped[str | None] = mapped_column(String(255), index=True)
    debit: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal(0))
    credit: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal(0))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))  # signed: credit positive, debit negative
    currency: Mapped[str] = mapped_column(String(3))
    running_balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("transaction_categories.id"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    line_number: Mapped[int | None] = mapped_column(Integer)
    duplicate_suspect: Mapped[bool] = mapped_column(Boolean, default=False)
    reconciliation_status: Mapped[str] = mapped_column(String(20), default="unreconciled")
    # unreconciled|matched|suggested|exception|manual
    raw_data: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    category: Mapped[TransactionCategory | None] = relationship(lazy="joined")


class BookEntry(UUIDPk, TenantScoped, Timestamps, SoftDelete, Base):
    """Internal ledger / cash-book record that bank transactions are reconciled against."""

    __tablename__ = "book_entries"
    __table_args__ = (
        Index("ix_book_entries_account_date", "account_id", "entry_date"),
        UniqueConstraint("account_id", "external_ref", name="uq_book_entries_account_external_ref"),
    )
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bank_accounts.id"), index=True)
    entry_date: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(Text)
    reference: Mapped[str | None] = mapped_column(String(255))
    external_ref: Mapped[str | None] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))  # signed, same convention as Transaction
    currency: Mapped[str] = mapped_column(String(3))
    source: Mapped[str] = mapped_column(String(20), default="import")  # import|manual|erp|demo
    reconciliation_status: Mapped[str] = mapped_column(String(20), default="unreconciled")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    raw_data: Mapped[dict] = mapped_column(JSONB, default=dict)
