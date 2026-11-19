"""Multi-threaded job worker.

Each thread claims one job at a time, takes a session-level advisory lock on the
account (per-account isolation — two jobs for the same account never run at once),
executes the handler in its own transaction and records the outcome. A failure of one
job/bank never affects others. Shutdown is graceful: in-flight jobs finish first.
"""
from __future__ import annotations

import os
import socket
import threading
import traceback
import uuid
import zlib

from sqlalchemy import select, text

from app.automation import queue
from app.automation.handlers import HANDLERS, Deferred, JobCancelled, JobContext, PermanentJobError, settings_for
from app.automation.state import compute_health
from app.core.config import get_settings
from app.core.logging import get_logger
from app.database.session import get_engine, get_sessionmaker
from app.integrations.base.adapter import (AuthenticationRequired, ConsentExpired, ProviderError,
                                           UserActionRequired)
from app.models.automation import AutomationJob
from app.models.banking import BankAccount
from app.notifications.service import notify

log = get_logger(__name__)


def _lock_key(account_id: uuid.UUID) -> int:
    return zlib.crc32(account_id.bytes) & 0x7FFFFFFF


def execute_job(job_id: uuid.UUID) -> str:
    """Run one claimed job to completion. Returns the final status. Safe to call from tests."""
    Session = get_sessionmaker()
    db = Session()
    lock_conn = None
    try:
        job = db.get(AutomationJob, job_id)
        if job is None:
            return "missing"
        if job.account_id:
            lock_conn = get_engine().connect()
            got = lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _lock_key(job.account_id)}).scalar()
            lock_conn.commit()
            if not got:
                queue.defer(db, job, 20, "another job is running for this account")
                db.commit()
                lock_conn.close()
                lock_conn = None
                return "queued"
        handler = HANDLERS.get(job.job_type)
        settings = settings_for(db, job.tenant_id)
        if handler is None:
            queue.fail(db, job, code="unknown_job_type", message=job.job_type, retryable=False)
            db.commit()
            return job.status
        queue.log(db, job, "info", f"Attempt {job.attempts} started")
        db.commit()
        try:
            result = handler(JobContext(db, job))
            db.refresh(job, attribute_names=["status"])
            if job.status != "running":
                raise JobCancelled("Execution stopped because this job is no longer running")
            queue.complete(db, job, result)
            db.commit()
        except Deferred as d:
            db.rollback()
            job = db.get(AutomationJob, job_id)
            if job.status != "running":
                return job.status
            if d.state:
                job.payload = {**(job.payload or {}), **d.state}
            if job.job_type == "collect_account" and job.account_id and job.started_at:
                acc = db.get(BankAccount, job.account_id)
                if acc is not None and (acc.last_attempted_sync_at is None
                                        or acc.last_attempted_sync_at < job.started_at):
                    acc.last_attempted_sync_at = job.started_at
            queue.defer(db, job, d.seconds, d.reason)
            if d.state.get("adapter_state") is not None:
                job.error_code = "data_not_ready"
            db.commit()
        except JobCancelled:
            db.rollback()
            job = db.get(AutomationJob, job_id)
            if job.status == "running":
                job.status, job.finished_at = "cancelled", queue.now()
            queue.log(db, job, "info", "Execution stopped; existing terminal job status retained")
            db.commit()
        except UserActionRequired as exc:
            db.rollback()
            job = db.get(AutomationJob, job_id)
            if job.status != "running":
                return job.status
            attempts_before_wait = job.attempts
            queue.await_user(db, job, action=exc.action, message=str(exc))
            job.error_code = exc.code
            if exc.code == "bank_authorization_denied":
                job.attempts = attempts_before_wait  # a rejected authorization is a recorded attempt, not a deferred poll
            _mark_connection(db, job, "requires_auth", str(exc))
            notify(db, tenant_id=job.tenant_id, type="auth_required", severity="warning",
                   title="Bank verification required", body=str(exc), link="/automation?tab=intervention",
                   entity_type="automation_job", entity_id=job.id, dedupe_key=f"auth:{job.account_id}:{exc.action}")
            db.commit()
        except (ProviderError, PermanentJobError, Exception) as exc:  # noqa: BLE001 - isolation boundary
            db.rollback()
            job = db.get(AutomationJob, job_id)
            if job.status != "running":
                return job.status
            retryable = bool(getattr(exc, "retryable", False))
            code = getattr(exc, "code", None) or type(exc).__name__
            message = str(exc) or type(exc).__name__
            from app.integrations.banks.browser_engine import ProfileReviewRequired
            if isinstance(exc, ProfileReviewRequired) and job.account_id:
                from app.integrations.banks.profiles import find_profile
                from app.models.system import SystemSetting
                account = db.get(BankAccount, job.account_id)
                key = account.browser_profile_key if account else None
                if key:
                    try:
                        profile = find_profile(db, job.tenant_id, key)
                    except StopIteration:
                        pass
                    else:
                        setting_key = f"browser_profile:{key}"
                        row = db.scalar(select(SystemSetting).where(SystemSetting.tenant_id == job.tenant_id,
                                                                    SystemSetting.key == setting_key))
                        if row is None:
                            row = SystemSetting(tenant_id=job.tenant_id, key=setting_key)
                            db.add(row)
                        row.value = {**profile.model_dump(mode="json"), "review_required": True}
            if not isinstance(exc, (ProviderError, PermanentJobError)):
                code, retryable = "internal_error", True
                log.error("job crashed", extra={"ctx": {"job": str(job_id), "trace": traceback.format_exc()[-2000:]}})
            permanent = queue.fail(db, job, code=code, message=message, retryable=retryable,
                                   base_delay=settings.retry_base_seconds)
            if isinstance(exc, (ConsentExpired, AuthenticationRequired)):
                _mark_connection(db, job, "consent_expired" if isinstance(exc, ConsentExpired) else "requires_auth",
                                 message)
                notify(db, tenant_id=job.tenant_id, type="auth_required", severity="warning",
                       title="Bank re-authorisation required", body=message, link="/accounts",
                       entity_type="automation_job", entity_id=job.id, dedupe_key=f"consent:{job.account_id}")
            elif job.account_id:
                _mark_connection(db, job, "error", message, failure=True)
            if permanent:
                notify(db, tenant_id=job.tenant_id, type="job_failed", severity="error",
                       title=f"Automation job failed: {job.job_type.replace('_', ' ')}", body=message[:500],
                       link=f"/automation?job={job.id}", entity_type="automation_job", entity_id=job.id)
            db.commit()
        return job.status
    finally:
        if lock_conn is not None:
            try:
                lock_conn.execute(text("SELECT pg_advisory_unlock_all()"))
                lock_conn.commit()
                lock_conn.close()
            except Exception:  # noqa: BLE001
                pass
        db.close()


def _mark_connection(db, job: AutomationJob, status: str, message: str, failure: bool = False) -> None:
    if not job.account_id or job.job_type != "collect_account":
        return
    acc = db.get(BankAccount, job.account_id)
    if acc is None or acc.connection is None:
        return
    conn = acc.connection
    conn.last_error = message[:2000]
    acc.last_sync_error = message[:2000]
    acc.last_attempted_sync_at = job.started_at or queue.now()
    if failure:
        conn.consecutive_failures = (conn.consecutive_failures or 0) + 1
        if conn.consecutive_failures >= 3:
            conn.status = "error"
    else:
        conn.status = status
    acc.connection_health = compute_health(acc)


class Worker:
    def __init__(self, concurrency: int | None = None) -> None:
        s = get_settings()
        self.concurrency = concurrency or s.worker_concurrency
        self.poll = s.worker_poll_seconds
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        with get_sessionmaker()() as db:
            recovered = queue.recover_stale(db)
            db.commit()
        if recovered:
            log.warning("recovered stale jobs", extra={"ctx": {"count": recovered}})
        for i in range(self.concurrency):
            t = threading.Thread(target=self._loop, name=f"gbp-worker-{i}", daemon=True, args=(f"{self.worker_id}:{i}",))
            t.start()
            self._threads.append(t)
        log.info("worker started", extra={"ctx": {"id": self.worker_id, "threads": self.concurrency}})

    def _loop(self, wid: str) -> None:
        Session = get_sessionmaker()
        while not self._stop.is_set():
            try:
                with Session() as db:
                    ids = queue.claim(db, wid, 1)
                    db.commit()
                if not ids:
                    self._stop.wait(self.poll)
                    continue
                execute_job(ids[0])
            except Exception:  # noqa: BLE001 - keep the worker alive (e.g. DB restart)
                log.exception("worker loop error")
                self._stop.wait(min(30.0, self.poll * 5))

    def stop(self, timeout: float = 30.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=timeout)
        log.info("worker stopped")


def run_pending_once(limit: int = 100) -> int:
    """Synchronously drain runnable jobs (used by tests and the CLI)."""
    done = 0
    Session = get_sessionmaker()
    while done < limit:
        with Session() as db:
            ids = queue.claim(db, "inline", 1)
            db.commit()
        if not ids:
            break
        execute_job(ids[0])
        done += 1
    return done


def pending_count() -> int:
    with get_sessionmaker()() as db:
        return len(db.scalars(select(AutomationJob.id).where(AutomationJob.status.in_(("queued", "running")))).all())
