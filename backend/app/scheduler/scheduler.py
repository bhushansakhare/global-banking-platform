"""Daily automation scheduler.

APScheduler only provides the heartbeat tick; the *schedule itself* is persisted in
``automation_settings.next_run_at`` so restarts never lose or duplicate a run. A
PostgreSQL advisory lock ensures that only one process executes the tick at a time,
and per-slot idempotency keys make double-enqueueing impossible.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session

from app.automation import queue
from app.core.logging import get_logger
from app.database.session import get_sessionmaker
from app.models.automation import AutomationSettings
from app.models.banking import BankAccount, BankConnection, BankProvider

log = get_logger(__name__)
_TICK_LOCK = 0x6B6270  # "gbp"


def build_trigger(s: AutomationSettings) -> CronTrigger:
    tz = ZoneInfo(s.timezone or "UTC")
    if s.schedule_type == "custom" and s.cron_expression:
        return CronTrigger.from_crontab(s.cron_expression, timezone=tz)
    hh, mm = (s.daily_time or "06:00").split(":")
    return CronTrigger(hour=int(hh), minute=int(mm), timezone=tz)


def compute_next_run(s: AutomationSettings, after: datetime | None = None) -> datetime | None:
    nxt = build_trigger(s).get_next_fire_time(None, after or datetime.now(UTC))
    return nxt.astimezone(UTC) if nxt else None


def account_trigger(acc: BankAccount, s: AutomationSettings) -> CronTrigger:
    tz = ZoneInfo(acc.timezone or s.timezone or "UTC")
    return CronTrigger.from_crontab(acc.sync_schedule, timezone=tz)


def compute_account_next(acc: BankAccount, s: AutomationSettings, after: datetime | None = None) -> datetime | None:
    if not acc.sync_schedule:
        return None
    nxt = account_trigger(acc, s).get_next_fire_time(None, after or datetime.now(UTC))
    return nxt.astimezone(UTC) if nxt else None


def _base_eligible(tenant_id: uuid.UUID):
    """Accounts that can genuinely be collected automatically: enabled, not paused, connected through a
    remote method (never manual import) and authorised."""
    return (select(BankAccount).join(BankConnection, BankConnection.id == BankAccount.connection_id)
            .join(BankProvider, BankProvider.id == BankConnection.provider_id)
            .where(BankAccount.tenant_id == tenant_id, BankAccount.deleted_at.is_(None),
                   BankAccount.automation_enabled.is_(True), BankAccount.automation_paused.is_(False),
                   BankConnection.deleted_at.is_(None),
                   or_(BankConnection.status.notin_(("disconnected", "pending", "requires_auth", "consent_expired")),
                       and_(BankProvider.code == "browser_automation", BankConnection.status == "requires_auth")),
                   BankProvider.code != "manual_upload"))


def eligible_accounts(db: Session, tenant_id: uuid.UUID, *, own_schedule: bool | None = False) -> list[BankAccount]:
    q = _base_eligible(tenant_id)
    if own_schedule is False:
        q = q.where(BankAccount.sync_schedule.is_(None))
    elif own_schedule is True:
        q = q.where(BankAccount.sync_schedule.is_not(None))
    accounts = list(db.scalars(q.order_by(BankAccount.nickname)))
    # A manual-import connection may also have an owner-reviewed browser observation flow.
    from app.automation.browser_jobs import saved_login_profile, reviewed_statement_flow
    from app.core.config import get_settings
    if get_settings().browser_automation_enabled:
        seen = {a.id for a in accounts}
        candidates = select(BankAccount).where(BankAccount.tenant_id == tenant_id,
            BankAccount.deleted_at.is_(None), BankAccount.automation_enabled.is_(True),
            BankAccount.automation_paused.is_(False),
            or_(BankAccount.browser_profile_key.is_not(None), BankAccount.browser_identifier_encrypted.is_not(None)))
        if own_schedule is False:
            candidates = candidates.where(BankAccount.sync_schedule.is_(None))
        elif own_schedule is True:
            candidates = candidates.where(BankAccount.sync_schedule.is_not(None))
        for account in db.scalars(candidates.order_by(BankAccount.nickname)):
            if account.id in seen:
                continue
            profile = saved_login_profile(db, account)
            if profile and reviewed_statement_flow(db, account, profile):
                accounts.append(account)
    return accounts


def manual_accounts(db: Session, tenant_id: uuid.UUID) -> list[BankAccount]:
    return list(db.scalars(
        select(BankAccount).join(BankConnection, BankConnection.id == BankAccount.connection_id)
        .join(BankProvider, BankProvider.id == BankConnection.provider_id)
        .where(BankAccount.tenant_id == tenant_id, BankAccount.deleted_at.is_(None),
               BankProvider.code == "manual_upload")))


def start_run(db: Session, s: AutomationSettings, *, trigger: str, slot: datetime,
              user_id: uuid.UUID | None = None) -> tuple[uuid.UUID, int]:
    run_id = uuid.uuid4()
    slot_key = slot.strftime("%Y%m%dT%H%M")
    created = 0
    accounts = eligible_accounts(db, s.tenant_id, own_schedule=False if trigger == "schedule" else None)
    if trigger == "manual":
        seen = {a.id for a in accounts}
        saved_accounts = db.scalars(select(BankAccount).where(BankAccount.tenant_id == s.tenant_id,
            BankAccount.deleted_at.is_(None),
            or_(BankAccount.browser_identifier_encrypted.is_not(None), BankAccount.browser_profile_key.is_not(None))))
        for account in saved_accounts:
            # Run now is an explicit one-off request; scheduling switches must not silently skip it.
            if account.id not in seen:
                from app.automation.browser_jobs import saved_login_profile
                if saved_login_profile(db, account):
                    accounts.append(account)
    for acc in accounts:
        if trigger in ("manual", "schedule"):
            from app.automation.browser_jobs import enqueue_saved_login
            browser_job = enqueue_saved_login(db, acc, user_id, run_id=run_id, trigger=trigger,
                idempotency_key=f"browser:{acc.id}:{slot_key}:{trigger}" if trigger == "schedule" else None)
            if browser_job is not None:
                created += int(browser_job[1])
                continue
        _, new = queue.enqueue(db, tenant_id=s.tenant_id, job_type="collect_account", account_id=acc.id,
                               payload={"lookback_days": s.lookback_days}, trigger=trigger, run_id=run_id,
                               idempotency_key=f"collect:{acc.id}:{slot_key}:{trigger}",
                               max_attempts=s.max_attempts, priority=100, created_by=user_id)
        created += int(new)
    if trigger == "schedule":
        # manual-import accounts cannot be collected; only the missing-statement check runs for them
        from app.automation.handlers import _check_missing_statement
        for acc in manual_accounts(db, s.tenant_id):
            from app.automation.browser_jobs import saved_login_profile, reviewed_statement_flow
            profile = saved_login_profile(db, acc)
            flow = reviewed_statement_flow(db, acc, profile) if profile else None
            if flow and flow.get("purpose") == "bank_observation":
                continue  # This account intentionally saves a bank screen rather than a statement file.
            _check_missing_statement(db, acc)
    if s.auto_generate_daily_report and created:
        queue.enqueue(db, tenant_id=s.tenant_id, job_type="daily_report", trigger=trigger, run_id=run_id,
                      payload={"date": slot.date().isoformat()}, idempotency_key=f"report:{slot_key}:{trigger}",
                      priority=200, created_by=user_id)
    return run_id, created


def tick() -> int:
    """One scheduler pass. Returns the number of tenants whose run was started."""
    started = 0
    Session = get_sessionmaker()
    with Session() as db:
        if not db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _TICK_LOCK}).scalar():
            return 0
        recovered = queue.recover_stale(db)
        if recovered:
            log.warning("scheduler recovered stale jobs", extra={"ctx": {"count": recovered}})
        now = datetime.now(UTC)
        for s in db.scalars(select(AutomationSettings).where(AutomationSettings.master_enabled.is_(True))
                            .with_for_update(skip_locked=True)):
            if s.next_run_at is None:
                s.next_run_at = compute_next_run(s, now)
                continue
            if s.paused or s.next_run_at > now:
                continue
            slot = s.next_run_at
            # a run missed by more than 6 h (e.g. machine off overnight) is executed once, not replayed
            run_id, n = start_run(db, s, trigger="schedule", slot=slot)
            s.last_run_at = now
            s.next_run_at = compute_next_run(s, now)
            started += 1
            log.info("scheduled run started", extra={"ctx": {"tenant": str(s.tenant_id), "run": str(run_id),
                                                             "jobs": n, "next": str(s.next_run_at)}})
        # accounts with their own schedule (per-account cron + time zone)
        for s in db.scalars(select(AutomationSettings).where(AutomationSettings.master_enabled.is_(True),
                                                             AutomationSettings.paused.is_(False))):
            for acc in eligible_accounts(db, s.tenant_id, own_schedule=True):
                try:
                    if acc.next_sync_at is None:
                        acc.next_sync_at = compute_account_next(acc, s, now)
                        continue
                    if acc.next_sync_at > now:
                        continue
                    slot_key = acc.next_sync_at.strftime("%Y%m%dT%H%M")
                    from app.automation.browser_jobs import enqueue_saved_login
                    browser_job = enqueue_saved_login(db, acc, None, trigger="schedule",
                        idempotency_key=f"browser:{acc.id}:{slot_key}:account-schedule")
                    if browser_job is None:
                        queue.enqueue(db, tenant_id=s.tenant_id, job_type="collect_account", account_id=acc.id,
                                      payload={"lookback_days": s.lookback_days}, trigger="schedule",
                                      idempotency_key=f"collect:{acc.id}:{slot_key}:account-schedule",
                                      max_attempts=s.max_attempts, priority=100)
                    acc.next_sync_at = compute_account_next(acc, s, now)
                except (ValueError, KeyError) as exc:  # invalid cron/time zone: surface, never crash the tick
                    acc.last_sync_error = f"Invalid account schedule: {exc}"
                    acc.next_sync_at = None
        from app.integrations.finzola.gateway import schedule_tick
        schedule_tick(db, now)
        db.commit()
    return started


class AutomationScheduler:
    def __init__(self, interval_seconds: int = 30) -> None:
        self._sched = BackgroundScheduler(timezone="UTC")
        self._sched.add_job(self._safe_tick, "interval", seconds=interval_seconds, id="gbp-tick",
                            max_instances=1, coalesce=True, next_run_time=datetime.now(UTC) + timedelta(seconds=3))

    @staticmethod
    def _safe_tick() -> None:
        try:
            tick()
        except Exception:  # noqa: BLE001 - never kill the scheduler thread
            log.exception("scheduler tick failed")

    def start(self) -> None:
        self._sched.start()
        log.info("scheduler started")

    def stop(self) -> None:
        self._sched.shutdown(wait=False)
