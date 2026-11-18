"""Statement processing pipeline.

receive → integrity → format detection → scanned check → extraction → normalisation →
balance identification → arithmetic validation → store transactions → balance snapshots →
hand-off to reconciliation (by the automation engine).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, time
from decimal import Decimal
from pathlib import PurePath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.balances.service import record_snapshot
from app.core.config import get_settings
from app.core.errors import ValidationFailed
from app.core.logging import get_logger
from app.models.banking import BankAccount
from app.models.statements import Statement, StatementFile, StatementParsingJob
from app.notifications.service import notify
from app.statements import storage
from app.statements.detect import detect_format
from app.statements.parsers import ParseInput, ParserUnavailable, parser_for
from app.transactions.ingest import IncomingTransaction, ingest

log = get_logger(__name__)
ALLOWED_FORMATS = {"pdf", "xlsx", "xls", "csv"}


def register_statement(db: Session, *, account: BankAccount, content: bytes, filename: str, source: str,
                       uploaded_by: uuid.UUID | None = None, external_statement_id: str | None = None,
                       period_start=None, period_end=None) -> tuple[Statement, bool]:
    """Store the original file and create a pending statement. Returns (statement, is_duplicate)."""
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    if not content:
        raise ValidationFailed("The statement file is empty", code="invalid_statement")
    if len(content) > max_bytes:
        raise ValidationFailed(f"File exceeds the {get_settings().max_upload_mb} MB limit", code="file_too_large")
    det = detect_format(content, filename)
    if det.format not in ALLOWED_FORMATS:
        raise ValidationFailed("Unsupported or unrecognised statement format. Supported: PDF, XLSX, XLS, CSV.",
                               code="invalid_statement")

    if external_statement_id:
        existing = db.scalar(select(Statement).where(Statement.account_id == account.id,
                                                     Statement.external_statement_id == external_statement_id,
                                                     Statement.deleted_at.is_(None)))
        if existing:
            return existing, True

    sha = storage.sha256_bytes(content)
    file = db.scalar(select(StatementFile).where(StatementFile.tenant_id == account.tenant_id,
                                                 StatementFile.sha256 == sha))
    if file is not None:
        dup = db.scalar(select(Statement).where(Statement.file_id == file.id, Statement.account_id == account.id,
                                                Statement.deleted_at.is_(None)))
        if dup is not None:
            return dup, True
    else:
        _, rel = storage.save_statement_file(account.tenant_id, content, det.format)
        safe_name = PurePath(filename or f"statement.{det.format}").name[:255]
        file = StatementFile(tenant_id=account.tenant_id, sha256=sha, original_filename=safe_name,
                             storage_path=rel, size_bytes=len(content), detected_format=det.format,
                             mime_type=det.mime, is_scanned_pdf=det.scanned_pdf, page_count=det.page_count)
        db.add(file)
        db.flush()

    st = Statement(tenant_id=account.tenant_id, account_id=account.id, file_id=file.id, source=source,
                   currency=account.currency, download_status="downloaded" if source != "upload" else "received",
                   processing_status="pending", uploaded_by=uploaded_by, external_statement_id=external_statement_id,
                   period_start=period_start, period_end=period_end, is_demo=account.is_demo,
                   downloaded_at=datetime.now(UTC))
    db.add(st)
    db.flush()
    return st, False


def process_statement(db: Session, statement_id: uuid.UUID, *, job_id: uuid.UUID | None = None,
                      password: str | None = None) -> StatementParsingJob:
    # serialise concurrent processing of the same statement
    db.execute(select(Statement.id).where(Statement.id == statement_id).with_for_update())
    st = db.get(Statement, statement_id)
    if st is None:
        raise ValidationFailed("Statement not found")
    account = db.get(BankAccount, st.account_id)
    assert account is not None and st.file is not None
    pj = StatementParsingJob(tenant_id=st.tenant_id, statement_id=st.id, automation_job_id=job_id,
                             status="running", started_at=datetime.now(UTC))
    db.add(pj)
    st.processing_status = "processing"
    db.flush()

    try:
        if not storage.verify_integrity(st.tenant_id, st.file.storage_path, st.file.sha256):
            raise ParserUnavailable("Stored file failed its integrity check", status="failed")
        content = storage.resolve("statements", st.tenant_id, st.file.storage_path).read_bytes()
        parser = parser_for(st.file.detected_format)
        pj.parser_name = st.parser_name = parser.name
        result = parser.parse(ParseInput(content=content, filename=st.file.original_filename,
                                         detected_format=st.file.detected_format, account_currency=account.currency,
                                         institution_external_id=account.institution.external_id,
                                         password=password))
    except ParserUnavailable as exc:
        st.processing_status = exc.status
        st.warnings = [{"code": exc.status, "message": str(exc)}]
        pj.status, pj.error, pj.finished_at = "failed", str(exc), datetime.now(UTC)
        notify(db, tenant_id=st.tenant_id, type="job_failed", severity="warning",
               title=f"Statement needs attention ({account.nickname})", body=str(exc),
               link=f"/statements/{st.id}", entity_type="statement", entity_id=st.id)
        db.flush()
        return pj

    if result.currency and result.currency != account.currency:
        result.warn("currency_mismatch", f"Statement currency {result.currency} differs from account currency "
                                         f"{account.currency}")

    items = [IncomingTransaction(booking_date=r.booking_date, value_date=r.value_date, description=r.description,
                                 reference=r.reference, debit=r.debit, credit=r.credit,
                                 currency=result.currency or account.currency, running_balance=r.running_balance,
                                 line_number=r.line_number, raw=r.raw) for r in result.rows]
    ing = ingest(db, account, items, source="demo" if account.is_demo and st.source == "demo" else "statement",
                 statement_id=st.id)

    # Statement balances (never relabelled as "current")
    st.period_start = st.period_start or result.period_start
    st.period_end = st.period_end or result.period_end
    st.opening_balance, st.closing_balance = result.opening_balance, result.closing_balance
    st.total_credits, st.total_debits = result.total_credits, result.total_debits
    st.transaction_count = len(result.rows)
    st.arithmetic_valid, st.arithmetic_difference = result.arithmetic_valid, result.arithmetic_difference
    st.parse_confidence = result.confidence
    st.warnings = result.warnings[:100]

    if st.period_start and st.opening_balance is not None:
        record_snapshot(db, account=account, balance_type="statement_opening", amount=st.opening_balance,
                        currency=st.currency, as_of=datetime.combine(st.period_start, time.min, UTC),
                        source="statement", source_ref=str(st.id), job_id=job_id)
    if st.period_end and st.closing_balance is not None:
        record_snapshot(db, account=account, balance_type="statement_closing", amount=st.closing_balance,
                        currency=st.currency, as_of=datetime.combine(st.period_end, time(23, 59, 59), UTC),
                        source="statement", source_ref=str(st.id), job_id=job_id)
        if not account.last_statement_date or st.period_end > account.last_statement_date:
            account.last_statement_date = st.period_end

    # overlapping-period duplicates (different file, same period)
    if st.period_start and st.period_end:
        overlap = db.scalar(select(Statement.id).where(
            Statement.account_id == st.account_id, Statement.id != st.id, Statement.deleted_at.is_(None),
            Statement.period_start == st.period_start, Statement.period_end == st.period_end,
            Statement.processing_status.in_(("processed", "processed_with_warnings", "needs_review"))).limit(1))
        if overlap:
            st.duplicate_of_id = overlap
            st.warnings = [*st.warnings, {"code": "duplicate_period",
                                          "message": "Another statement covers the same period"}]

    if not result.rows:
        st.processing_status = "needs_review"
    elif result.arithmetic_valid is False or result.confidence < Decimal("0.8"):
        st.processing_status = "needs_review"
    elif result.warnings:
        st.processing_status = "processed_with_warnings"
    else:
        st.processing_status = "processed"

    pj.status = "succeeded"
    pj.rows_extracted, pj.rows_inserted, pj.rows_duplicate = len(result.rows), ing.inserted, ing.duplicates
    pj.finished_at = datetime.now(UTC)
    pj.diagnostics = {"column_map": result.column_map, "date_format": result.date_format,
                      "unparsed_rows": result.unparsed_rows, "running_balance_breaks": result.running_balance_breaks,
                      "confidence": str(result.confidence), "duplicate_suspects": ing.suspects}
    if st.processing_status == "needs_review":
        notify(db, tenant_id=st.tenant_id, type="reconciliation_mismatch", severity="warning",
               title=f"Statement requires review ({account.nickname})",
               body="Extraction confidence is low or the balances do not reconcile arithmetically.",
               link=f"/statements/{st.id}", entity_type="statement", entity_id=st.id)
    db.flush()
    log.info("statement processed", extra={"ctx": {"statement": str(st.id), "status": st.processing_status,
                                                   "rows": len(result.rows), "inserted": ing.inserted}})
    return pj
