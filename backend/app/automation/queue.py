"""Durable PostgreSQL job queue.

* Claiming uses ``FOR UPDATE SKIP LOCKED`` so many workers/processes can share the table.
* Idempotency keys are unique per tenant — re-enqueueing the same logical job is a no-op.
* Jobs of paused tenants stay queued; nothing is lost.
* The interface (enqueue / claim / complete / fail) is deliberately small so a Redis/Celery
  backend can be substituted later without touching handlers.
"""
from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.automation import AutomationJob, AutomationJobLog


def now() -> datetime:
    return datetime.now(UTC)


def enqueue(db: Session, *, tenant_id: uuid.UUID, job_type: str, account_id: uuid.UUID | None = None,
            payload: dict[str, Any] | None = None, idempotency_key: str | None = None, trigger: str = "manual",
            parent_job_id: uuid.UUID | None = None, run_id: uuid.UUID | None = None, max_attempts: int = 4,
            priority: int = 100, run_after: datetime | None = None, created_by: uuid.UUID | None = None
            ) -> tuple[AutomationJob, bool]:
    """Returns (job, created). If the idempotency key exists the existing job is returned."""
    job_id = uuid.uuid4()
    stmt = insert(AutomationJob).values(
        id=job_id, tenant_id=tenant_id, job_type=job_type, status="queued", trigger=trigger, account_id=account_id,
        parent_job_id=parent_job_id, run_id=run_id, idempotency_key=idempotency_key, payload=payload or {},
        result={}, priority=priority, attempts=0, max_attempts=max_attempts, run_after=run_after or now(),
        progress=0, cancel_requested=False, created_by=created_by, created_at=now(), updated_at=now(),
    )
    # DO NOTHING on any unique conflict: the idempotency key, or the "one active collect job per
    # account" guard (uq_automation_jobs_active_collect) — duplicates are never created.
    stmt = stmt.on_conflict_do_nothing()
    inserted = db.execute(stmt.returning(AutomationJob.id)).scalar()
    if inserted is None:
        job = None
        if idempotency_key:
            job = db.scalar(select(AutomationJob).where(AutomationJob.tenant_id == tenant_id,
                                                        AutomationJob.idempotency_key == idempotency_key))
        if job is None and job_type == "collect_account" and account_id is not None:
            job = active_collect_job(db, account_id)
        assert job is not None, "insert conflicted but no existing job was found"
        return job, False
    job = db.get(AutomationJob, job_id)
    assert job is not None
    log(db, job, "info", f"Job queued ({trigger})", payload=payload or {})
    return job, True


ACTIVE_STATES = ("queued", "running", "retry_scheduled", "awaiting_user")


def active_collect_job(db: Session, account_id: uuid.UUID) -> AutomationJob | None:
    return db.scalar(select(AutomationJob).where(AutomationJob.account_id == account_id,
                                                 AutomationJob.job_type == "collect_account",
                                                 AutomationJob.status.in_(ACTIVE_STATES)).limit(1))


_CLAIM_SQL = text("""
    WITH candidate AS (
        SELECT j.id FROM automation_jobs j
        LEFT JOIN automation_settings s ON s.tenant_id = j.tenant_id
        WHERE j.status IN ('queued','retry_scheduled')
          AND j.run_after <= now()
          AND j.cancel_requested = false
          AND (COALESCE(s.paused, false) = false OR
               (j.trigger = 'manual' AND j.payload->>'user_requested_browser' = 'true'))
        ORDER BY j.priority, j.run_after
        FOR UPDATE OF j SKIP LOCKED
        LIMIT :limit
    )
    UPDATE automation_jobs a
       SET status = 'running', locked_by = :worker, locked_at = now(), heartbeat_at = now(),
           started_at = COALESCE(a.started_at, now()), attempts = a.attempts + 1, updated_at = now(),
           error_code = NULL, requires_user_action = NULL
      FROM candidate
     WHERE a.id = candidate.id
    RETURNING a.id
""")


def claim(db: Session, worker_id: str, limit: int = 1) -> list[uuid.UUID]:
    return list(db.execute(_CLAIM_SQL, {"worker": worker_id, "limit": limit}).scalars())


def log(db: Session, job: AutomationJob, level: str, message: str, **context: Any) -> None:
    from app.core.logging import redact

    db.add(AutomationJobLog(tenant_id=job.tenant_id, job_id=job.id, ts=now(), level=level, message=message[:2000],
                            context=redact(context)))


def heartbeat(db: Session, job_id: uuid.UUID, progress: int | None = None, message: str | None = None) -> bool:
    """Updates the heartbeat; returns True if cancellation has been requested."""
    vals: dict[str, Any] = {"heartbeat_at": now()}
    if progress is not None:
        vals["progress"] = max(0, min(100, progress))
    if message is not None:
        vals["progress_message"] = message[:255]
    row = db.execute(update(AutomationJob).where(AutomationJob.id == job_id, AutomationJob.status == "running")
                     .values(**vals).returning(AutomationJob.cancel_requested)).first()
    # A recovered/timed-out job must not keep driving an orphaned browser or resurrect its heartbeat.
    return row is None or bool(row[0])


def complete(db: Session, job: AutomationJob, result: dict[str, Any]) -> None:
    job.status, job.result, job.finished_at, job.progress = "succeeded", result, now(), 100
    job.locked_by = None
    log(db, job, "info", "Job succeeded", result=result)


def backoff_seconds(attempts: int, base: int) -> int:
    return int(base * (2 ** max(0, attempts - 1)) * (1 + random.uniform(0, 0.25)))


def fail(db: Session, job: AutomationJob, *, code: str, message: str, retryable: bool, base_delay: int = 60) -> bool:
    """Returns True when the job has permanently failed."""
    job.error_code, job.error_message, job.locked_by = code, message[:4000], None
    if retryable and job.attempts < job.max_attempts:
        delay = backoff_seconds(job.attempts, base_delay)
        job.status, job.run_after = "retry_scheduled", now() + timedelta(seconds=delay)
        log(db, job, "warning", f"Attempt {job.attempts} failed; retrying in {delay}s", code=code, error=message)
        return False
    job.status, job.finished_at = "failed", now()
    log(db, job, "error", f"Job failed permanently after {job.attempts} attempt(s)", code=code, error=message)
    return True


def await_user(db: Session, job: AutomationJob, *, action: str, message: str) -> None:
    job.status, job.requires_user_action, job.error_code, job.error_message = "awaiting_user", action, \
        "user_action_required", message
    job.locked_by = None
    job.attempts = max(0, job.attempts - 1)  # waiting for a human is not a failed attempt
    log(db, job, "warning", f"Paused — user action required ({action})", detail=message)


def defer(db: Session, job: AutomationJob, seconds: int, reason: str) -> None:
    job.status, job.run_after, job.locked_by = "queued", now() + timedelta(seconds=seconds), None
    job.attempts = max(0, job.attempts - 1)
    log(db, job, "info", f"Deferred {seconds}s: {reason}")


def cancel(db: Session, job: AutomationJob, reason: str) -> None:
    if job.status in ("queued", "retry_scheduled", "awaiting_user"):
        job.status, job.finished_at = "cancelled", now()
        log(db, job, "info", f"Cancelled: {reason}")
    elif job.status == "running":
        job.cancel_requested = True
        log(db, job, "info", f"Cancellation requested: {reason}")


def recover_stale(db: Session) -> int:
    """Jobs whose worker died (process crash, DB restart) are retried or failed — never left 'running'."""
    s = get_settings()
    cutoff = now() - timedelta(seconds=s.job_stale_seconds)
    hard_timeout = now() - timedelta(seconds=s.job_timeout_seconds)
    stale = db.scalars(select(AutomationJob).where(
        AutomationJob.status == "running",
        (AutomationJob.heartbeat_at < cutoff) |
        (func.coalesce(AutomationJob.locked_at, AutomationJob.started_at) < hard_timeout))
        .with_for_update(skip_locked=True)).all()
    for job in stale:
        attempt_started = job.locked_at or job.started_at
        timed_out = attempt_started is not None and attempt_started < hard_timeout
        fail(db, job, code="job_timeout" if timed_out else "worker_lost",
             message="Job exceeded its timeout" if timed_out else "Worker stopped responding; job recovered",
             retryable=True, base_delay=30)
    return len(stale)
