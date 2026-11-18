from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TenantScoped, Timestamps, UUIDPk

NOTIFICATION_TYPES = (
    "sync_success", "connection_failed", "auth_required", "statement_missing", "low_balance",
    "reconciliation_mismatch", "duplicate_transaction", "job_failed", "report_ready",
)


class Notification(UUIDPk, TenantScoped, Timestamps, Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_read", "user_id", "read_at"),)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10), default="info")  # info|success|warning|error
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str | None] = mapped_column(String(300))
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationPreference(UUIDPk, TenantScoped, Timestamps, Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (UniqueConstraint("user_id", "type"),)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(40))
    in_app: Mapped[bool] = mapped_column(Boolean, default=True)


class AuditLog(UUIDPk, Base):
    """Append-only (enforced by a database trigger in the migration)."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_tenant_ts", "tenant_id", "ts"),
                      Index("ix_audit_logs_entity", "entity_type", "entity_id"))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_type: Mapped[str] = mapped_column(String(20), default="user")  # user|system|automation
    actor_label: Mapped[str | None] = mapped_column(String(254))
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(20), default="success")
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))


class SystemSetting(UUIDPk, TenantScoped, Timestamps, Base):
    __tablename__ = "system_settings"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))


class Report(UUIDPk, TenantScoped, Timestamps, Base):
    __tablename__ = "reports"
    report_type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    file_format: Mapped[str] = mapped_column(String(10))
    parameters: Mapped[dict] = mapped_column(JSONB, default=dict)
    storage_path: Mapped[str | None] = mapped_column(String(500))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="ready")  # ready|failed
    error: Mapped[str | None] = mapped_column(Text)
    generated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    trigger: Mapped[str] = mapped_column(String(20), default="manual")
    contains_demo_data: Mapped[bool] = mapped_column(Boolean, default=False)
