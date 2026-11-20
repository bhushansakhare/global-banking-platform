from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api import serializers as S
from app.api.deps import DB, Principal, require
from app.automation import service as svc
from app.automation.handlers import settings_for
from app.automation.state import views_for
from app.core.errors import NotFound
from app.models.automation import AutomationJob, AutomationJobLog
from app.models.banking import BankAccount
from app.schemas.requests import AutomationSettingsUpdate, ResumeJobRequest, RunNowRequest, ToggleRequest

router = APIRouter(tags=["automation"])


def _names(db, tenant_id):
    return {a.id: a.nickname for a in db.scalars(select(BankAccount).where(BankAccount.tenant_id == tenant_id))}


@router.get("/automation")
def overview(db: DB, p: Principal = require("automation:read")) -> dict:
    s = settings_for(db, p.tenant_id)
    since = datetime.now(UTC) - timedelta(hours=24)
    counts = dict(db.execute(select(AutomationJob.status, func.count()).where(
        AutomationJob.tenant_id == p.tenant_id, AutomationJob.created_at >= since).group_by(AutomationJob.status)).all())
    active = dict(db.execute(select(AutomationJob.status, func.count()).where(
        AutomationJob.tenant_id == p.tenant_id,
        AutomationJob.status.in_(("queued", "running", "retry_scheduled", "awaiting_user")))
        .group_by(AutomationJob.status)).all())
    accs = db.scalars(select(BankAccount).where(BankAccount.tenant_id == p.tenant_id,
                                                BankAccount.deleted_at.is_(None)).order_by(BankAccount.nickname)).all()
    latest: dict[uuid.UUID, AutomationJob] = {}
    for j in db.scalars(select(AutomationJob).where(AutomationJob.tenant_id == p.tenant_id,
                                                    AutomationJob.job_type.in_(("collect_account", "open_bank_login")),
                                                    AutomationJob.created_at >= datetime.now(UTC) - timedelta(days=7))
                        .order_by(AutomationJob.created_at.desc())):
        latest.setdefault(j.account_id, j)
    views = views_for(db, p.tenant_id, list(accs))
    db.commit()
    return {
        "settings": S.automation_settings(s), "jobs_24h": counts, "active": active,
        "accounts": [{"id": str(a.id), "nickname": a.nickname, "institution": a.institution.name,
                      "automation_enabled": a.automation_enabled, "automation_paused": a.automation_paused,
                      "connection_status": a.connection.status if a.connection else None,
                      "provider": a.connection.provider.name if a.connection else None,
                      "provider_code": a.connection.provider.code if a.connection else None,
                      "connection_method": a.connection.connection_method if a.connection else None,
                      "environment": a.connection.environment if a.connection else None,
                      "health": a.connection_health, "last_sync_at": a.last_sync_at,
                      "last_attempted_sync_at": a.last_attempted_sync_at,
                      "last_successful_sync_at": a.last_successful_sync_at, "last_sync_error": a.last_sync_error,
                      "account_number_masked": a.account_number_masked, "currency": a.currency,
                      "is_demo": a.is_demo, "automation": views.get(a.id),
                      "latest_job": S.job(latest[a.id]) if a.id in latest else None}
                     for a in accs],
    }


@router.put("/automation/settings")
def update_settings(body: AutomationSettingsUpdate, db: DB, p: Principal = require("automation:manage")) -> dict:
    return S.automation_settings(svc.update_settings(db, p.tenant_id, p.user_id, body.model_dump(exclude_unset=True)))


@router.post("/automation/master")
def master(body: ToggleRequest, db: DB, p: Principal = require("automation:manage")) -> dict:
    return S.automation_settings(svc.set_master(db, p.tenant_id, p.user_id, body.enabled))


@router.post("/automation/pause")
def pause(db: DB, p: Principal = require("automation:manage")) -> dict:
    return S.automation_settings(svc.set_paused(db, p.tenant_id, p.user_id, True))


@router.post("/automation/resume")
def resume(db: DB, p: Principal = require("automation:manage")) -> dict:
    return S.automation_settings(svc.set_paused(db, p.tenant_id, p.user_id, False))


@router.post("/automation/run-now", status_code=202)
def run_now(body: RunNowRequest, db: DB, p: Principal = require("automation:manage")) -> dict:
    return svc.run_now(db, p.tenant_id, p.user_id, body.account_ids)


@router.post("/automation/retry-failed")
def retry_failed(db: DB, p: Principal = require("automation:manage")) -> dict:
    return {"requeued": svc.retry_all_failed(db, p.tenant_id, p.user_id)}


@router.get("/jobs")
def list_jobs(db: DB, p: Principal = require("automation:read"), status: str | None = None,
              job_type: str | None = None, account_id: uuid.UUID | None = None, run_id: uuid.UUID | None = None,
              page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)) -> dict:
    q = select(AutomationJob).where(AutomationJob.tenant_id == p.tenant_id)
    if status:
        q = q.where(AutomationJob.status.in_(status.split(",")))
    if job_type:
        q = q.where(AutomationJob.job_type == job_type)
    if account_id:
        q = q.where(AutomationJob.account_id == account_id)
    if run_id:
        q = q.where(AutomationJob.run_id == run_id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(AutomationJob.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    names = _names(db, p.tenant_id)
    return S.page([S.job(j, names.get(j.account_id)) for j in rows], total, page, page_size)


@router.get("/jobs/{job_id}")
def job_detail(job_id: uuid.UUID, db: DB, p: Principal = require("automation:read")) -> dict:
    j = db.scalar(select(AutomationJob).where(AutomationJob.id == job_id, AutomationJob.tenant_id == p.tenant_id))
    if j is None:
        raise NotFound("Job not found")
    logs = db.scalars(select(AutomationJobLog).where(AutomationJobLog.job_id == j.id)
                      .order_by(AutomationJobLog.ts)).all()
    children = db.scalars(select(AutomationJob).where(AutomationJob.parent_job_id == j.id)).all()
    names = _names(db, p.tenant_id)
    return {**S.job(j, names.get(j.account_id)), "payload": {k: v for k, v in j.payload.items() if k != "otp"},
            "logs": [S.job_log(x) for x in logs], "children": [S.job(c, names.get(c.account_id)) for c in children]}


@router.post("/jobs/{job_id}/retry")
def retry(job_id: uuid.UUID, db: DB, p: Principal = require("automation:manage")) -> dict:
    return S.job(svc.retry_job(db, p.tenant_id, p.user_id, job_id))


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: uuid.UUID, db: DB, p: Principal = require("automation:manage")) -> dict:
    return S.job(svc.cancel_job(db, p.tenant_id, p.user_id, job_id))


@router.post("/jobs/{job_id}/resume", summary="Resume a job paused for OTP/CAPTCHA/re-authentication")
def resume_job(job_id: uuid.UUID, body: ResumeJobRequest, db: DB, p: Principal = require("automation:manage")) -> dict:
    return S.job(svc.resume_job(db, p.tenant_id, p.user_id, job_id, body.otp))
