"""Append-only audit trail."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import redact
from app.models.system import AuditLog


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def record(db: Session, *, action: str, tenant_id: uuid.UUID | None, actor_user_id: uuid.UUID | None = None,
           actor_type: str = "user", actor_label: str | None = None, entity_type: str | None = None,
           entity_id: Any = None, outcome: str = "success", before: dict | None = None, after: dict | None = None,
           meta: dict | None = None, ip: str | None = None, user_agent: str | None = None,
           request_id: str | None = None) -> AuditLog:
    entry = AuditLog(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_type=actor_type,
        actor_label=actor_label,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        outcome=outcome,
        before=redact(_jsonable(before)) if before else None,
        after=redact(_jsonable(after)) if after else None,
        meta=redact(_jsonable(meta or {})),
        ip_address=ip,
        user_agent=(user_agent or "")[:500] or None,
        request_id=request_id,
    )
    db.add(entry)
    db.flush()
    return entry
