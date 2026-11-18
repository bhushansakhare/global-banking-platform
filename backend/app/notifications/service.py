"""In-app notifications fan-out with per-user preferences and de-duplication."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.identity import User
from app.models.system import Notification, NotificationPreference


def notify(db: Session, *, tenant_id: uuid.UUID, type: str, title: str, body: str = "", severity: str = "info",
           link: str | None = None, entity_type: str | None = None, entity_id: uuid.UUID | None = None,
           dedupe_key: str | None = None, dedupe_hours: int = 24) -> int:
    if dedupe_key:
        since = datetime.now(UTC) - timedelta(hours=dedupe_hours)
        exists = db.scalar(select(Notification.id).where(Notification.tenant_id == tenant_id,
                                                         Notification.dedupe_key == dedupe_key,
                                                         Notification.created_at >= since).limit(1))
        if exists:
            return 0
    users = db.scalars(select(User.id).where(User.tenant_id == tenant_id, User.is_active.is_(True),
                                             User.deleted_at.is_(None))).all()
    disabled = set(db.scalars(select(NotificationPreference.user_id).where(
        NotificationPreference.tenant_id == tenant_id, NotificationPreference.type == type,
        NotificationPreference.in_app.is_(False))))
    count = 0
    for uid in users:
        if uid in disabled:
            continue
        db.add(Notification(tenant_id=tenant_id, user_id=uid, type=type, title=title[:200], body=body,
                            severity=severity, link=link, entity_type=entity_type, entity_id=entity_id,
                            dedupe_key=dedupe_key))
        count += 1
    db.flush()
    return count
