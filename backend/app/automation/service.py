"""Automation control operations exposed through the API."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.audit.service import record as audit
from app.automation import queue
from app.automation.handlers import settings_for
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.integrations.base.browser import otp_vault
from app.models.automation import AutomationJob, AutomationSettings
from app.models.banking import BankAccount
from app.scheduler.scheduler import compute_next_run, start_run

EDITABLE = {"schedule_type", "daily_time", "cron_expression", "timezone", "lookback_days", "auto_reconcile",
            "auto_generate_daily_report", "max_attempts", "retry_base_seconds"}


def _snapshot(s: AutomationSettings) -> dict[str, Any]:
    return {k: getattr(s, k) for k in (*EDITABLE, "master_enabled", "paused")}


def update_settings(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, changes: dict[str, Any]
                    ) -> AutomationSettings:
    s = settings_for(db, tenant_id)
    before = _snapshot(s)
    for k, v in changes.items():
        if k in EDITABLE and v is not None:
            setattr(s, k, v)
    if s.schedule_type == "custom":
        if not s.cron_expression:
            raise ValidationFailed("A cron expression is required for a custom schedule")
        try:
            CronTrigger.from_crontab(s.cron_expression)
        except ValueError as exc:
            raise ValidationFailed(f"Invalid cron expression: {exc}") from exc
    try:
        s.next_run_at = compute_next_run(s) if s.master_enabled else None
    except Exception as exc:  # invalid timezone
        raise ValidationFailed(f"Invalid schedule: {exc}") from exc
    s.updated_by = user_id
    audit(db, action="automation.settings.update", tenant_id=tenant_id, actor_user_id=user_id,
          entity_type="automation_settings", entity_id=s.id, before=before, after=_snapshot(s))
    return s


def set_master(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, enabled: bool) -> AutomationSettings:
    s = settings_for(db, tenant_id)
    s.master_enabled = enabled
    cancelled = 0
    if enabled:
        s.paused = False
        s.next_run_at = compute_next_run(s)
    else:
        s.next_run_at = None
        # stop scheduled work that has not started; running jobs finish safely
        for job in db.scalars(select(AutomationJob).where(
                AutomationJob.tenant_id == tenant_id, AutomationJob.trigger.in_(("schedule", "pipeline")),
                AutomationJob.status.in_(("queued", "retry_scheduled")))):
            queue.cancel(db, job, "automation switched off")
            cancelled += 1
    audit(db, action="automation.master." + ("on" if enabled else "off"), tenant_id=tenant_id,
          actor_user_id=user_id, entity_type="automation_settings", entity_id=s.id,
          meta={"cancelled_queued_jobs": cancelled})
    return s


def set_paused(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, paused: bool) -> AutomationSettings:
    s = settings_for(db, tenant_id)
    s.paused = paused
    audit(db, action="automation." + ("pause" if paused else "resume"), tenant_id=tenant_id, actor_user_id=user_id,
          entity_type="automation_settings", entity_id=s.id)
    return s


def run_now(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, account_ids: list[uuid.UUID] | None
            ) -> dict[str, Any]:
    s = settings_for(db, tenant_id)
    slot = datetime.now(UTC)
    if account_ids:
        run_id = uuid.uuid4()
        n = 0
        for aid in account_ids:
            acc = db.scalar(select(BankAccount).where(BankAccount.id == aid, BankAccount.tenant_id == tenant_id,
                                                      BankAccount.deleted_at.is_(None)))
            if acc is None:
                raise NotFound(f"Account {aid} not found")
            from app.automation.browser_jobs import enqueue_saved_login
            browser_job = enqueue_saved_login(db, acc, user_id, run_id=run_id)
            if browser_job is not None:
                n += int(browser_job[1])
                continue
            _, new = queue.enqueue(db, tenant_id=tenant_id, job_type="collect_account", account_id=acc.id,
                                   payload={"lookback_days": s.lookback_days}, trigger="manual", run_id=run_id,
                                   idempotency_key=f"collect:{acc.id}:{slot:%Y%m%dT%H%M%S}:manual",
                                   max_attempts=s.max_attempts, created_by=user_id)
            n += int(new)
    else:
        run_id, n = start_run(db, s, trigger="manual", slot=slot.replace(microsecond=0), user_id=user_id)
    db.flush()  # resumed jobs change run_id/status in a session with autoflush disabled
    run_jobs = list(db.scalars(select(AutomationJob).where(AutomationJob.tenant_id == tenant_id,
        AutomationJob.run_id == run_id, AutomationJob.job_type.in_(("collect_account", "open_bank_login")))))
    resumed = max(0, len(run_jobs) - n)
    started = n + resumed
    active_query = select(func.count()).select_from(AutomationJob).where(AutomationJob.tenant_id == tenant_id,
        AutomationJob.job_type.in_(("collect_account", "open_bank_login")),
        AutomationJob.status.in_(("queued", "running", "retry_scheduled")))
    if account_ids:
        active_query = active_query.where(AutomationJob.account_id.in_(account_ids))
    existing = max(0, (db.scalar(active_query) or 0) - started)
    if started:
        browser_count = sum(j.job_type == "open_bank_login" or bool(j.payload.get("authenticate")) for j in run_jobs)
        message = f"{browser_count} bank browser login(s) queued" if browser_count else f"{started} collection job(s) queued"
    elif existing:
        message = "A bank run is already queued or running; another browser was not opened"
    else:
        raise Conflict("No runnable bank account found. Save a bank login or select a configured account.", code="no_eligible_accounts")
    audit(db, action="automation.run_now", tenant_id=tenant_id, actor_user_id=user_id, entity_type="automation_run",
          entity_id=run_id, meta={"jobs": n, "accounts": [str(a) for a in account_ids or []]})
    return {"run_id": str(run_id), "jobs_created": n, "jobs_resumed": resumed, "jobs_started": started,
            "already_in_progress": existing, "message": message}


def _job(db: Session, tenant_id: uuid.UUID, job_id: uuid.UUID) -> AutomationJob:
    job = db.scalar(select(AutomationJob).where(AutomationJob.id == job_id, AutomationJob.tenant_id == tenant_id)
                    .with_for_update())
    if job is None:
        raise NotFound("Job not found")
    return job


def retry_job(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, job_id: uuid.UUID) -> AutomationJob:
    job = _job(db, tenant_id, job_id)
    if job.status not in ("failed", "cancelled"):
        raise Conflict(f"Only failed or cancelled jobs can be retried (status: {job.status})")
    if job.job_type == "collect_account" and job.account_id and queue.active_collect_job(db, job.account_id):
        raise Conflict("A synchronisation is already queued or running for this account")
    job.status, job.run_after, job.attempts, job.finished_at = "queued", datetime.now(UTC), 0, None
    job.error_code = job.error_message = None
    job.cancel_requested = False
    job.trigger = "retry"
    queue.log(db, job, "info", "Manually re-queued")
    audit(db, action="automation.job.retry", tenant_id=tenant_id, actor_user_id=user_id, entity_type="automation_job",
          entity_id=job.id)
    return job


def retry_all_failed(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> int:
    ids = list(db.scalars(select(AutomationJob.id).where(AutomationJob.tenant_id == tenant_id,
                                                         AutomationJob.status == "failed",
                                                         AutomationJob.created_at >= datetime.now(UTC) - timedelta(days=7))))
    n = 0
    for jid in ids:
        try:
            retry_job(db, tenant_id, user_id, jid)
            db.flush()
            n += 1
        except Conflict:
            continue  # an account already has an active sync, or an older failure for it was re-queued
    return n


def cancel_job(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, job_id: uuid.UUID) -> AutomationJob:
    job = _job(db, tenant_id, job_id)
    if job.status in ("succeeded", "failed", "cancelled"):
        raise Conflict("Job has already finished")
    queue.cancel(db, job, "cancelled by user")
    audit(db, action="automation.job.cancel", tenant_id=tenant_id, actor_user_id=user_id,
          entity_type="automation_job", entity_id=job.id)
    return job


def resume_job(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, job_id: uuid.UUID, otp: str | None
               ) -> AutomationJob:
    """Resume a job waiting for a human (OTP/CAPTCHA/re-auth). OTP is handed over in memory only."""
    job = _job(db, tenant_id, job_id)
    if job.status != "awaiting_user":
        raise Conflict("Job is not waiting for user action")
    acc = db.get(BankAccount, job.account_id) if job.account_id else None
    if acc and acc.connection and acc.connection.provider.code == "browser_automation":
        if otp:
            raise ValidationFailed("Enter verification codes only on the official bank page")
        job.payload = {**job.payload, "interactive_approved": True}
    if job.requires_user_action == "otp":
        if not acc or not acc.is_demo:
            raise ValidationFailed("Enter verification codes only on the official bank page")
        if not otp or not otp.strip().isalnum() or not (4 <= len(otp.strip()) <= 12):
            raise ValidationFailed("A valid one-time passcode is required")
        otp_vault.put(str(job.id), otp.strip())
        acc = db.get(BankAccount, job.account_id) if job.account_id else None
        if acc and acc.is_demo:  # demo scenario acknowledges the OTP step
            job.payload = {**job.payload, "otp_verified": True}
    job.status, job.run_after, job.requires_user_action = "queued", datetime.now(UTC), None
    job.error_code = job.error_message = None
    queue.log(db, job, "info", "Resumed after user verification")  # OTP value is never logged
    audit(db, action="automation.job.resume", tenant_id=tenant_id, actor_user_id=user_id,
          entity_type="automation_job", entity_id=job.id)
    return job


def account_action(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, account_id: uuid.UUID,
                   action: str) -> BankAccount:
    """enable | pause | resume | disable for one account."""
    acc = db.scalar(select(BankAccount).where(BankAccount.id == account_id, BankAccount.tenant_id == tenant_id,
                                              BankAccount.deleted_at.is_(None)))
    if acc is None:
        raise NotFound("Account not found")
    before = {"enabled": acc.automation_enabled, "paused": acc.automation_paused}
    if action == "enable":
        acc.automation_enabled, acc.automation_paused = True, False
    elif action == "disable":
        acc.automation_enabled, acc.automation_paused = False, False
    elif action == "pause":
        if not acc.automation_enabled:
            raise Conflict("Automation is not enabled for this account")
        acc.automation_paused = True
    elif action == "resume":
        acc.automation_paused = False
    else:
        raise ValidationFailed("Unknown action")
    if action in ("pause", "disable"):
        db.execute(update(AutomationJob).where(AutomationJob.account_id == acc.id,
                                               AutomationJob.trigger == "schedule",
                                               AutomationJob.status.in_(("queued", "retry_scheduled")))
                   .values(status="cancelled", finished_at=datetime.now(UTC)))
        acc.next_sync_at = None
    elif acc.sync_schedule:
        from app.scheduler.scheduler import compute_account_next
        acc.next_sync_at = compute_account_next(acc, settings_for(db, tenant_id))
    audit(db, action=f"account.automation.{action}", tenant_id=tenant_id, actor_user_id=user_id,
          entity_type="bank_account", entity_id=acc.id, before=before,
          after={"enabled": acc.automation_enabled, "paused": acc.automation_paused})
    return acc


def sync_account_now(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, account_id: uuid.UUID) -> dict[str, Any]:
    """Queue an immediate collection — refused with the exact reason when nothing can be collected."""
    from app.automation.state import is_manual
    from app.integrations.registry import get_adapter

    acc = db.scalar(select(BankAccount).where(BankAccount.id == account_id, BankAccount.tenant_id == tenant_id,
                                              BankAccount.deleted_at.is_(None)))
    if acc is None:
        raise NotFound("Account not found")
    from app.automation.browser_jobs import enqueue_saved_login
    browser_job = enqueue_saved_login(db, acc, user_id)
    if browser_job is not None:
        job, created = browser_job
        audit(db, action="automation.account.saved_login", tenant_id=tenant_id, actor_user_id=user_id,
              entity_type="bank_account", entity_id=acc.id, meta={"job": str(job.id)})
        return {"job_id": str(job.id), "created": created, "status": job.status}
    c = acc.connection
    if c is None or c.status == "disconnected":
        raise Conflict("Connect the bank before syncing", code="not_configured")
    if is_manual(acc):
        raise Conflict("This account uses manual statement import — there is nothing to collect automatically. "
                       "Upload a statement, or connect the bank through a supported method.", code="not_configured")
    if not get_adapter(c.provider.code).is_configured():
        raise Conflict(f"{c.provider.name} is awaiting provider setup on this platform", code="provider_setup_required")
    if c.status in ("pending", "requires_auth", "consent_expired") and c.provider.code != "browser_automation":
        raise Conflict("Authorisation is required before syncing", code="authorization_required")
    active = queue.active_collect_job(db, acc.id)
    if active is not None:
        return {"job_id": str(active.id), "created": False, "status": active.status}
    s = settings_for(db, tenant_id)
    slot = datetime.now(UTC)
    job, new = queue.enqueue(db, tenant_id=tenant_id, job_type="collect_account", account_id=acc.id,
                             payload={"lookback_days": s.lookback_days}, trigger="manual",
                             idempotency_key=f"collect:{acc.id}:{slot:%Y%m%dT%H%M%S}:manual",
                             max_attempts=s.max_attempts, created_by=user_id)
    audit(db, action="automation.account.sync_now", tenant_id=tenant_id, actor_user_id=user_id,
          entity_type="bank_account", entity_id=acc.id, meta={"job": str(job.id)})
    return {"job_id": str(job.id), "created": new, "status": job.status}


def set_account_automation(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, account_id: uuid.UUID,
                           enabled: bool) -> BankAccount:
    acc = db.scalar(select(BankAccount).where(BankAccount.id == account_id, BankAccount.tenant_id == tenant_id,
                                              BankAccount.deleted_at.is_(None)))
    if acc is None:
        raise NotFound("Account not found")
    acc.automation_enabled = enabled
    if not enabled:
        db.execute(update(AutomationJob).where(AutomationJob.account_id == acc.id,
                                               AutomationJob.trigger == "schedule",
                                               AutomationJob.status.in_(("queued", "retry_scheduled")))
                   .values(status="cancelled", finished_at=datetime.now(UTC)))
    audit(db, action="account.automation." + ("on" if enabled else "off"), tenant_id=tenant_id,
          actor_user_id=user_id, entity_type="bank_account", entity_id=acc.id)
    return acc
