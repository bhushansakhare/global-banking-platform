"""Per-account balance reports and warnings; bank secrets and statement files are never emailed."""
import smtplib
import ssl
import uuid
from email.message import EmailMessage

from sqlalchemy import select

from app.core.config import get_settings
from app.integrations.base.adapter import ConfigurationMissing, ProviderUnavailable
from app.models.banking import AccountBalance, BankAccount
from app.services.monitoring import mail_ready, settings_for


def send_balance_email(ctx):
    db, job = ctx.db, ctx.job
    account = db.scalar(select(BankAccount).where(BankAccount.id == job.account_id,
        BankAccount.tenant_id == job.tenant_id, BankAccount.deleted_at.is_(None)))
    if account is None or account.is_demo or account.low_balance_threshold is None:
        return {"skipped": "Monitoring unavailable for this account"}
    monitoring = settings_for(db, account)
    if not monitoring.email_enabled:
        return {"skipped": "Email alerts disabled"}
    balance = db.scalar(select(AccountBalance).where(AccountBalance.id == uuid.UUID(job.payload["balance_id"]),
        AccountBalance.tenant_id == job.tenant_id, AccountBalance.account_id == account.id))
    if balance is None or balance.currency != account.currency:
        return {"skipped": "Balance unavailable for this account"}
    below_target = balance.amount < account.low_balance_threshold
    # Preserve the promise of previously queued warning-only jobs after this update.
    if job.job_type == "low_balance_email" and not below_target:
        return {"skipped": "Balance is no longer below the target"}
    from app.balances.service import alert_balance
    selected = alert_balance(db, account)
    if selected is None or selected.id != balance.id:
        return {"skipped": "A newer balance supersedes this alert"}
    recipients = list(dict.fromkeys(str(r) for r in monitoring.recipients))
    if not recipients:
        raise ConfigurationMissing("No account-specific email recipient is configured")
    if not mail_ready():
        raise ConfigurationMissing("Configure SMTP_HOST and SMTP_FROM to deliver balance alerts")
    ctx.progress(30, "Sending balance warning email" if below_target else "Sending balance report email")
    settings = get_settings()
    message = EmailMessage()
    message["Subject"] = "Bank account balance below target" if below_target else "Bank account balance report: target met"
    message["From"] = settings.smtp_from
    message["To"] = "undisclosed-recipients:;"
    message["Message-ID"] = f"<balance-alert-{job.id}@local.banking>"
    message.set_content(f"Account: {account.nickname} ({account.account_number_masked})\n"
        f"Status: {'BELOW TARGET - attention required' if below_target else 'TARGET MET'}\n"
        f"Balance type: {balance.balance_type.replace('_', ' ')}\n"
        f"Balance: {balance.amount:,.2f} {balance.currency}\n"
        f"Target: {account.low_balance_threshold:,.2f} {account.currency}\n"
        f"Balance as of: {balance.as_of.isoformat()}\n"
        "A statement closing balance describes its statement date, not a live balance.\n"
        "Open the account in your banking software to review the source.\n")
    try:
        transport = smtplib.SMTP_SSL if settings.smtp_security == "ssl" else smtplib.SMTP
        kwargs = {"context": ssl.create_default_context()} if settings.smtp_security == "ssl" else {}
        with transport(settings.smtp_host, settings.smtp_port, timeout=30, **kwargs) as smtp:
            if settings.smtp_security == "starttls":
                smtp.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            refused = smtp.send_message(message, to_addrs=recipients)
            if refused:
                raise ConfigurationMissing("Some recipients were refused; review delivery before retrying")
    except (smtplib.SMTPException, OSError):
        raise ProviderUnavailable("SMTP delivery failed; check the mail server configuration") from None
    return {"email_sent": True, "recipient_count": len(recipients), "balance_as_of": balance.as_of.isoformat(),
            "balance_status": "below_target" if below_target else "target_met"}
