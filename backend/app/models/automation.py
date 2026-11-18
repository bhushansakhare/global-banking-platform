from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TenantScoped, Timestamps, UUIDPk

JOB_STATES = ("queued", "running", "succeeded", "failed", "retry_scheduled", "cancelled", "awaiting_user")
TERMINAL_STATES = ("succeeded", "failed", "cancelled")


class AutomationSettings(UUIDPk, TenantScoped, Timestamps, Base):
    __tablename__ = "automation_settings"
    __table_args__ = (UniqueConstraint("tenant_id"),)
    master_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    schedule_type: Mapped[str] = mapped_column(String(20), default="daily")  # daily|custom
    daily_time: Mapped[str] = mapped_column(String(5), default="06:00")
    cron_expression: Mapped[str | None] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/London")
    lookback_days: Mapped[int] = mapped_column(Integer, default=7)
    auto_reconcile: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_generate_daily_report: Mapped[bool] = mapped_column(Boolean, default=True)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4)
    retry_base_seconds: Mapped[int] = mapped_column(Integer, default=60)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))


class AutomationJob(UUIDPk, TenantScoped, Timestamps, Base):
    """Durable PostgreSQL-backed job. Claimed with FOR UPDATE SKIP LOCKED."""

    __tablename__ = "automation_jobs"
    __table_args__ = (
        Index("ix_automation_jobs_claim", "status", "run_after", postgresql_where=text("status in ('queued','retry_scheduled')")),
        Index("ix_automation_jobs_tenant_created", "tenant_id", "created_at"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_automation_jobs_idempotency"),
        Index("uq_automation_jobs_active_collect", "account_id", unique=True,
              postgresql_where=text("job_type = 'collect_account' AND status IN "
                                    "('queued','running','retry_scheduled','awaiting_user')")),
    )
    job_type: Mapped[str] = mapped_column(String(40))  # collect_account|parse_statement|reconcile_account|daily_report
    status: Mapped[str] = mapped_column(String(20), default="queued")
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # schedule|manual|retry|pipeline
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("bank_accounts.id"),
                                                         index=True)
    parent_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("automation_jobs.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(100))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    progress_message: Mapped[str | None] = mapped_column(String(255))
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)
    requires_user_action: Mapped[str | None] = mapped_column(String(60))  # otp|captcha|reauth|consent
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))


class AutomationJobLog(UUIDPk, TenantScoped, Base):
    __tablename__ = "automation_job_logs"
    __table_args__ = (Index("ix_automation_job_logs_job_ts", "job_id", "ts"),)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),
                                              ForeignKey("automation_jobs.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    level: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(Text)
    context: Mapped[dict] = mapped_column(JSONB, default=dict)
