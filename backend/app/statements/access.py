"""Account-bound PDF passwords and in-memory display copies. Originals stay unchanged."""
from contextlib import contextmanager

import fitz
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.crypto import decrypt_json, encrypt_json
from app.core.errors import ValidationFailed
from app.models.system import SystemSetting
from app.statements import storage


class UnlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr = Field(min_length=1, max_length=1024)


def key_for(account, statement=None):
    return f"statement_pdf_password:{account.id}" + (f":{statement.id}" if statement else "")


def saved_password(db, account, statement=None):
    for key in ([key_for(account, statement), key_for(account)] if statement else [key_for(account)]):
        row = db.scalar(select(SystemSetting).where(SystemSetting.tenant_id == account.tenant_id,
                                                    SystemSetting.key == key))
        if row:
            return decrypt_json(row.value["encrypted"], f"{account.tenant_id}:{key}")["password"]
    return None


def remember_password(db, account, password, *, statement=None, user_id=None):
    key = key_for(account, statement)
    value = {"encrypted": encrypt_json({"password": password}, f"{account.tenant_id}:{key}")}
    db.execute(insert(SystemSetting).values(tenant_id=account.tenant_id, key=key, value=value, updated_by=user_id)
        .on_conflict_do_update(index_elements=[SystemSetting.tenant_id, SystemSetting.key],
                              set_={"value": value, "updated_by": user_id}).execution_options(synchronize_session=False))


@contextmanager
def open_pdf(db, statement, account, *, password=None):
    file = statement.file
    if not file or file.detected_format != "pdf":
        raise ValidationFailed("This action is available for PDF statements")
    if not storage.verify_integrity(statement.tenant_id, file.storage_path, file.sha256):
        raise ValidationFailed("Stored statement failed its integrity check")
    path = storage.resolve("statements", statement.tenant_id, file.storage_path)
    try:
        doc = fitz.open(path)
    except Exception:
        raise ValidationFailed("The PDF could not be opened; upload a fresh bank export") from None
    with doc:
        if doc.needs_pass:
            value = password if password is not None else saved_password(db, account, statement)
            if not value or not doc.authenticate(value):
                raise ValidationFailed("Enter this account's statement PDF password to unlock it",
                                       code="statement_password_required")
        yield doc


def password_required(statement):
    return any(w.get("code") == "pdf_password_required" or "password protected" in w.get("message", "").lower()
               for w in (statement.warnings or []))


def access_status(db, statement, account):
    saved = saved_password(db, account, statement)
    result = {"password_saved": bool(saved), "password_required": password_required(statement), "preview_ready": False,
              "encrypted": False}
    if not statement.file or statement.file.detected_format != "pdf":
        return result
    try:
        with open_pdf(db, statement, account) as doc:
            result.update(password_required=False, preview_ready=True, page_count=len(doc), encrypted=bool(doc.needs_pass))
    except ValidationFailed as exc:
        result["password_required"] = exc.code == "statement_password_required"
        result["encrypted"] = result["password_required"]
        if not result["password_required"]:
            result["error"] = exc.message
    return result
