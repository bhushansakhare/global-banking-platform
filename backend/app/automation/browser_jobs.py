"""Browser collection uses the existing durable queue, parser, snapshots and reconciliation jobs."""
import time
import importlib
import re
from decimal import Decimal
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from app.automation import queue
from app.core.crypto import decrypt_json
from app.database.session import get_engine
from app.integrations.base.adapter import ConfigurationMissing, UserActionRequired
from app.integrations.banks.browser_engine import retrieve, validate_download
from app.integrations.banks.profiles import find_profile
from app.models.banking import BankAccount
from app.statements.pipeline import register_statement
from app.services.monitoring import settings_for as monitoring_for


def capture_visible_balance(db, account, result, *, job_id=None, as_of=None):
    """Read only the matched bank account selector's explicitly labelled available balance."""
    from app.balances.service import record_snapshot, check_low_balance
    suffix = re.sub(r"\D", "", account.account_number_masked)[-4:]
    for control in result.get("controls", []):
        if "bb-account-selector" not in control.get("parent_class", ""):
            continue
        text = control.get("text", "")
        account_text = re.split(r"\b(?:Available\s+)?Balance\b", text, maxsplit=1, flags=re.I)[0]
        compact = re.sub(r"\s+", "", account_text)
        if len(suffix) != 4 or not re.search(rf"{re.escape(suffix)}(?!\d)", compact) or "Available Balance" not in text:
            continue
        from app.integrations.banks.session_review import observed_available_amount
        amount = observed_available_amount(text)
        if amount is None or account.currency != "INR":
            continue
        recorded = record_snapshot(db, account=account, balance_type="available", amount=amount,
            currency=account.currency, as_of=as_of or datetime.now(UTC), source="browser",
            source_ref="HDFC Bank website", job_id=job_id, verification_status="bank_page_observed")
        check_low_balance(db, account)
        return recorded
    return False


def reviewed_statement_flow(db, account, profile):
    from app.models.system import SystemSetting
    row = db.scalar(select(SystemSetting).where(SystemSetting.tenant_id == account.tenant_id,
        SystemSetting.key == f"bank_statement_flow:{account.id}"))
    if row and row.value.get("verified") and row.value.get("profile_key") == profile.key:
        return row.value
    return None


def reset_statement_review(db, job):
    from app.models.system import SystemSetting
    command = db.scalar(select(SystemSetting).where(SystemSetting.tenant_id == job.tenant_id,
        SystemSetting.key == f"browser_review:{job.id}"))
    if command:
        command.value = {"status": "idle"}


def saved_login_profile(db, account):
    """Return only profile metadata, never secret values, for an explicitly saved browser login."""
    if account.is_demo or not account.connection \
            or account.connection.status == "disconnected" \
            or account.connection.provider.code not in ("manual_upload", "browser_automation"):
        return None
    if monitoring_for(db, account).bank_login_method == "qr" and account.browser_profile_key:
        try:
            profile = find_profile(db, account.tenant_id, account.browser_profile_key)
        except StopIteration:
            return None
        if profile.country == account.country_code and (not profile.currencies or account.currency in profile.currencies):
            return profile
        return None
    if not account.browser_identifier_encrypted:
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


def enqueue_saved_login(db, account, user_id, *, run_id=None, trigger="manual", idempotency_key=None):
    """Saved login; scheduled runs require an account-reviewed observation flow."""
    from app.core.config import get_settings
    from app.core.errors import Conflict
    from app.models.automation import AutomationJob
    profile = saved_login_profile(db, account)
    if profile is None:
        return None
    use_qr = monitoring_for(db, account).bank_login_method == "qr"
    capture_reviewed = bool(reviewed_statement_flow(db, account, profile))
    if trigger == "schedule" and not capture_reviewed:
        return None
    if not get_settings().browser_automation_enabled:
        raise Conflict("Enable the local visible bank browser before running saved authentication", code="browser_disabled")
    db.execute(select(BankAccount.id).where(BankAccount.id == account.id).with_for_update())
    active = db.scalar(select(AutomationJob).where(AutomationJob.tenant_id == account.tenant_id,
        AutomationJob.account_id == account.id, AutomationJob.job_type.in_(["collect_account", "open_bank_login"]),
        AutomationJob.status.in_(queue.ACTIVE_STATES)).limit(1))
    if active:
        if trigger == "manual" and active.status == "awaiting_user" and active.payload.get("profile_key") == profile.key:
            reset_statement_review(db, active)
            active.payload = {**{k: v for k, v in active.payload.items() if k != "review_steps"},
                              "authenticate": not use_qr, "interactive_approved": True,
                              "user_requested_browser": True, "qr_login": use_qr, "statement_review": capture_reviewed}
            active.result = {"data_retrieved": False}
            active.status, active.run_after, active.requires_user_action = "queued", queue.now(), None
            active.error_code = active.error_message = None
            active.trigger = "manual"
            if run_id is not None:
                active.run_id = run_id
        return active, False
    # Keep using the login handler until collection has a genuine browser connection/profile binding.
    collect = profile.retrieval_supported and account.connection.provider.code == "browser_automation" \
        and account.browser_profile_key == profile.key
    return queue.enqueue(db, tenant_id=account.tenant_id, account_id=account.id,
        job_type="collect_account" if collect else "open_bank_login", trigger=trigger, run_id=run_id,
        idempotency_key=idempotency_key,
        payload={"profile_key": profile.key, "authenticate": not use_qr, "qr_login": use_qr,
                 "user_requested_browser": trigger == "manual", "statement_review": capture_reviewed}, max_attempts=1, created_by=user_id)


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
    qr_login = bool(job.payload.get("qr_login"))
    creds = decrypt_json(account.browser_identifier_encrypted, str(job.tenant_id)) \
        if account.browser_identifier_encrypted and not qr_login else {}
    if creds.get("scope") != profile.credential_scope:
        creds = {"customer_id": creds.get("customer_id", "")} if "scope" not in creds else {}
    use_saved_password = not qr_login and bool(job.payload.get("authenticate") or job.payload.get("interactive_approved")
                                             or (not login_only and job.trigger == "manual"))
    monitoring = monitoring_for(db, account)
    screenshot = []
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
            capture_flow = reviewed_statement_flow(db, account, profile)
            if job.payload.get("statement_review") and job.payload.get("review_steps"):
                capture_flow = {"steps": job.payload["review_steps"][:12]}
            capture_step = [0]
            def session_control(page):
                from app.integrations.banks import session_review as review_module
                # Local, account-owner review mode permits updating its fixed read-only
                # helpers without destroying the already approved bank session.
                review_module = importlib.reload(review_module)
                from app.models.system import SystemSetting
                command = db.scalar(select(SystemSetting).where(SystemSetting.tenant_id == job.tenant_id,
                    SystemSetting.key == f"browser_review:{job.id}").execution_options(populate_existing=True))
                if capture_flow and capture_step[0] < len(capture_flow.get("steps", [])):
                    if command is None:
                        command = SystemSetting(tenant_id=job.tenant_id, key=f"browser_review:{job.id}")
                        db.add(command)
                    if command.value and command.value.get("status") == "failed":
                        raise UserActionRequired("Statement controls changed; review the visible bank page", action="statement_review")
                    if not command.value or command.value.get("status") != "pending":
                        step = dict(capture_flow["steps"][capture_step[0]])
                        if step.get("period") == "current_month":
                            step["label"] = datetime.now(ZoneInfo(account.timezone or "Asia/Kolkata")).strftime("%B %Y")
                        command.value = {**step, "status": "pending"}
                        db.commit()
                captured = review_module.run_command(db, job, page, profile, account.account_number_masked,
                                       screenshot.append if monitoring.capture_statement_screenshot else None)
                if command and command.value.get("status") == "succeeded" and command.value.get("action") in ("inspect", "capture_observation"):
                    if command.value.get("action") == "capture_observation":
                        from app.balances.service import record_snapshot, check_low_balance
                        observation = command.value["result"]["observation"]
                        record_snapshot(db, account=account, balance_type="available", amount=Decimal(observation["amount"]),
                            currency=observation["currency"], as_of=datetime.fromisoformat(observation["captured_at"]),
                            source="browser", source_ref="HDFC Bank website", job_id=job.id,
                            verification_status="bank_page_observed")
                        check_low_balance(db, account)
                    else:
                        capture_visible_balance(db, account, command.value.get("result", {}), job_id=job.id,
                                                as_of=command.updated_at)
                    db.commit()
                    if command.value.get("action") == "capture_observation":
                        from app.integrations.banks.browser_engine import BankObservationCaptured
                        job.result = {"data_retrieved": True, "observation_captured": True,
                                      **command.value.get("result", {}).get("observation", {})}
                        db.commit()
                        raise BankObservationCaptured()
                if capture_flow and command and command.value.get("status") == "succeeded":
                    capture_step[0] += 1
                return captured
            if not login_only:
                account.last_attempted_sync_at = datetime.now(UTC)
                db.commit()
            files = retrieve(profile, customer_id=creds.get("customer_id", ""),
                             password=creds.get("password", "") if use_saved_password else "",
                             on_review=review if login_only else None,
                             masked_number=account.account_number_masked, date_from=start, date_to=end,
                             progress=progress, login_only=login_only, qr_login=qr_login,
                             verification_wait_seconds=monitoring.verification_wait_seconds,
                             session_control=session_control if job.payload.get("statement_review") else None,
                             on_statement_screenshot=screenshot.append if monitoring.capture_statement_screenshot and not login_only else None)
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(1748123901)"))
            lock.commit()
    if login_only and job.result.get("observation_captured"):
        now = datetime.now(UTC)
        account.last_sync_at = account.last_successful_sync_at = now
        account.last_sync_error = None
        account.connection_health = "not_applicable" if account.connection.provider.code == "manual_upload" else "healthy"
        account.last_sync_result = {**job.result, "source": "bank_page_observed", "live_balance": True}
        return account.last_sync_result
    if login_only and not files:
        return {**job.result, "data_retrieved": False, "profile_key": profile.key,
                "message": "Official login opened; this is not a bank sync or verified connection"}
    if login_only and files and not job.payload.get("statement_review"):
        raise ConfigurationMissing("Statement capture was not requested for this login session")
    if not files:
        raise ConfigurationMissing("Bank did not deliver a statement; no successful sync recorded")
    added = duplicates = 0
    capture_only = login_only and bool(files)
    for name, content in files:
        validate_download(content, name, ["pdf", "csv", "xlsx", "xls"] if capture_only else profile.formats)
        st, duplicate = register_statement(db, account=account, content=content, filename=name,
                                            source="browser_download")
        if screenshot:
            from app.statements.storage import save_generated
            from app.models.system import SystemSetting
            path = save_generated("bank_evidence", account.tenant_id, f"{st.id}.png", screenshot[-1])
            key = f"statement_evidence:{st.id}"
            evidence = db.scalar(select(SystemSetting).where(SystemSetting.tenant_id == account.tenant_id,
                                                             SystemSetting.key == key))
            if evidence is None:
                evidence = SystemSetting(tenant_id=account.tenant_id, key=key)
                db.add(evidence)
            evidence.value = {"path": path, "captured_at": datetime.now(UTC).isoformat(), "source": "bank_statement_screen"}
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
    account.connection_health = "not_applicable" if capture_only and account.connection.provider.code == "manual_upload" else "healthy"
    if account.connection.provider.code == "browser_automation":
        account.connection.status = "requires_auth"  # browser cookies are destroyed; next run needs user login
    account.connection.last_success_at = now
    account.connection.last_error = None
    account.connection.consecutive_failures = 0
    result = {"data_retrieved": True, "statements_new": added, "statements_duplicate": duplicates,
              "profile_key": profile.key, "source": "browser_download", "parsing": "queued",
              "next_login": "user_action_required"}
    if capture_only:
        result.update(capture_only=True, live_balance=False)
    account.last_sync_result = result
    return result


def open_bank_login(ctx):
    return browser_collect(ctx, login_only=True)
