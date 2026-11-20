"""Account-scoped monitoring settings, separate from bank credentials."""
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator
from sqlalchemy import select

from app.core.config import get_settings
from app.models.system import SystemSetting


class MonitoringSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verification_wait_seconds: int = Field(default=600, ge=60, le=720)
    email_enabled: bool = False
    recipients: list[EmailStr] = Field(default_factory=list, max_length=10)
    capture_statement_screenshot: bool = True
    bank_login_method: Literal["password", "qr"] = "password"

    @model_validator(mode="after")
    def require_recipient(self):
        if self.email_enabled and not self.recipients:
            raise ValueError("Add an account-specific email recipient before enabling email alerts")
        return self


class MonitoringUpdate(MonitoringSettings):
    low_balance_threshold: Decimal | None = Field(default=None, ge=0)


def settings_for(db, account) -> MonitoringSettings:
    row = db.scalar(select(SystemSetting).where(SystemSetting.tenant_id == account.tenant_id,
        SystemSetting.key == f"account_monitoring:{account.id}"))
    return MonitoringSettings.model_validate(row.value) if row else MonitoringSettings(
        verification_wait_seconds=get_settings().browser_login_timeout_seconds)


def save_settings(db, account, settings, user_id):
    key = f"account_monitoring:{account.id}"
    # Serialise edits to the same account and its unique settings row.
    from app.models.banking import BankAccount
    db.execute(select(BankAccount.id).where(BankAccount.id == account.id).with_for_update())
    row = db.scalar(select(SystemSetting).where(SystemSetting.tenant_id == account.tenant_id,
                                               SystemSetting.key == key))
    if row is None:
        row = SystemSetting(tenant_id=account.tenant_id, key=key)
        db.add(row)
    row.value = settings.model_dump(mode="json")
    row.updated_by = user_id
    db.flush()


def mail_ready() -> bool:
    s = get_settings()
    return bool(s.smtp_host and s.smtp_from)
