"""Per-account automation state and connection-health policy.

Health policy (documented in docs/AUTOMATION_ENGINE.md):
* ``healthy``  — the last collection SUCCEEDED against the live provider (data endpoints answered),
                 within ``HEALTHY_WINDOW`` and with no newer failure.
* ``degraded`` — 1–2 consecutive failures, or a retry is scheduled, or the last success is stale.
* ``failing``  — 3+ consecutive failures.
* ``unknown``  — never verified (e.g. just created, awaiting authorisation).
* ``not_applicable`` — manual-import-only account: there is no live connection to be healthy.
An account record existing is never enough to be healthy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.integrations.registry import get_adapter
from app.models.automation import AutomationJob, AutomationSettings
from app.models.banking import BankAccount

HEALTHY_WINDOW = timedelta(hours=48)

STATES = ("not_configured", "provider_setup_required", "authorization_required", "ready", "scheduled", "running",
          "waiting_for_user", "paused", "failed", "degraded", "healthy")

MANUAL_METHOD = "manual_statement_import"


@dataclass
class AutomationView:
    status: str
    reason: str
    next_sync_at: datetime | None
    retry: dict[str, Any] | None
    action: str | None  # connect|authorize|provider_setup|resolve|enable|none

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reason": self.reason, "next_sync_at": self.next_sync_at,
                "retry": self.retry, "action_required": self.action}


def views_for(db, tenant_id, accounts: list[BankAccount]) -> dict[Any, dict[str, Any]]:
    """Automation view for many accounts with two queries (settings + latest collect job per account)."""
    from sqlalchemy import select

    if not accounts:
        return {}
    settings = db.scalar(select(AutomationSettings).where(AutomationSettings.tenant_id == tenant_id))
    ids = [a.id for a in accounts]
    latest: dict[Any, AutomationJob] = {}
    for j in db.scalars(select(AutomationJob).where(AutomationJob.tenant_id == tenant_id,
                                                    AutomationJob.account_id.in_(ids),
                                                    AutomationJob.job_type.in_(["collect_account", "open_bank_login"]))
                        .order_by(AutomationJob.created_at.desc()).limit(50 * len(ids))):
        latest.setdefault(j.account_id, j)
    result = {a.id: compute_state(a, settings, latest.get(a.id)).as_dict() for a in accounts}
    from app.automation.browser_jobs import saved_login_profile
    for account in accounts:
        if not is_manual(account) or not saved_login_profile(db, account):
            continue
        from app.core.config import get_settings
        browser_enabled = get_settings().browser_automation_enabled
        job = latest.get(account.id)
        if not browser_enabled:
            view = AutomationView("provider_setup_required", "The local bank browser is disabled. Start the browser-enabled backend before running authentication.", None, None, "connect")
        elif job and job.status == "failed" and job.error_code in ("provider_not_configured", "browser_disabled"):
            view = AutomationView("ready", "The visible bank browser is now enabled. Run now starts a fresh login using your saved details.", None, None, None)
        elif job and job.status in ("queued", "running", "retry_scheduled"):
            view = AutomationView("running", job.progress_message or "Opening the visible bank browser", None, None, None)
        elif job and job.status == "awaiting_user":
            view = AutomationView("waiting_for_user", job.error_message or "Complete verification on the bank page", None, None, "authorize")
        elif job and job.status == "failed":
            view = AutomationView("failed", job.error_message or "Bank login failed; check the connection", None, None, "resolve")
        else:
            view = AutomationView("ready", "Saved bank login is ready. Run now opens the visible browser; statement retrieval depends on the reviewed bank profile.", None, None, None)
        result[account.id] = view.as_dict()
    return result


def is_manual(account: BankAccount) -> bool:
    c = account.connection
    return c is None or c.provider.code == "manual_upload" or c.connection_method == MANUAL_METHOD


def compute_health(account: BankAccount, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    if is_manual(account):
        return "not_applicable"
    c = account.connection
    failures = (c.consecutive_failures or 0) if c else 0
    if failures >= 3:
        return "failing"
    if failures:
        return "degraded"
    if account.last_successful_sync_at is None:
        return "unknown"
    if now - account.last_successful_sync_at > HEALTHY_WINDOW:
        return "degraded"
    return "healthy"


def compute_state(account: BankAccount, settings: AutomationSettings | None, latest_job: AutomationJob | None,
                  now: datetime | None = None) -> AutomationView:
    now = now or datetime.now(UTC)
    c = account.connection
    next_at = account.next_sync_at if account.sync_schedule else (settings.next_run_at if settings else None)

    if c is None or c.status == "disconnected":
        return AutomationView("not_configured", "No bank connection. Connect the bank to enable automation.",
                              None, None, "connect")
    if is_manual(account):
        return AutomationView("not_configured",
                              "Manual statement import only — nothing is collected automatically. Connect the bank "
                              "through a supported method to automate it.", None, None, "connect")
    try:
        adapter = get_adapter(c.provider.code)
        configured = adapter.is_configured()
    except Exception:  # noqa: BLE001
        configured = False
    if not configured:
        return AutomationView("provider_setup_required",
                              f"{c.provider.name} needs platform credentials before this account can connect.",
                              None, None, "provider_setup")
    if latest_job is not None and latest_job.status == "awaiting_user":
        reauth = latest_job.requires_user_action in ("consent", "reauth")
        return AutomationView("waiting_for_user", latest_job.error_message or "Your action is required",
                              next_at, None, "authorize" if reauth else "resolve")
    if c.status in ("pending", "requires_auth", "consent_expired"):
        why = {"pending": "Authorisation has not been completed.",
               "requires_auth": "The bank/provider needs you to (re)authorise access.",
               "consent_expired": "Your consent has expired or was revoked — renew it to resume."}[c.status]
        return AutomationView("authorization_required", why, None, None, "authorize")

    if latest_job is not None:
        if latest_job.status == "running" or (latest_job.status == "queued" and latest_job.run_after <= now
                                              and latest_job.attempts == 0):
            return AutomationView("running", latest_job.progress_message or "Synchronisation in progress",
                                  next_at, None, None)
        if latest_job.status == "awaiting_user":
            return AutomationView("waiting_for_user", latest_job.error_message or "Your action is required",
                                  next_at, None, "resolve")
    retry = None
    if latest_job is not None and latest_job.status in ("retry_scheduled", "queued") and latest_job.attempts > 0:
        retry = {"attempt": latest_job.attempts, "max_attempts": latest_job.max_attempts,
                 "next_attempt_at": latest_job.run_after, "error": latest_job.error_message}
    elif latest_job is not None and latest_job.status == "queued" and latest_job.error_code == "data_not_ready":
        retry = {"attempt": latest_job.attempts, "max_attempts": latest_job.max_attempts,
                 "next_attempt_at": latest_job.run_after, "error": None}

    if not account.automation_enabled:
        return AutomationView("ready", "Connected and authorised. Enable automation to sync on schedule.",
                              None, retry, "enable")
    if account.automation_paused:
        return AutomationView("paused", "Automation is paused for this account.", None, retry, None)
    if settings is not None and (not settings.master_enabled or settings.paused):
        why = "Organisation-wide automation is switched off." if not settings.master_enabled else \
            "Organisation-wide automation is paused."
        return AutomationView("ready", why + " Turn it on in the Automation Center.",
                              None if not account.sync_schedule else next_at, retry, "enable")

    if latest_job is not None and latest_job.status == "failed":
        return AutomationView("failed", latest_job.error_message or "The last synchronisation failed",
                              next_at, retry, "resolve")
    health = compute_health(account, now)
    if retry or health in ("degraded", "failing"):
        reason = (retry or {}).get("error") or account.last_sync_error or "Recent synchronisations had problems"
        return AutomationView("failed" if health == "failing" else "degraded", reason, next_at, retry, None)
    if health == "healthy":
        return AutomationView("healthy", "Last synchronisation succeeded with live data.", next_at, None, None)
    return AutomationView("scheduled", "Waiting for the first scheduled synchronisation.", next_at, None, None)
