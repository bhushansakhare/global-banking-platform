"""Validated request bodies."""
from __future__ import annotations

import datetime as dt
import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Currency = Field(pattern=r"^[A-Z]{3}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SetupRequest(StrictModel):
    organisation: str = Field(min_length=2, max_length=200)
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    reporting_currency: str = Currency
    timezone: str = Field(default="Europe/London", max_length=64)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")


class LoginRequest(StrictModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(StrictModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class UserCreate(StrictModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    role: Literal["owner", "admin", "analyst", "viewer"]
    password: str = Field(min_length=12, max_length=128)


class UserUpdate(StrictModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    role: Literal["owner", "admin", "analyst", "viewer"] | None = None
    is_active: bool | None = None


class InstitutionCreate(StrictModel):
    name: str = Field(min_length=2, max_length=200)
    country_code: str = Field(pattern=r"^[A-Z]{2}$")
    bic: str | None = Field(default=None, pattern=r"^[A-Z0-9]{8}([A-Z0-9]{3})?$")
    supported_currencies: list[str] = Field(default_factory=list, max_length=20)


class AccountCreate(StrictModel):
    institution_id: uuid.UUID
    nickname: str = Field(min_length=1, max_length=120)
    currency: str = Currency
    account_type: Literal["current", "savings", "deposit", "credit_card", "loan", "escrow", "other"] = "current"
    account_number: str | None = Field(default=None, min_length=4, max_length=40, pattern=r"^[A-Za-z0-9 \-]+$")
    holder_name: str | None = Field(default=None, max_length=200)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    low_balance_threshold: Decimal | None = Field(default=None, ge=0)
    automation_enabled: bool = True
    statement_period: Literal["daily", "weekly", "monthly", "quarterly"] = "monthly"
    external_account_id: str | None = Field(default=None, max_length=255)
    demo_scenario: Literal["ok", "otp_required", "provider_down", "consent_expired", "auth_failed"] | None = None
    notes: str | None = Field(default=None, max_length=2000)
    connection_provider: str | None = Field(default=None, max_length=60, pattern=r"^[a-z0-9_]+$")
    sync_schedule: str | None = Field(default=None, max_length=100)
    timezone: str | None = Field(default=None, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        return _check_tz(v)


def _check_tz(v: str | None) -> str | None:
    if not v:
        return None
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        ZoneInfo(v)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Unknown time zone") from exc
    return v


class ConnectionMethodChange(StrictModel):
    provider_code: str = Field(max_length=60, pattern=r"^[a-z0-9_]+$")


class AuthorizationStart(StrictModel):
    # RBI Account Aggregator: the customer's mobile number (AA handle). Never a bank password.
    mobile: str | None = Field(default=None, max_length=16, pattern=r"^[0-9+\- ]+$")
    redirect_url: str | None = Field(default=None, pattern=r"^https?://", max_length=500)


class AccountAutomationAction(StrictModel):
    action: Literal["enable", "pause", "resume", "disable"]


class AccountUpdate(StrictModel):
    nickname: str | None = Field(default=None, min_length=1, max_length=120)
    account_type: Literal["current", "savings", "deposit", "credit_card", "loan", "escrow", "other"] | None = None
    holder_name: str | None = Field(default=None, max_length=200)
    low_balance_threshold: Decimal | None = Field(default=None, ge=0)
    statement_period: Literal["daily", "weekly", "monthly", "quarterly"] | None = None
    timezone: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=2000)
    sync_schedule: str | None = Field(default=None, max_length=100)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        return _check_tz(v)


class ToggleRequest(StrictModel):
    enabled: bool


class DemoScenarioRequest(StrictModel):
    scenario: Literal["ok", "otp_required", "provider_down", "consent_expired", "auth_failed"]


class PlaidExchangeRequest(StrictModel):
    public_token: str = Field(min_length=10, max_length=500)
    account_external_id: str | None = Field(default=None, max_length=255)


class GoCardlessRequisitionRequest(StrictModel):
    redirect_url: str = Field(pattern=r"^https?://", max_length=500)


class AutomationSettingsUpdate(StrictModel):
    schedule_type: Literal["daily", "custom"] | None = None
    daily_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    cron_expression: str | None = Field(default=None, max_length=100)
    timezone: str | None = Field(default=None, max_length=64)
    lookback_days: int | None = Field(default=None, ge=1, le=90)
    auto_reconcile: bool | None = None
    auto_generate_daily_report: bool | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    retry_base_seconds: int | None = Field(default=None, ge=5, le=3600)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown time zone") from exc
        return v


class RunNowRequest(StrictModel):
    account_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)


class ResumeJobRequest(StrictModel):
    otp: str | None = Field(default=None, max_length=12)


class ReconcileRequest(StrictModel):
    account_id: uuid.UUID
    statement_id: uuid.UUID | None = None
    period_start: date | None = None
    period_end: date | None = None
    rules: dict | None = None


class MatchDecision(StrictModel):
    decision: Literal["confirm", "reject"]
    note: str | None = Field(default=None, max_length=1000)


class ManualMatchRequest(StrictModel):
    transaction_id: uuid.UUID
    book_entry_id: uuid.UUID
    note: str | None = Field(default=None, max_length=1000)


class ExceptionUpdate(StrictModel):
    status: Literal["resolved", "ignored", "open"]
    note: str = Field(min_length=3, max_length=1000)


class ReconRules(StrictModel):
    date_tolerance_days: int = Field(default=3, ge=0, le=31)
    amount_tolerance_abs: Decimal = Field(default=Decimal("0"), ge=0)
    amount_tolerance_pct: Decimal = Field(default=Decimal("0"), ge=0, le=10)
    auto_confirm_threshold: Decimal = Field(default=Decimal("0.90"), ge=Decimal("0.5"), le=1)
    balance_tolerance: Decimal = Field(default=Decimal("0.01"), ge=0)
    match_references: bool = True


class BookEntryCreate(StrictModel):
    account_id: uuid.UUID
    entry_date: date
    description: str = Field(min_length=1, max_length=1000)
    amount: Decimal
    reference: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=2000)


class CategoryCreate(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    colour: str = Field(default="#64748B", pattern=r"^#[0-9A-Fa-f]{6}$")
    keywords: list[str] = Field(default_factory=list, max_length=50)
    direction: Literal["credit", "debit", "any"] = "any"
    priority: int = Field(default=100, ge=0, le=1000)


class BulkCategorise(StrictModel):
    transaction_ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)
    category_id: uuid.UUID | None


class ReportRequest(StrictModel):
    report_type: str = Field(max_length=40)
    file_format: Literal["xlsx", "csv", "pdf"] = "xlsx"
    date: dt.date | None = None
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    account_id: uuid.UUID | None = None
    institution_id: uuid.UUID | None = None


class ExchangeRateCreate(StrictModel):
    base_currency: str = Currency
    quote_currency: str = Currency
    rate: Decimal = Field(gt=0)
    rate_date: date
    source: str = Field(default="manual", max_length=60)


class OrganisationUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    reporting_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    timezone: str | None = Field(default=None, max_length=64)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")


class NotificationPrefsUpdate(StrictModel):
    preferences: dict[str, bool]


class SettingValue(StrictModel):
    value: dict
