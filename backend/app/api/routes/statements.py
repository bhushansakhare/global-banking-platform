from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select

from app.api import serializers as S
from app.api.deps import DB, Principal, require
from app.audit.service import record as audit
from app.automation import queue
from app.core.config import get_settings
from app.core.errors import NotFound, ValidationFailed
from app.models.banking import BankAccount
from app.models.statements import Statement, StatementParsingJob
from app.models.transactions import Transaction
from app.services.banking import get_account
from app.statements import storage
from app.statements.pipeline import process_statement, register_statement

router = APIRouter(prefix="/statements", tags=["statements"])


def _get(db, tenant_id: uuid.UUID, statement_id: uuid.UUID) -> Statement:
    st = db.scalar(select(Statement).where(Statement.id == statement_id, Statement.tenant_id == tenant_id,
                                           Statement.deleted_at.is_(None)))
    if st is None:
        raise NotFound("Statement not found")
    return st


@router.get("")
def list_statements(db: DB, p: Principal = require("statements:read"), account_id: uuid.UUID | None = None,
                    status: str | None = None, source: str | None = None, date_from: date | None = None,
                    date_to: date | None = None, page: int = Query(1, ge=1),
                    page_size: int = Query(25, ge=1, le=200)) -> dict:
    q = select(Statement).where(Statement.tenant_id == p.tenant_id, Statement.deleted_at.is_(None))
    if account_id:
        q = q.where(Statement.account_id == account_id)
    if status:
        q = q.where(Statement.processing_status == status)
    if source:
        q = q.where(Statement.source == source)
    if date_from:
        q = q.where(Statement.period_end >= date_from)
    if date_to:
        q = q.where(Statement.period_start <= date_to)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(Statement.period_end.desc().nulls_last(), Statement.created_at.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    names = {a.id: a.nickname for a in db.scalars(select(BankAccount).where(BankAccount.tenant_id == p.tenant_id))}
    return S.page([S.statement(s, names.get(s.account_id)) for s in rows], total, page, page_size)


@router.post("/upload", status_code=201, summary="Upload an official statement export (PDF, XLSX, XLS, CSV)")
async def upload(db: DB, account_id: uuid.UUID = Form(...), file: UploadFile = File(...),
                 process_now: bool = Form(True), p: Principal = require("statements:write")) -> dict:
    acc = get_account(db, p.tenant_id, account_id)
    limit = get_settings().max_upload_mb * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise ValidationFailed(f"File exceeds the {get_settings().max_upload_mb} MB limit", code="file_too_large")
    st, duplicate = register_statement(db, account=acc, content=content, filename=file.filename or "statement",
                                       source="upload", uploaded_by=p.user_id)
    audit(db, action="statement.upload", tenant_id=p.tenant_id, actor_user_id=p.user_id, entity_type="statement",
          entity_id=st.id, meta={"filename": file.filename, "duplicate": duplicate, "size": len(content)}, ip=p.ip)
    if duplicate:
        return {"duplicate": True, "statement": S.statement(st, acc.nickname),
                "message": "This file has already been imported for this account."}
    if process_now:
        process_statement(db, st.id)
        db.refresh(st)
        if st.period_start and st.period_end:
            queue.enqueue(db, tenant_id=p.tenant_id, job_type="reconcile_account", account_id=acc.id,
                          payload={"statement_id": str(st.id)}, idempotency_key=f"recon:stmt:{st.id}",
                          trigger="pipeline", created_by=p.user_id, priority=80)
    return {"duplicate": False, "statement": S.statement(st, acc.nickname)}


@router.get("/{statement_id}")
def get_statement(statement_id: uuid.UUID, db: DB, p: Principal = require("statements:read")) -> dict:
    st = _get(db, p.tenant_id, statement_id)
    acc = db.get(BankAccount, st.account_id)
    jobs = db.scalars(select(StatementParsingJob).where(StatementParsingJob.statement_id == st.id)
                      .order_by(StatementParsingJob.created_at.desc())).all()
    out = S.statement(st, acc.nickname if acc else None)
    out["parsing_jobs"] = [{"id": str(j.id), "status": j.status, "parser_name": j.parser_name,
                            "rows_extracted": j.rows_extracted, "rows_inserted": j.rows_inserted,
                            "rows_duplicate": j.rows_duplicate, "started_at": j.started_at,
                            "finished_at": j.finished_at, "error": j.error, "diagnostics": j.diagnostics}
                           for j in jobs]
    out["institution"] = acc.institution.name if acc else None
    return out


@router.get("/{statement_id}/transactions")
def statement_transactions(statement_id: uuid.UUID, db: DB, p: Principal = require("transactions:read")
                           ) -> list[dict]:
    st = _get(db, p.tenant_id, statement_id)
    rows = db.scalars(select(Transaction).where(Transaction.statement_id == st.id)
                      .order_by(Transaction.booking_date, Transaction.line_number)).all()
    return [S.transaction(t) for t in rows]


@router.get("/{statement_id}/file", summary="Download/preview the original file")
def download_file(statement_id: uuid.UUID, db: DB, p: Principal = require("statements:read"),
                  inline: bool = False) -> FileResponse:
    st = _get(db, p.tenant_id, statement_id)
    if st.file is None:
        raise NotFound("No file stored for this statement")
    path = storage.resolve("statements", p.tenant_id, st.file.storage_path)
    audit(db, action="statement.download", tenant_id=p.tenant_id, actor_user_id=p.user_id, entity_type="statement",
          entity_id=st.id, ip=p.ip)
    db.commit()
    disp = "inline" if inline and st.file.detected_format in ("pdf", "csv") else "attachment"
    return FileResponse(path, media_type=st.file.mime_type or "application/octet-stream",
                        filename=st.file.original_filename, content_disposition_type=disp,
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/{statement_id}/reprocess")
def reprocess(statement_id: uuid.UUID, db: DB, p: Principal = require("statements:write")) -> dict:
    st = _get(db, p.tenant_id, statement_id)
    process_statement(db, st.id)
    db.refresh(st)
    audit(db, action="statement.reprocess", tenant_id=p.tenant_id, actor_user_id=p.user_id, entity_type="statement",
          entity_id=st.id, after={"status": st.processing_status})
    return S.statement(st)


@router.delete("/{statement_id}", status_code=204, response_class=Response, response_model=None, summary="Archive a statement (original file and transactions kept)")
def archive(statement_id: uuid.UUID, db: DB, p: Principal = require("statements:write")) -> None:
    st = _get(db, p.tenant_id, statement_id)
    st.deleted_at = datetime.now(UTC)
    audit(db, action="statement.archive", tenant_id=p.tenant_id, actor_user_id=p.user_id, entity_type="statement",
          entity_id=st.id)
