"""Account-bound encrypted outgoing mail settings; passwords never enter API responses."""
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr
from sqlalchemy import select

from app.core.crypto import decrypt_json, encrypt_json
from app.core.config import get_settings
from app.models.system import SystemSetting


class MailUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = Field(min_length=1, max_length=253, pattern=r"^[a-zA-Z0-9.-]+$")
    port: int = Field(default=587, ge=1, le=65535)
    sender: EmailStr
    username: str = Field(default="", max_length=254)
    password: SecretStr | None = Field(default=None, max_length=4096)
    security: Literal["starttls", "ssl"] = "starttls"


def row_for(db, account):
    return db.scalar(select(SystemSetting).where(SystemSetting.tenant_id == account.tenant_id,
        SystemSetting.key == f"account_smtp:{account.id}"))


def context(account):
    return f"smtp:{account.tenant_id}:{account.id}"


def public_settings(db, account):
    row = row_for(db, account)
    if row is None:
        return {"configured": False, "host": "", "port": 587, "sender": "",
                "username": "", "security": "starttls", "password_saved": False}
    value = decrypt_json(row.value["encrypted"], context(account))
    password = value.pop("password", "")
    return {**value, "configured": True, "password_saved": bool(password)}


def save_mail(db, account, body, user_id):
    row = row_for(db, account)
    previous = decrypt_json(row.value["encrypted"], context(account)) if row else {}
    value = body.model_dump(exclude={"password"}, mode="json")
    # Omitted password preserves it only for the same SMTP identity.
    same_identity = all(previous.get(k) == value.get(k) for k in ("host", "port", "username", "security"))
    value["password"] = (body.password.get_secret_value() if body.password is not None
                         else previous.get("password", "") if same_identity else "")
    if row is None:
        row = SystemSetting(tenant_id=account.tenant_id, key=f"account_smtp:{account.id}")
        db.add(row)
    row.value = {"encrypted": encrypt_json(value, context(account))}
    row.updated_by = user_id
    db.flush()


def mail_settings(db, account):
    row = row_for(db, account)
    if row is None:
        return get_settings()  # Existing installations can retain their environment sender.
    value = decrypt_json(row.value["encrypted"], context(account))
    return SimpleNamespace(smtp_host=value["host"], smtp_port=value["port"], smtp_from=value["sender"],
        smtp_username=value["username"], smtp_password=value["password"], smtp_security=value["security"])


def ready(db, account):
    s = mail_settings(db, account)
    return bool(s.smtp_host and s.smtp_from)
