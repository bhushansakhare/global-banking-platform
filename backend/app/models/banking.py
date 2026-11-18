from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text,
                        UniqueConstraint)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, SoftDelete, TenantScoped, Timestamps, UUIDPk


class Country(Base):
    __tablename__ = "countries"
    code: Mapped[str] = mapped_column(String(2), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    default_currency: Mapped[str] = mapped_column(String(3))
    region: Mapped[str] = mapped_column(String(40))
    open_banking_framework: Mapped[str | None] = mapped_column(String(120))


class BankProvider(UUIDPk, Timestamps, Base):
    """A data-access channel (aggregator, official API, manual upload, demo)."""

    __tablename__ = "bank_providers"
    code: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    provider_type: Mapped[str] = mapped_column(String(40))  # aggregator|bank_api|account_aggregator|browser|manual|demo
    countries: Mapped[list[str]] = mapped_column(ARRAY(String(2)), default=list)
    currencies: Mapped[list[str]] = mapped_column(ARRAY(String(3)), default=list)
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    auth_method: Mapped[str] = mapped_column(String(60))
    consent_required: Mapped[bool] = mapped_column(Boolean, default=False)
    refresh_frequency: Mapped[str] = mapped_column(String(60), default="daily")
    integration_status: Mapped[str] = mapped_column(String(40))  # production_verified|implemented_unverified|planned|demo
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    documentation_url: Mapped[str | None] = mapped_column(String(500))
    min_request_interval_seconds: Mapped[float] = mapped_column(Numeric(8, 2), default=0)
    notes: Mapped[str | None] = mapped_column(Text)


class Institution(UUIDPk, Timestamps, SoftDelete, Base):
    __tablename__ = "institutions"
    __table_args__ = (UniqueConstraint("tenant_id", "provider_id", "external_id"),)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True),
                                                        ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    provider_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bank_providers.id"), index=True)
    country_code: Mapped[str] = mapped_column(String(2), ForeignKey("countries.code"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    external_id: Mapped[str] = mapped_column(String(200))
    bic: Mapped[str | None] = mapped_column(String(11))
    logo_url: Mapped[str | None] = mapped_column(String(500))
    supported_currencies: Mapped[list[str]] = mapped_column(ARRAY(String(3)), default=list)
    supports_balances: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_transactions: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_statements: Mapped[bool] = mapped_column(Boolean, default=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    provider: Mapped[BankProvider] = relationship(lazy="joined")


class BankConnection(UUIDPk, TenantScoped, Timestamps, SoftDelete, Base):
    __tablename__ = "bank_connections"
    institution_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("institutions.id"), index=True)
    provider_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bank_providers.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    # pending|active|requires_auth|consent_expired|error|disconnected
    external_reference: Mapped[str | None] = mapped_column(String(255))
    encrypted_credentials: Mapped[str | None] = mapped_column(Text)
    credentials_authorised_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    # official_bank_api|authorised_data_provider|permitted_browser_automation|manual_statement_import|demo
    connection_method: Mapped[str | None] = mapped_column(String(40))
    environment: Mapped[str | None] = mapped_column(String(20))  # sandbox|production|demo|local
    institution: Mapped[Institution] = relationship(lazy="joined")
    provider: Mapped[BankProvider] = relationship(lazy="joined")


class BankAccount(UUIDPk, TenantScoped, Timestamps, SoftDelete, Base):
    __tablename__ = "bank_accounts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "account_number_hash", name="uq_bank_accounts_tenant_number"),
        Index("ix_bank_accounts_tenant_automation", "tenant_id", "automation_enabled"),
    )
    institution_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("institutions.id"), index=True)
    connection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("bank_connections.id"),
                                                            index=True)
    nickname: Mapped[str] = mapped_column(String(120))
    holder_name: Mapped[str | None] = mapped_column(String(200))
    account_type: Mapped[str] = mapped_column(String(40), default="current")
    currency: Mapped[str] = mapped_column(String(3))
    country_code: Mapped[str] = mapped_column(String(2), ForeignKey("countries.code"))
    account_number_encrypted: Mapped[str | None] = mapped_column(Text)
    account_number_hash: Mapped[str | None] = mapped_column(String(64))
    account_number_masked: Mapped[str] = mapped_column(String(20), default="••••")
    external_account_id: Mapped[str | None] = mapped_column(String(255))
    automation_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    low_balance_threshold: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    statement_period: Mapped[str] = mapped_column(String(20), default="monthly")
    timezone: Mapped[str | None] = mapped_column(String(64))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_statement_date: Mapped[date | None] = mapped_column(Date)
    # healthy|degraded|failing|unknown|not_applicable — "healthy" only after a verified sync
    connection_health: Mapped[str] = mapped_column(String(20), default="unknown")
    automation_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    sync_schedule: Mapped[str | None] = mapped_column(String(100))  # cron; NULL = organisation schedule
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempted_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_error: Mapped[str | None] = mapped_column(Text)
    last_sync_result: Mapped[dict | None] = mapped_column(JSONB)
    browser_profile_key: Mapped[str | None] = mapped_column(String(60))
    browser_identifier_encrypted: Mapped[str | None] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    institution: Mapped[Institution] = relationship(lazy="joined")
    connection: Mapped[BankConnection | None] = relationship(lazy="joined")


class AccountConsent(UUIDPk, TenantScoped, Timestamps, Base):
    __tablename__ = "account_consents"
    connection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),
                                                     ForeignKey("bank_connections.id", ondelete="CASCADE"), index=True)
    consent_reference: Mapped[str | None] = mapped_column(String(255))
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    status: Mapped[str] = mapped_column(String(30), default="pending")  # pending|granted|expired|revoked
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    authorisation_url: Mapped[str | None] = mapped_column(Text)


class AccountBalance(UUIDPk, TenantScoped, Base):
    """Immutable balance snapshot. Each type is stored separately; never relabelled."""

    __tablename__ = "account_balances"
    __table_args__ = (
        Index("ix_account_balances_account_type_asof", "account_id", "balance_type", "as_of"),
        UniqueConstraint("account_id", "balance_type", "as_of", "source", name="uq_account_balances_snapshot"),
        CheckConstraint("balance_type in ('available','ledger','current','statement_opening','statement_closing',"
                        "'reported')", name="balance_type"),
    )
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),
                                                  ForeignKey("bank_accounts.id", ondelete="CASCADE"), index=True)
    balance_type: Mapped[str] = mapped_column(String(30))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    currency: Mapped[str] = mapped_column(String(3))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(30))  # provider|statement|manual|demo
    source_ref: Mapped[str | None] = mapped_column(String(255))
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    # provider_reported|statement_derived|user_entered|demo|unverified
    verification_status: Mapped[str] = mapped_column(String(30), default="unverified")
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict | None] = mapped_column(JSONB)


class ExchangeRate(UUIDPk, TenantScoped, Timestamps, Base):
    __tablename__ = "exchange_rates"
    __table_args__ = (UniqueConstraint("tenant_id", "base_currency", "quote_currency", "rate_date", "source"),)
    base_currency: Mapped[str] = mapped_column(String(3))
    quote_currency: Mapped[str] = mapped_column(String(3))
    rate: Mapped[Decimal] = mapped_column(Numeric(20, 10))
    rate_date: Mapped[date] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String(60), default="manual")
