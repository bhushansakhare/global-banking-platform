"""Job handlers. Each runs inside its own DB transaction managed by the worker."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.automation import queue
from app.balances.service import check_low_balance, record_snapshot
from app.core.config import get_settings
from app.core.crypto import decrypt_json
from app.automation.state import compute_health, is_manual
from app.integrations.base.adapter import (Capability, ConfigurationMissing, ConnectionContext, ConsentExpired,
                                           DataNotReady, ProviderUnavailable)
from app.integrations.registry import get_adapter
from app.models.automation import AutomationJob, AutomationSettings
from app.models.banking import BankAccount, BankConnection
from app.models.statements import Statement
from app.models.transactions import BookEntry
from app.notifications.service import notify
from app.reconciliation.service import run_reconciliation
from app.statements.pipeline import process_statement, register_statement
from app.transactions.ingest import IncomingTransaction, ingest


class JobCancelled(Exception):
    pass


class Deferred(Exception):
    """Re-check later without consuming an attempt. ``state`` is merged into the job payload so
    progress (e.g. an AA data-session id) survives the transaction rollback."""

    def __init__(self, seconds: int, reason: str, state: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.seconds, self.reason, self.state = seconds, reason, state or {}


MAX_DATA_WAIT_CYCLES = 40  # ~30 min of AA data-session polling before it counts as a failed attempt


class PermanentJobError(Exception):
    code = "invalid_job"


@dataclass
class JobContext:
    db: Session
    job: AutomationJob

    def progress(self, pct: int, message: str) -> None:
        if queue.heartbeat(self.db, self.job.id, pct, message):
            raise JobCancelled("Cancellation requested")
        queue.log(self.db, self.job, "info", message, progress=pct)
        self.db.flush()


def settings_for(db: Session, tenant_id: uuid.UUID) -> AutomationSettings:
    s = db.scalar(select(AutomationSettings).where(AutomationSettings.tenant_id == tenant_id))
    if s is None:
        s = AutomationSettings(tenant_id=tenant_id)
        db.add(s)
        db.flush()
    return s


def connection_context(conn: BankConnection, account: BankAccount | None,
                       job_state: dict[str, Any] | None = None) -> ConnectionContext:
    creds = decrypt_json(conn.encrypted_credentials, str(conn.tenant_id)) if conn.encrypted_credentials else {}
    return ConnectionContext(connection_id=str(conn.id), tenant_id=str(conn.tenant_id),
                             institution_external_id=conn.institution.external_id,
                             external_reference=conn.external_reference, credentials=creds,
                             account_external_id=account.external_account_id if account else None,
                             account_currency=account.currency if account else None,
                             account_masked_number=account.account_number_masked if account else None,
                             account_type=account.account_type if account else None,
                             job_state=dict(job_state or {}))


# ---------------------------------------------------------------------------
def collect_account(ctx: JobContext) -> dict[str, Any]:
    db, job = ctx.db, ctx.job
    account = db.scalar(select(BankAccount).where(BankAccount.id == job.account_id,
                                                  BankAccount.tenant_id == job.tenant_id,
                                                  BankAccount.deleted_at.is_(None)))
    if account is None:
        raise PermanentJobError("Account no longer exists")
    if job.trigger == "schedule" and (not account.automation_enabled or account.automation_paused):
        return {"skipped": "automation disabled or paused for this account", "data_retrieved": False}
    if account.connection and account.connection.provider.code == "browser_automation":
        from app.automation.browser_jobs import browser_collect
        return browser_collect(ctx)
    conn = account.connection
    if conn is None or conn.status == "disconnected" or conn.deleted_at is not None:
        return {"skipped": "account has no active connection", "data_retrieved": False}
    provider = conn.provider
    if provider.is_demo and not get_settings().demo_allowed:
        raise ConfigurationMissing("Demo provider is disabled in this environment")

    settings = settings_for(db, job.tenant_id)
    adapter = get_adapter(provider.code)
    job_state = job.payload.get("adapter_state") or {}
    cctx = connection_context(conn, account, job_state)
    if job.payload.get("otp_verified"):
        cctx.credentials["otp_verified"] = True  # demo scenario only; real OTPs go through the OTP vault
    result: dict[str, Any] = {"provider": provider.code, "is_demo": provider.is_demo,
                              "environment": conn.environment or adapter.environment()}
    src = "demo" if provider.is_demo else "provider"
    verification = "demo" if provider.is_demo else "provider_reported"
    if not job.payload.get("attempt_recorded"):
        account.last_attempted_sync_at = datetime.now(UTC)
    ctx.progress(5, f"Checking connection via {provider.name}")

    if is_manual(account) or not adapter.capabilities:
        # manual import channel: nothing can be fetched; never reported as a successful sync
        _check_missing_statement(db, account)
        conn.last_checked_at = datetime.now(UTC)
        account.connection_health = "not_applicable"
        return {**result, "skipped": "manual statement import - no automatic collection is possible",
                "data_retrieved": False}
    if not adapter.is_configured():
        raise ConfigurationMissing(f"{provider.name} is not configured on this platform (provider setup required)")

    status = adapter.check_connection(cctx)
    conn.last_checked_at = datetime.now(UTC)
    if not status.ok:
        if status.status == "error":
            raise ProviderUnavailable(status.message)
        if status.status == "consent_expired":
            raise ConsentExpired(status.message)
        # requires_auth -> the data calls below raise the precise user-action error

    today = datetime.now(UTC).date()
    since = today - timedelta(days=int(job.payload.get("lookback_days") or settings.lookback_days))
    try:
        adapter.prepare_sync(cctx, since, today)
    except DataNotReady as wait:
        cycles = int(job.payload.get("data_wait_cycles") or 0) + 1
        if cycles > MAX_DATA_WAIT_CYCLES:
            raise ProviderUnavailable("The provider did not deliver data in time; this attempt will be retried") \
                from wait
        raise Deferred(wait.retry_after, str(wait), state={
            "adapter_state": {**job_state, **wait.state}, "data_wait_cycles": cycles,
            "attempt_recorded": True}) from wait

    if adapter.supports(Capability.BALANCES):
        ctx.progress(20, "Fetching balances")
        n = 0
        for b in adapter.get_balances(cctx):
            n += record_snapshot(db, account=account, balance_type=b.balance_type, amount=b.amount,
                                 currency=b.currency, as_of=b.as_of, source=src, source_ref=provider.code,
                                 job_id=job.id, raw=b.raw, verification_status=verification)
        result["balances_recorded"] = n

    if adapter.supports(Capability.TRANSACTIONS):
        ctx.progress(45, "Fetching transactions")
        txs = adapter.get_transactions(cctx, since, today)
        same_ccy = [t for t in txs if t.currency == account.currency]
        ing = ingest(db, account, [
            IncomingTransaction(booking_date=t.booking_date, value_date=t.value_date, description=t.description,
                                reference=t.reference, debit=-t.amount if t.amount < 0 else Decimal(0),
                                credit=t.amount if t.amount > 0 else Decimal(0), currency=t.currency,
                                running_balance=t.running_balance, provider_transaction_id=t.provider_transaction_id,
                                raw=t.raw) for t in same_ccy], source=src)
        result.update(transactions_fetched=len(txs), transactions_new=ing.inserted,
                      transactions_duplicate=ing.duplicates, duplicate_suspects=ing.suspects)
        if len(same_ccy) != len(txs):  # never mix currencies inside one account ledger
            result["transactions_other_currency_skipped"] = len(txs) - len(same_ccy)

    if not account.external_account_id and hasattr(adapter, "matched_account"):
        try:
            account.external_account_id = adapter.matched_account(cctx).external_account_id
        except Exception:  # noqa: BLE001 - best effort; matching was validated in prepare_sync
            pass

    new_statements: list[uuid.UUID] = []
    if adapter.supports(Capability.STATEMENTS):
        ctx.progress(70, "Checking for new statements")
        start = (account.last_statement_date + timedelta(days=1)) if account.last_statement_date else \
            today - timedelta(days=int(job.payload.get("statement_history_days", 62)))
        if start <= today:
            for sd in adapter.get_statements(cctx, start, today):
                st, dup = register_statement(db, account=account, content=sd.content, filename=sd.filename,
                                             source="demo" if provider.is_demo else "provider_download",
                                             external_statement_id=sd.external_statement_id,
                                             period_start=sd.period_start, period_end=sd.period_end)
                if not dup:
                    new_statements.append(st.id)
                    queue.enqueue(db, tenant_id=job.tenant_id, job_type="parse_statement", account_id=account.id,
                                  payload={"statement_id": str(st.id)}, idempotency_key=f"parse:{st.id}",
                                  trigger="pipeline", parent_job_id=job.id, run_id=job.run_id, priority=50)
        result["statements_new"] = len(new_statements)

    now = datetime.now(UTC)
    account.last_sync_at = account.last_successful_sync_at = now
    account.last_sync_error = None
    result["data_retrieved"] = True
    if status.consent_expires_at:
        result["consent_expires_at"] = status.consent_expires_at.isoformat()
    account.last_sync_result = {k: v for k, v in result.items() if isinstance(v, (int, str, bool))}
    conn.status, conn.last_success_at, conn.consecutive_failures, conn.last_error = "active", now, 0, None
    account.connection_health = compute_health(account, now)
    check_low_balance(db, account)

    if settings.auto_reconcile and not new_statements:
        has_book = db.scalar(select(func.count(BookEntry.id)).where(BookEntry.account_id == account.id,
                                                                    BookEntry.deleted_at.is_(None)))
        if has_book:
            p_end = today - timedelta(days=1)
            p_start = p_end - timedelta(days=settings.lookback_days)
            queue.enqueue(db, tenant_id=job.tenant_id, job_type="reconcile_account", account_id=account.id,
                          payload={"period_start": p_start.isoformat(), "period_end": p_end.isoformat()},
                          idempotency_key=f"recon:{account.id}:{p_start}:{p_end}", trigger="pipeline",
                          parent_job_id=job.id, run_id=job.run_id, priority=80)
    _check_missing_statement(db, account)
    notify(db, tenant_id=job.tenant_id, type="sync_success", severity="success",
           title=f"{account.nickname} synchronised" + (" (demo data)" if provider.is_demo else ""),
           body=", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in result.items() if isinstance(v, int)),
           link=f"/accounts/{account.id}", entity_type="bank_account", entity_id=account.id,
           dedupe_key=f"sync:{account.id}:{today}")
    ctx.progress(100, "Collection complete")
    return result


def _check_missing_statement(db: Session, account: BankAccount) -> None:
    today = datetime.now(UTC).date()
    expected_end = date(today.year, today.month, 1) - timedelta(days=1)  # last month end
    if account.statement_period != "monthly" or today.day < 5:
        return
    if account.last_statement_date is None or account.last_statement_date < expected_end:
        notify(db, tenant_id=account.tenant_id, type="statement_missing", severity="warning",
               title=f"Statement missing for {account.nickname}",
               body=f"No statement received covering the period ending {expected_end:%d %b %Y}.",
               link=f"/statements?account_id={account.id}", entity_type="bank_account", entity_id=account.id,
               dedupe_key=f"missing:{account.id}:{expected_end}", dedupe_hours=24 * 7)


def parse_statement(ctx: JobContext) -> dict[str, Any]:
    db, job = ctx.db, ctx.job
    st_id = uuid.UUID(job.payload["statement_id"])
    st = db.scalar(select(Statement).where(Statement.id == st_id, Statement.tenant_id == job.tenant_id))
    if st is None:
        raise PermanentJobError("Statement not found")
    ctx.progress(10, "Extracting statement")
    pj = process_statement(db, st_id, job_id=job.id)
    db.refresh(st)
    result = {"statement_id": str(st_id), "status": st.processing_status, "rows": pj.rows_extracted,
              "inserted": pj.rows_inserted, "duplicates": pj.rows_duplicate}
    settings = settings_for(db, job.tenant_id)
    if settings.auto_reconcile and st.processing_status in ("processed", "processed_with_warnings", "needs_review") \
            and st.period_start and st.period_end:
        queue.enqueue(db, tenant_id=job.tenant_id, job_type="reconcile_account", account_id=st.account_id,
                      payload={"statement_id": str(st.id)}, idempotency_key=f"recon:stmt:{st.id}",
                      trigger="pipeline", parent_job_id=job.id, run_id=job.run_id, priority=80)
    if pj.status == "failed":
        result["error"] = pj.error
    return result


def reconcile_account(ctx: JobContext) -> dict[str, Any]:
    db, job = ctx.db, ctx.job
    p = job.payload
    ctx.progress(10, "Running reconciliation")
    s = run_reconciliation(
        db, tenant_id=job.tenant_id, account_id=job.account_id,
        statement_id=uuid.UUID(p["statement_id"]) if p.get("statement_id") else None,
        period_start=date.fromisoformat(p["period_start"]) if p.get("period_start") else None,
        period_end=date.fromisoformat(p["period_end"]) if p.get("period_end") else None,
        trigger="automation", job_id=job.id, user_id=job.created_by)
    return {"session_id": str(s.id), "status": s.status, "matched": s.matched_count,
            "exceptions": s.exception_count}


def daily_report(ctx: JobContext) -> dict[str, Any]:
    from app.reports.service import generate_report

    db, job = ctx.db, ctx.job
    if job.run_id:
        pending = db.scalar(select(func.count(AutomationJob.id)).where(
            AutomationJob.run_id == job.run_id, AutomationJob.id != job.id,
            AutomationJob.status.in_(("queued", "running", "retry_scheduled"))))
        started = job.created_at or datetime.now(UTC)
        if pending and datetime.now(UTC) - started < timedelta(hours=2):
            raise Deferred(60, f"{pending} collection job(s) still in progress")
    report_date = date.fromisoformat(job.payload.get("date")) if job.payload.get("date") else \
        datetime.now(UTC).date()
    rep = generate_report(db, tenant_id=job.tenant_id, report_type="daily", file_format="xlsx",
                          params={"date": report_date.isoformat()}, user_id=None, trigger="automation")
    failed = db.scalar(select(func.count(AutomationJob.id)).where(AutomationJob.run_id == job.run_id,
                                                                  AutomationJob.status == "failed")) if job.run_id else 0
    notify(db, tenant_id=job.tenant_id, type="report_ready", severity="info",
           title=f"Daily banking report ready — {report_date:%d %b %Y}",
           body=f"Automation run finished with {failed or 0} failed job(s).", link="/reports",
           entity_type="report", entity_id=rep.id)
    return {"report_id": str(rep.id), "failed_jobs_in_run": failed or 0}


HANDLERS: dict[str, Callable[[JobContext], dict[str, Any]]] = {
    "collect_account": collect_account,
    "parse_statement": parse_statement,
    "reconcile_account": reconcile_account,
    "daily_report": daily_report,
}

from app.automation.browser_jobs import open_bank_login  # noqa: E402
HANDLERS["open_bank_login"] = open_bank_login
