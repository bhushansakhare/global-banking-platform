"""Browser collection uses the existing durable queue, parser, snapshots and reconciliation jobs."""
import time
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, text

from app.automation import queue
from app.core.crypto import decrypt_json
from app.database.session import get_engine
from app.integrations.base.adapter import ConfigurationMissing, UserActionRequired
from app.integrations.banks.browser_engine import retrieve, validate_download
from app.integrations.banks.profiles import find_profile
from app.models.banking import BankAccount
from app.statements.pipeline import register_statement


def saved_login_profile(db, account):
    """Return only profile metadata, never secret values, for an explicitly saved browser login."""
    if account.is_demo or not account.browser_identifier_encrypted or not account.connection \
            or account.connection.status == "disconnected" \
            or account.connection.provider.code not in ("manual_upload", "browser_automation"):
        return None
    credentials = decrypt_json(account.browser_identifier_encrypted, str(account.tenant_id))
    if not credentials.get("customer_id") or not credentials.get("password") or not credentials.get("profile_key"):
        return None
    try:
        profile = find_profile(db, account.tenant_id, credentials["profile_key"])
    except StopIteration:
        return None
    if credentials.get("scope") != profile.credential_scope or not profile.public()["capabilities"]["saved_login"]:
        return None
    return profile


def enqueue_saved_login(db, account, user_id, *, run_id=None):
    """Manual Run now reuses saved login even while the legacy connection is statement upload."""
    from app.core.config import get_settings
    from app.core.errors import Conflict
    from app.models.automation import AutomationJob
    profile = saved_login_profile(db, account)
    if profile is None:
        return None
    if not get_settings().browser_automation_enabled:
        raise Conflict("Enable the local visible bank browser before running saved authentication", code="browser_disabled")
    db.execute(select(BankAccount.id).where(BankAccount.id == account.id).with_for_update())
    active = db.scalar(select(AutomationJob).where(AutomationJob.tenant_id == account.tenant_id,
        AutomationJob.account_id == account.id, AutomationJob.job_type.in_(["collect_account", "open_bank_login"]),
        AutomationJob.status.in_(queue.ACTIVE_STATES)).limit(1))
    if active:
        if active.status == "awaiting_user" and active.payload.get("profile_key") == profile.key:
            active.payload = {**active.payload, "authenticate": True, "interactive_approved": True}
            active.status, active.run_after, active.requires_user_action = "queued", queue.now(), None
            active.error_code = active.error_message = None
        return active, False
    # Keep using the login handler until collection has a genuine browser connection/profile binding.
    collect = profile.retrieval_supported and account.connection.provider.code == "browser_automation" \
        and account.browser_profile_key == profile.key
    return queue.enqueue(db, tenant_id=account.tenant_id, account_id=account.id,
        job_type="collect_account" if collect else "open_bank_login", trigger="manual", run_id=run_id,
        payload={"profile_key": profile.key, "authenticate": True}, max_attempts=1, created_by=user_id)


def browser_collect(ctx, *, login_only=False):
    from app.automation.handlers import Deferred, PermanentJobError
    db, job = ctx.db, ctx.job
    account = db.scalar(select(BankAccount).where(BankAccount.id == job.account_id,
                        BankAccount.tenant_id == job.tenant_id, BankAccount.deleted_at.is_(None)))
    if account is None:
        raise PermanentJobError("Account no longer exists")
    if not login_only and job.trigger == "schedule" and not job.payload.get("interactive_approved"):
        # Ephemeral sessions deliberately never retain banking cookies or credentials.
        # A scheduled job requests interaction once and stays awaiting_user until explicitly resumed.
        raise UserActionRequired("Open the bank browser to complete authentication before this scheduled sync.",
                                 action="reauth")
    key = job.payload.get("profile_key") if login_only else account.browser_profile_key
    try:
        profile = find_profile(db, job.tenant_id, key)
    except StopIteration:
        raise ConfigurationMissing("Bank browser profile was removed; select a reviewed profile") from None
    if not login_only and not profile.retrieval_supported:
        raise ConfigurationMissing("Bank browser profile requires review before statement retrieval")
    creds = decrypt_json(account.browser_identifier_encrypted, str(job.tenant_id)) \
        if account.browser_identifier_encrypted else {}
    if creds.get("scope") != profile.credential_scope:
        creds = {"customer_id": creds.get("customer_id", "")} if "scope" not in creds else {}
    use_saved_password = bool(job.payload.get("authenticate") or job.payload.get("interactive_approved")
                              or (not login_only and job.trigger == "manual"))
    today = datetime.now(UTC).date()
    start = date.fromisoformat(job.payload["date_from"]) if job.payload.get("date_from") else today - timedelta(days=30)
    end = date.fromisoformat(job.payload["date_to"]) if job.payload.get("date_to") else today
    # A single visible bank browser per desktop, even across worker processes/accounts.
    with get_engine().connect() as lock:
        if not lock.execute(text("SELECT pg_try_advisory_lock(1748123901)")).scalar():
            lock.commit()
            raise Deferred(15, "Another bank browser is open; waiting for it to close")
        lock.commit()
        try:
            last_pulse = [0.0]
            def progress(pct, message):
                if time.monotonic() - last_pulse[0] >= 3:
                    ctx.progress(pct, message)
                    db.commit()  # publish heartbeat/cancel checks during interactive authentication
                    last_pulse[0] = time.monotonic()
            def review(evidence):
                job.result = {"data_retrieved": False, "navigation_review": evidence}
                db.commit()
            if not login_only:
                account.last_attempted_sync_at = datetime.now(UTC)
                db.commit()
            files = retrieve(profile, customer_id=creds.get("customer_id", ""),
                             password=creds.get("password", "") if use_saved_password else "",
                             on_review=review if login_only else None,
                             masked_number=account.account_number_masked, date_from=start, date_to=end,
                             progress=progress, login_only=login_only)
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(1748123901)"))
            lock.commit()
    if login_only:
        return {**job.result, "data_retrieved": False, "profile_key": profile.key,
                "message": "Official login opened; this is not a bank sync or verified connection"}
    if not files:
        raise ConfigurationMissing("Bank did not deliver a statement; no successful sync recorded")
    added = duplicates = 0
    for name, content in files:
        validate_download(content, name, profile.formats)
        st, duplicate = register_statement(db, account=account, content=content, filename=name,
                                            source="browser_download")
        duplicates += int(duplicate)
        if not duplicate:
            added += 1
            queue.enqueue(db, tenant_id=job.tenant_id, job_type="parse_statement", account_id=account.id,
                          payload={"statement_id": str(st.id)}, idempotency_key=f"parse:{st.id}",
                          trigger="pipeline", parent_job_id=job.id, priority=50)
    # Parsing records statement balances; never invent a live available/ledger balance here.
    now = datetime.now(UTC)
    account.last_sync_at = account.last_successful_sync_at = now
    account.last_sync_error = None
    account.connection_health = "healthy"
    account.connection.status = "requires_auth"  # browser cookies are destroyed; next run needs user login
    account.connection.last_success_at = now
    account.connection.last_error = None
    account.connection.consecutive_failures = 0
    result = {"data_retrieved": True, "statements_new": added, "statements_duplicate": duplicates,
              "profile_key": profile.key, "source": "browser_download", "parsing": "queued",
              "next_login": "user_action_required"}
    account.last_sync_result = result
    return result


def open_bank_login(ctx):
    return browser_collect(ctx, login_only=True)
