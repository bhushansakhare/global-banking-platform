"""Reports, analytics, notifications, audit logs and settings."""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select, update

from app.analytics import service as analytics
from app.api import serializers as S
from app.api.deps import DB, CurrentUser, Principal, require
from app.audit.service import record as audit
from app.core.config import get_settings
from app.core.errors import NotFound, ValidationFailed
from app.models.identity import Organization
from app.models.system import NOTIFICATION_TYPES, AuditLog, Notification, NotificationPreference, Report, SystemSetting
from app.reports.builders import REPORT_TYPES
from app.reports.renderers import MIME
from app.reports.service import generate_report
from app.schemas.requests import NotificationPrefsUpdate, OrganisationUpdate, ReportRequest, SettingValue
from app.services import demo
from app.statements import storage

router = APIRouter()

# ---- analytics -------------------------------------------------------------
an = APIRouter(prefix="/analytics", tags=["analytics"])


@an.get("/summary")
def summary(db: DB, p: Principal = require("dashboard:read")) -> dict:
    return analytics.summary(db, p.tenant_id)


@an.get("/balance-history")
def balance_history(db: DB, p: Principal = require("dashboard:read"), days: int = Query(90, ge=7, le=730)) -> dict:
    return analytics.balance_history(db, p.tenant_id, days)


@an.get("/cash-flow")
def cash_flow(db: DB, p: Principal = require("dashboard:read"), days: int = Query(30, ge=7, le=365)) -> dict:
    return analytics.cash_flow(db, p.tenant_id, days)


@an.get("/monthly")
def monthly(db: DB, p: Principal = require("dashboard:read"), months: int = Query(12, ge=1, le=36)) -> dict:
    return analytics.monthly_activity(db, p.tenant_id, months)


@an.get("/distributions")
def distributions(db: DB, p: Principal = require("dashboard:read")) -> dict:
    return analytics.distributions(db, p.tenant_id)


@an.get("/reconciliation-performance")
def recon_perf(db: DB, p: Principal = require("dashboard:read"), weeks: int = Query(12, ge=1, le=104)) -> dict:
    return analytics.reconciliation_performance(db, p.tenant_id, weeks)


# ---- reports ---------------------------------------------------------------
rp = APIRouter(prefix="/reports", tags=["reports"])


@rp.get("/types")
def report_types(p: Principal = require("reports:read")) -> list[dict]:
    return [{"code": k, "name": v} for k, v in REPORT_TYPES.items()]


@rp.post("", status_code=201)
def create_report(body: ReportRequest, db: DB, p: Principal = require("reports:write")) -> dict:
    params = body.model_dump(mode="json", exclude={"report_type", "file_format"}, exclude_none=True)
    return S.report(generate_report(db, tenant_id=p.tenant_id, report_type=body.report_type,
                                    file_format=body.file_format, params=params, user_id=p.user_id))


@rp.get("")
def list_reports(db: DB, p: Principal = require("reports:read"), page: int = Query(1, ge=1),
                 page_size: int = Query(25, ge=1, le=200)) -> dict:
    q = select(Report).where(Report.tenant_id == p.tenant_id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(Report.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return S.page([S.report(r) for r in rows], total, page, page_size)


@rp.get("/{report_id}/download")
def download_report(report_id: uuid.UUID, db: DB, p: Principal = require("reports:read")) -> FileResponse:
    r = db.scalar(select(Report).where(Report.id == report_id, Report.tenant_id == p.tenant_id))
    if r is None or not r.storage_path:
        raise NotFound("Report not found")
    path = storage.resolve("reports", p.tenant_id, r.storage_path)
    audit(db, action="report.download", tenant_id=p.tenant_id, actor_user_id=p.user_id, entity_type="report",
          entity_id=r.id)
    db.commit()
    return FileResponse(path, media_type=MIME[r.file_format], filename=path.name,
                        headers={"Cache-Control": "no-store"})


# ---- notifications ---------------------------------------------------------
nt = APIRouter(prefix="/notifications", tags=["notifications"])


@nt.get("")
def list_notifications(db: DB, p: Principal = require("notifications:read"), unread: bool = False,
                       page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=200)) -> dict:
    q = select(Notification).where(Notification.tenant_id == p.tenant_id, Notification.user_id == p.user_id)
    if unread:
        q = q.where(Notification.read_at.is_(None))
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(Notification.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    out = S.page([S.notification(n) for n in rows], total, page, page_size)
    out["unread"] = db.scalar(select(func.count(Notification.id)).where(
        Notification.user_id == p.user_id, Notification.read_at.is_(None))) or 0
    return out


@nt.get("/unread-count")
def unread_count(db: DB, p: CurrentUser) -> dict:
    return {"unread": db.scalar(select(func.count(Notification.id)).where(Notification.user_id == p.user_id,
                                                                        Notification.read_at.is_(None))) or 0}


@nt.post("/{notification_id}/read")
def mark_read(notification_id: uuid.UUID, db: DB, p: CurrentUser) -> dict:
    db.execute(update(Notification).where(Notification.id == notification_id, Notification.user_id == p.user_id)
               .values(read_at=datetime.now(UTC)))
    return {"ok": True}


@nt.post("/read-all")
def mark_all(db: DB, p: CurrentUser) -> dict:
    res = db.execute(update(Notification).where(Notification.user_id == p.user_id, Notification.read_at.is_(None))
                     .values(read_at=datetime.now(UTC)))
    return {"updated": res.rowcount}


@nt.get("/preferences")
def get_prefs(db: DB, p: CurrentUser) -> dict:
    off = {x.type for x in db.scalars(select(NotificationPreference).where(NotificationPreference.user_id == p.user_id,
                                                                          NotificationPreference.in_app.is_(False)))}
    return {t: t not in off for t in NOTIFICATION_TYPES}


@nt.put("/preferences")
def put_prefs(body: NotificationPrefsUpdate, db: DB, p: CurrentUser) -> dict:
    for t, enabled in body.preferences.items():
        if t not in NOTIFICATION_TYPES:
            raise ValidationFailed(f"Unknown notification type {t}")
        pref = db.scalar(select(NotificationPreference).where(NotificationPreference.user_id == p.user_id,
                                                             NotificationPreference.type == t))
        if pref is None:
            db.add(NotificationPreference(tenant_id=p.tenant_id, user_id=p.user_id, type=t, in_app=enabled))
        else:
            pref.in_app = enabled
    db.flush()
    return get_prefs(db, p)


# ---- audit -----------------------------------------------------------------
au = APIRouter(prefix="/audit-logs", tags=["audit"])


@au.get("")
def audit_logs(db: DB, p: Principal = require("audit:read"), action: str | None = None,
               entity_type: str | None = None, actor_user_id: uuid.UUID | None = None, outcome: str | None = None,
               date_from: date | None = None, date_to: date | None = None, page: int = Query(1, ge=1),
               page_size: int = Query(50, ge=1, le=200)) -> dict:
    q = select(AuditLog).where(AuditLog.tenant_id == p.tenant_id)
    if action:
        q = q.where(AuditLog.action.ilike(f"{action}%"))
    if entity_type:
        q = q.where(AuditLog.entity_type == entity_type)
    if actor_user_id:
        q = q.where(AuditLog.actor_user_id == actor_user_id)
    if outcome:
        q = q.where(AuditLog.outcome == outcome)
    if date_from:
        q = q.where(AuditLog.ts >= datetime.combine(date_from, datetime.min.time(), UTC))
    if date_to:
        q = q.where(AuditLog.ts < datetime.combine(date_to, datetime.max.time(), UTC))
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(AuditLog.ts.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return S.page([S.audit_log(a) for a in rows], total, page, page_size)


# ---- settings ----------------------------------------------------------------
st = APIRouter(prefix="/settings", tags=["settings"])
SETTING_KEYS = {"storage", "data_retention", "backup", "import_export", "security", "notifications_defaults",
                "api_integrations", "currency_preferences"}
DEFAULTS = {
    "storage": {"statements_dir": "storage/statements", "reports_dir": "storage/reports", "max_upload_mb": 25},
    "data_retention": {"statements_years": 8, "transactions_years": 8, "audit_logs_years": 10, "job_logs_days": 180},
    "backup": {"enabled": False, "schedule": "daily 02:00", "destination": "", "include_files": True},
    "import_export": {"default_export_format": "xlsx", "csv_delimiter": ",", "date_format": "DD/MM/YYYY"},
    "security": {"session_idle_minutes": 120, "access_token_minutes": 15, "max_failed_logins": 5,
                 "lockout_minutes": 15, "require_strong_passwords": True},
    "notifications_defaults": {"low_balance": True, "failed_jobs": True},
    "api_integrations": {},
    "currency_preferences": {"display_original_currency": True, "fx_source": "manual"},
}


@st.get("")
def get_settings_all(db: DB, p: Principal = require("settings:read")) -> dict:
    org = db.get(Organization, p.tenant_id)
    stored = {s.key: s.value for s in db.scalars(select(SystemSetting).where(SystemSetting.tenant_id == p.tenant_id))}
    cfg = get_settings()
    values = {k: {**DEFAULTS.get(k, {}), **stored.get(k, {})} for k in SETTING_KEYS}
    values["security"].update({"session_idle_minutes": cfg.session_idle_minutes,
                               "access_token_minutes": cfg.access_token_minutes,
                               "max_failed_logins": cfg.max_failed_logins, "lockout_minutes": cfg.lockout_minutes})
    values["api_integrations"] = {"plaid_configured": bool(cfg.plaid_client_id), "plaid_env": cfg.plaid_env,
                                  "gocardless_configured": bool(cfg.gocardless_secret_id)}
    return {"organisation": {"name": org.name, "reporting_currency": org.reporting_currency, "timezone": org.timezone,
                             "country_code": org.country_code, "demo_mode": org.demo_mode},
            "environment": cfg.app_env, "demo_allowed": cfg.demo_allowed, **values}


@st.put("/organisation")
def put_org(body: OrganisationUpdate, db: DB, p: Principal = require("settings:manage")) -> dict:
    org = db.get(Organization, p.tenant_id)
    changes = body.model_dump(exclude_unset=True)
    before = {k: getattr(org, k) for k in changes}
    for k, v in changes.items():
        setattr(org, k, v)
    audit(db, action="settings.organisation", tenant_id=p.tenant_id, actor_user_id=p.user_id, before=before,
          after=changes)
    return {"ok": True}


@st.put("/{key}")
def put_setting(key: str, body: SettingValue, db: DB, p: Principal = require("settings:manage")) -> dict:
    if key not in SETTING_KEYS or key in ("api_integrations",):
        raise ValidationFailed("Unknown or read-only setting")
    s = db.scalar(select(SystemSetting).where(SystemSetting.tenant_id == p.tenant_id, SystemSetting.key == key))
    allowed = set(DEFAULTS.get(key, {}))
    value = {k: v for k, v in body.value.items() if k in allowed}
    before = s.value if s else None
    if s is None:
        db.add(SystemSetting(tenant_id=p.tenant_id, key=key, value=value, updated_by=p.user_id))
    else:
        s.value, s.updated_by = {**s.value, **value}, p.user_id
    audit(db, action=f"settings.{key}", tenant_id=p.tenant_id, actor_user_id=p.user_id, before=before, after=value)
    return {"ok": True, "value": value}


@st.post("/demo/seed", summary="Create clearly labelled demo accounts, FX rates and cash-book")
def seed_demo(db: DB, p: Principal = require("settings:manage")) -> dict:
    return demo.seed_demo(db, p)


@st.post("/demo/clear")
def clear_demo(db: DB, p: Principal = require("settings:manage")) -> dict:
    return demo.clear_demo(db, p)


for r in (an, rp, nt, au, st):
    router.include_router(r)
