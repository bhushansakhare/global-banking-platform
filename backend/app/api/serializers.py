"""Model → JSON-safe dict. Never exposes secrets or full account numbers."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.models import (AccountBalance, AuditLog, AutomationJob, AutomationJobLog, AutomationSettings, BankAccount,
                        BankConnection, BankProvider, BookEntry, Institution, Notification, ReconciliationException,
                        ReconciliationMatch, ReconciliationSession, Report, Statement, Transaction, User)


def money(v: Decimal | None) -> str | None:
    return None if v is None else f"{v:.2f}"


def user(u: User) -> dict[str, Any]:
    return {"id": str(u.id), "email": u.email, "full_name": u.full_name, "role": u.role.code,
            "role_name": u.role.name, "is_active": u.is_active, "last_login_at": u.last_login_at,
            "created_at": u.created_at, "permissions": sorted(p.code for p in u.role.permissions)}


def provider(p: BankProvider) -> dict[str, Any]:
    return {"id": str(p.id), "code": p.code, "name": p.name, "provider_type": p.provider_type,
            "countries": p.countries, "currencies": p.currencies, "capabilities": p.capabilities,
            "auth_method": p.auth_method, "consent_required": p.consent_required,
            "refresh_frequency": p.refresh_frequency, "integration_status": p.integration_status,
            "is_demo": p.is_demo, "documentation_url": p.documentation_url, "notes": p.notes}


def institution(i: Institution) -> dict[str, Any]:
    return {"id": str(i.id), "name": i.name, "country_code": i.country_code, "bic": i.bic, "logo_url": i.logo_url,
            "external_id": i.external_id, "supported_currencies": i.supported_currencies,
            "supports_balances": i.supports_balances, "supports_transactions": i.supports_transactions,
            "supports_statements": i.supports_statements, "is_demo": i.is_demo, "is_custom": i.tenant_id is not None,
            "provider": {"code": i.provider.code, "name": i.provider.name,
                         "integration_status": i.provider.integration_status}}


def connection(c: BankConnection | None) -> dict[str, Any] | None:
    if c is None:
        return None
    return {"id": str(c.id), "status": c.status, "provider_code": c.provider.code, "provider_name": c.provider.name,
            "integration_status": c.provider.integration_status, "is_demo": c.is_demo,
            "last_checked_at": c.last_checked_at, "last_success_at": c.last_success_at, "last_error": c.last_error,
            "consecutive_failures": c.consecutive_failures, "has_credentials": bool(c.encrypted_credentials),
            "institution": c.institution.name, "connection_method": c.connection_method,
            "environment": c.environment, "provider_type": c.provider.provider_type}


def account(a: BankAccount, balances: list[AccountBalance] | None = None,
            automation: dict[str, Any] | None = None) -> dict[str, Any]:
    bal = {b.balance_type: balance(b) for b in balances or []}
    return {"id": str(a.id), "nickname": a.nickname, "holder_name": a.holder_name, "account_type": a.account_type,
            "currency": a.currency, "country_code": a.country_code, "account_number_masked": a.account_number_masked,
            "institution": {"id": str(a.institution.id), "name": a.institution.name,
                            "country_code": a.institution.country_code},
            "connection": connection(a.connection), "automation_enabled": a.automation_enabled,
            "low_balance_threshold": money(a.low_balance_threshold), "statement_period": a.statement_period,
            "last_sync_at": a.last_sync_at, "last_statement_date": a.last_statement_date,
            "connection_health": a.connection_health, "is_demo": a.is_demo, "notes": a.notes,
            "created_at": a.created_at, "balances": bal, "automation_paused": a.automation_paused,
            "sync_schedule": a.sync_schedule, "timezone": a.timezone,
            "last_attempted_sync_at": a.last_attempted_sync_at, "last_successful_sync_at": a.last_successful_sync_at,
            "last_sync_error": a.last_sync_error, "last_sync_result": a.last_sync_result,
            "automation": automation}


def balance(b: AccountBalance) -> dict[str, Any]:
    return {"id": str(b.id), "account_id": str(b.account_id), "balance_type": b.balance_type,
            "amount": money(b.amount), "currency": b.currency, "as_of": b.as_of, "source": b.source,
            "source_ref": b.source_ref, "is_demo": b.is_demo, "captured_at": b.captured_at,
            "verification_status": b.verification_status}


def statement(s: Statement, account_name: str | None = None) -> dict[str, Any]:
    from app.statements.access import password_required
    return {"id": str(s.id), "account_id": str(s.account_id), "account_name": account_name, "source": s.source,
            "period_start": s.period_start, "period_end": s.period_end, "opening_balance": money(s.opening_balance),
            "closing_balance": money(s.closing_balance), "total_credits": money(s.total_credits),
            "total_debits": money(s.total_debits), "transaction_count": s.transaction_count, "currency": s.currency,
            "download_status": s.download_status, "processing_status": s.processing_status,
            "arithmetic_valid": s.arithmetic_valid, "arithmetic_difference": money(s.arithmetic_difference),
            "parser_name": s.parser_name, "parse_confidence": str(s.parse_confidence) if s.parse_confidence else None,
            "warnings": s.warnings, "password_required": password_required(s),
            "duplicate_of_id": str(s.duplicate_of_id) if s.duplicate_of_id else None,
            "downloaded_at": s.downloaded_at, "created_at": s.created_at, "is_demo": s.is_demo,
            "file": {"original_filename": s.file.original_filename, "format": s.file.detected_format,
                     "size_bytes": s.file.size_bytes, "sha256": s.file.sha256, "scanned": s.file.is_scanned_pdf,
                     "page_count": s.file.page_count} if s.file else None}


def transaction(t: Transaction, account_name: str | None = None, bank_name: str | None = None) -> dict[str, Any]:
    return {"id": str(t.id), "account_id": str(t.account_id), "account_name": account_name, "bank_name": bank_name,
            "statement_id": str(t.statement_id) if t.statement_id else None, "source": t.source,
            "booking_date": t.booking_date, "value_date": t.value_date, "description": t.description,
            "reference": t.reference, "debit": money(t.debit), "credit": money(t.credit), "amount": money(t.amount),
            "currency": t.currency, "running_balance": money(t.running_balance),
            "category": {"id": str(t.category.id), "name": t.category.name, "colour": t.category.colour}
            if t.category else None, "reconciliation_status": t.reconciliation_status,
            "duplicate_suspect": t.duplicate_suspect, "is_demo": t.is_demo}


def book_entry(b: BookEntry) -> dict[str, Any]:
    return {"id": str(b.id), "account_id": str(b.account_id), "entry_date": b.entry_date,
            "description": b.description, "reference": b.reference, "amount": money(b.amount), "currency": b.currency,
            "source": b.source, "reconciliation_status": b.reconciliation_status, "is_demo": b.is_demo,
            "notes": b.notes}


def recon_session(s: ReconciliationSession, account_name: str | None = None) -> dict[str, Any]:
    return {"id": str(s.id), "account_id": str(s.account_id), "account_name": account_name,
            "statement_id": str(s.statement_id) if s.statement_id else None, "period_start": s.period_start,
            "period_end": s.period_end, "status": s.status, "trigger": s.trigger, "rules": s.rules,
            "engine_version": s.engine_version, "bank_transaction_count": s.bank_transaction_count,
            "book_entry_count": s.book_entry_count, "matched_count": s.matched_count,
            "suggested_count": s.suggested_count, "unmatched_bank_count": s.unmatched_bank_count,
            "unmatched_book_count": s.unmatched_book_count, "exception_count": s.exception_count,
            "opening_balance": money(s.opening_balance), "closing_balance": money(s.closing_balance),
            "expected_closing_balance": money(s.expected_closing_balance),
            "balance_difference": money(s.balance_difference),
            "match_rate": str(s.match_rate) if s.match_rate is not None else None, "created_at": s.created_at,
            "completed_at": s.completed_at, "summary": s.summary}


def recon_match(m: ReconciliationMatch, txn: Transaction | None, be: BookEntry | None) -> dict[str, Any]:
    return {"id": str(m.id), "match_type": m.match_type, "status": m.status, "original_status": m.original_status,
            "score": str(m.score), "amount_difference": money(m.amount_difference),
            "date_difference_days": m.date_difference_days, "reasons": m.reasons, "decided_at": m.decided_at,
            "decision_note": m.decision_note, "transaction": transaction(txn) if txn else None,
            "book_entry": book_entry(be) if be else None}


def recon_exception(e: ReconciliationException) -> dict[str, Any]:
    return {"id": str(e.id), "exception_type": e.exception_type, "severity": e.severity, "message": e.message,
            "amount": money(e.amount), "details": e.details, "status": e.status,
            "transaction_id": str(e.transaction_id) if e.transaction_id else None,
            "book_entry_id": str(e.book_entry_id) if e.book_entry_id else None, "resolved_at": e.resolved_at,
            "resolution_note": e.resolution_note}


def automation_settings(s: AutomationSettings) -> dict[str, Any]:
    return {"master_enabled": s.master_enabled, "paused": s.paused, "schedule_type": s.schedule_type,
            "daily_time": s.daily_time, "cron_expression": s.cron_expression, "timezone": s.timezone,
            "lookback_days": s.lookback_days, "auto_reconcile": s.auto_reconcile,
            "auto_generate_daily_report": s.auto_generate_daily_report, "max_attempts": s.max_attempts,
            "retry_base_seconds": s.retry_base_seconds, "next_run_at": s.next_run_at, "last_run_at": s.last_run_at}


def job(j: AutomationJob, account_name: str | None = None) -> dict[str, Any]:
    return {"id": str(j.id), "job_type": j.job_type, "status": j.status, "trigger": j.trigger,
            "account_id": str(j.account_id) if j.account_id else None, "account_name": account_name,
            "run_id": str(j.run_id) if j.run_id else None,
            "parent_job_id": str(j.parent_job_id) if j.parent_job_id else None, "attempts": j.attempts,
            "max_attempts": j.max_attempts, "run_after": j.run_after, "started_at": j.started_at,
            "finished_at": j.finished_at, "progress": j.progress, "progress_message": j.progress_message,
            "error_code": j.error_code, "error_message": j.error_message,
            "requires_user_action": j.requires_user_action, "result": j.result, "created_at": j.created_at,
            "duration_seconds": (j.finished_at - j.started_at).total_seconds()
            if j.finished_at and j.started_at else None}


def job_log(entry: AutomationJobLog) -> dict[str, Any]:
    return {"id": str(entry.id), "ts": entry.ts, "level": entry.level, "message": entry.message,
            "context": entry.context}


def notification(n: Notification) -> dict[str, Any]:
    return {"id": str(n.id), "type": n.type, "severity": n.severity, "title": n.title, "body": n.body,
            "link": n.link, "read_at": n.read_at, "created_at": n.created_at}


def audit_log(a: AuditLog) -> dict[str, Any]:
    return {"id": str(a.id), "ts": a.ts, "actor_user_id": str(a.actor_user_id) if a.actor_user_id else None,
            "actor_type": a.actor_type, "actor_label": a.actor_label, "action": a.action,
            "entity_type": a.entity_type, "entity_id": a.entity_id, "outcome": a.outcome, "before": a.before,
            "after": a.after, "meta": a.meta, "ip_address": str(a.ip_address) if a.ip_address else None}


def report(r: Report) -> dict[str, Any]:
    return {"id": str(r.id), "report_type": r.report_type, "title": r.title, "file_format": r.file_format,
            "parameters": r.parameters, "size_bytes": r.size_bytes, "status": r.status, "trigger": r.trigger,
            "contains_demo_data": r.contains_demo_data, "created_at": r.created_at}


def page(items: list[Any], total: int, page_no: int, size: int) -> dict[str, Any]:
    return {"items": items, "total": total, "page": page_no, "page_size": size,
            "pages": (total + size - 1) // size if size else 0}
