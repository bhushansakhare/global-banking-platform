from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.audit.service import record as audit
from app.core.errors import ValidationFailed
from app.models.system import Report
from app.reports.builders import REPORT_TYPES, build
from app.reports.renderers import RENDERERS
from app.statements.storage import save_generated


def generate_report(db: Session, *, tenant_id: uuid.UUID, report_type: str, file_format: str, params: dict[str, Any],
                    user_id: uuid.UUID | None, trigger: str = "manual") -> Report:
    if report_type not in REPORT_TYPES:
        raise ValidationFailed(f"Unknown report type '{report_type}'")
    if file_format not in RENDERERS:
        raise ValidationFailed("Format must be xlsx, csv or pdf")
    try:
        data = build(db, tenant_id, report_type, params)
    except ValueError as exc:
        raise ValidationFailed(str(exc)) from exc
    content = RENDERERS[file_format](data)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    filename = f"{report_type}-{stamp}-{uuid.uuid4().hex[:6]}.{file_format}"
    rel = save_generated("reports", tenant_id, filename, content)
    rep = Report(tenant_id=tenant_id, report_type=report_type, title=f"{data.title} — {data.subtitle.split(' · ')[1]}"
                 if " · " in data.subtitle else data.title, file_format=file_format, parameters=params,
                 storage_path=rel, size_bytes=len(content), status="ready", generated_by=user_id, trigger=trigger,
                 contains_demo_data=data.contains_demo)
    db.add(rep)
    db.flush()
    audit(db, action="report.generate", tenant_id=tenant_id, actor_user_id=user_id,
          actor_type="user" if user_id else "automation", entity_type="report", entity_id=rep.id,
          meta={"type": report_type, "format": file_format})
    return rep
