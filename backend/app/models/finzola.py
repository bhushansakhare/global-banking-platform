"""Finzola staging and delivery records, separate from immutable bank evidence."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, SoftDelete, TenantScoped, Timestamps, UUIDPk


class FinzolaRecord(UUIDPk, TenantScoped, Timestamps, Base):
    __tablename__ = "finzola_records"
    __table_args__ = (UniqueConstraint("tenant_id", "domain", "external_id"),)
    domain: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(200))
    generation: Mapped[str] = mapped_column(String(36))
    data: Mapped[dict] = mapped_column(JSONB)


class FinzolaMapping(UUIDPk, TenantScoped, Timestamps, Base):
    __tablename__ = "finzola_mappings"
    __table_args__ = (UniqueConstraint("tenant_id", "external_org_id", "routing_field", "routing_value"),)
    external_org_id: Mapped[str] = mapped_column(String(120))
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bank_accounts.id"))
    routing_field: Mapped[str] = mapped_column(String(30), default="default")
    routing_value: Mapped[str] = mapped_column(String(120), default="")
    options: Mapped[dict] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    authorized_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))


class FinzolaItem(UUIDPk, TenantScoped, Timestamps, Base):
    __tablename__ = "finzola_items"
    __table_args__ = (UniqueConstraint("tenant_id", "external_org_id", "external_id"),)
    external_org_id: Mapped[str] = mapped_column(String(120))
    external_id: Mapped[str] = mapped_column(String(200))
    source_hash: Mapped[str] = mapped_column(String(64))
    expected: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(40), default="requires_attention")
    reason: Mapped[str] = mapped_column(Text, default="")
    decision: Mapped[str | None] = mapped_column(String(30))
    mapping_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("finzola_mappings.id"))
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("bank_accounts.id"))
    book_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("book_entries.id"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("reconciliation_sessions.id"))
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("transactions.id"))


class FinzolaDestination(UUIDPk, TenantScoped, Timestamps, SoftDelete, Base):
    __tablename__ = "finzola_destinations"
    __table_args__ = (UniqueConstraint("tenant_id", "external_org_id"), UniqueConstraint("key_hash"))
    external_org_id: Mapped[str] = mapped_column(String(120))
    encrypted_key: Mapped[str] = mapped_column(Text)
    key_hash: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    verified_revision: Mapped[int | None] = mapped_column(Integer)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_delivery: Mapped[bool] = mapped_column(Boolean, default=False)
    authorized_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    last_error: Mapped[str | None] = mapped_column(Text)


class FinzolaDelivery(UUIDPk, TenantScoped, Timestamps, Base):
    __tablename__ = "finzola_deliveries"
    __table_args__ = (UniqueConstraint("tenant_id", "destination_id", "request_id", "payload_hash", "revision"),)
    destination_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("finzola_destinations.id"))
    source_kind: Mapped[str] = mapped_column(String(20))
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    request_id: Mapped[str] = mapped_column(String(200))
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(Integer)
    authorized_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    acknowledgement: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
