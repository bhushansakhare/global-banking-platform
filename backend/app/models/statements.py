from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, \
    UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, SoftDelete, TenantScoped, Timestamps, UUIDPk


class StatementFile(UUIDPk, TenantScoped, Timestamps, Base):
    """The original, unmodified file. Content-addressed by SHA-256 per tenant."""

    __tablename__ = "statement_files"
    __table_args__ = (UniqueConstraint("tenant_id", "sha256"),)
    sha256: Mapped[str] = mapped_column(String(64))
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(500))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    detected_format: Mapped[str] = mapped_column(String(10))  # pdf|xlsx|xls|csv|unknown
    mime_type: Mapped[str | None] = mapped_column(String(100))
    is_scanned_pdf: Mapped[bool | None] = mapped_column(Boolean)
    page_count: Mapped[int | None] = mapped_column(Integer)


class Statement(UUIDPk, TenantScoped, Timestamps, SoftDelete, Base):
    __tablename__ = "statements"
    __table_args__ = (Index("ix_statements_account_period", "account_id", "period_start", "period_end"),)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bank_accounts.id"), index=True)
    file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("statement_files.id"))
    source: Mapped[str] = mapped_column(String(30))  # provider_download|upload|demo
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    opening_balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    closing_balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    total_credits: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    total_debits: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    transaction_count: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    download_status: Mapped[str] = mapped_column(String(20), default="received")  # pending|downloaded|received|failed
    processing_status: Mapped[str] = mapped_column(String(30), default="pending")
    # pending|processing|processed|processed_with_warnings|needs_review|needs_ocr|failed|duplicate
    arithmetic_valid: Mapped[bool | None] = mapped_column(Boolean)
    arithmetic_difference: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    parser_name: Mapped[str | None] = mapped_column(String(60))
    parse_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    warnings: Mapped[list] = mapped_column(JSONB, default=list)
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("statements.id"))
    external_statement_id: Mapped[str | None] = mapped_column(String(255))
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    file: Mapped[StatementFile | None] = relationship(lazy="joined")


class StatementParsingJob(UUIDPk, TenantScoped, Timestamps, Base):
    __tablename__ = "statement_parsing_jobs"
    statement_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),
                                                    ForeignKey("statements.id", ondelete="CASCADE"), index=True)
    automation_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    parser_name: Mapped[str | None] = mapped_column(String(60))
    rows_extracted: Mapped[int] = mapped_column(Integer, default=0)
    rows_inserted: Mapped[int] = mapped_column(Integer, default=0)
    rows_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    diagnostics: Mapped[dict] = mapped_column(JSONB, default=dict)
