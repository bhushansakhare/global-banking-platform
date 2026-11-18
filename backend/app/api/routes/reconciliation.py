from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api import serializers as S
from app.api.deps import DB, Principal, require
from app.audit.service import record as audit
from app.core.errors import NotFound
from app.models.banking import BankAccount
from app.models.reconciliation import ReconciliationException, ReconciliationMatch, ReconciliationSession
from app.models.system import SystemSetting
from app.models.transactions import BookEntry, Transaction
from app.reconciliation import service as svc
from app.schemas.requests import ExceptionUpdate, ManualMatchRequest, MatchDecision, ReconcileRequest, ReconRules

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


@router.post("/run", status_code=201)
def run(body: ReconcileRequest, db: DB, p: Principal = require("reconciliation:write")) -> dict:
    s = svc.run_reconciliation(db, tenant_id=p.tenant_id, account_id=body.account_id, statement_id=body.statement_id,
                               period_start=body.period_start, period_end=body.period_end, trigger="manual",
                               user_id=p.user_id, rules_override=body.rules)
    return S.recon_session(s)


@router.get("/sessions")
def sessions(db: DB, p: Principal = require("reconciliation:read"), account_id: uuid.UUID | None = None,
             status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)) -> dict:
    q = select(ReconciliationSession).where(ReconciliationSession.tenant_id == p.tenant_id)
    if account_id:
        q = q.where(ReconciliationSession.account_id == account_id)
    if status:
        q = q.where(ReconciliationSession.status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(ReconciliationSession.created_at.desc()).offset((page - 1) * page_size)
                      .limit(page_size)).all()
    names = {a.id: a.nickname for a in db.scalars(select(BankAccount).where(BankAccount.tenant_id == p.tenant_id))}
    return S.page([S.recon_session(s, names.get(s.account_id)) for s in rows], total, page, page_size)


@router.get("/sessions/{session_id}")
def session_detail(session_id: uuid.UUID, db: DB, p: Principal = require("reconciliation:read")) -> dict:
    s = db.scalar(select(ReconciliationSession).where(ReconciliationSession.id == session_id,
                                                      ReconciliationSession.tenant_id == p.tenant_id))
    if s is None:
        raise NotFound("Session not found")
    acc = db.get(BankAccount, s.account_id)
    matches = db.scalars(select(ReconciliationMatch).where(ReconciliationMatch.session_id == s.id)
                         .order_by(ReconciliationMatch.status, ReconciliationMatch.score)).all()
    txn_ids = {m.transaction_id for m in matches if m.transaction_id}
    be_ids = {m.book_entry_id for m in matches if m.book_entry_id}
    excs = db.scalars(select(ReconciliationException).where(ReconciliationException.session_id == s.id)
                      .order_by(ReconciliationException.severity, ReconciliationException.exception_type)).all()
    txn_ids |= {e.transaction_id for e in excs if e.transaction_id}
    be_ids |= {e.book_entry_id for e in excs if e.book_entry_id}
    txns = {t.id: t for t in db.scalars(select(Transaction).where(Transaction.id.in_(txn_ids)))} if txn_ids else {}
    bes = {b.id: b for b in db.scalars(select(BookEntry).where(BookEntry.id.in_(be_ids)))} if be_ids else {}
    out = S.recon_session(s, acc.nickname if acc else None)
    out["currency"] = acc.currency if acc else None
    out["is_demo"] = acc.is_demo if acc else False
    out["matches"] = [S.recon_match(m, txns.get(m.transaction_id), bes.get(m.book_entry_id)) for m in matches]
    out["exceptions"] = [{**S.recon_exception(e),
                          "transaction": S.transaction(txns[e.transaction_id]) if e.transaction_id in txns else None,
                          "book_entry": S.book_entry(bes[e.book_entry_id]) if e.book_entry_id in bes else None}
                         for e in excs]
    return out


@router.post("/matches/{match_id}/decision")
def decide(match_id: uuid.UUID, body: MatchDecision, db: DB, p: Principal = require("reconciliation:write")) -> dict:
    m = svc.decide_match(db, tenant_id=p.tenant_id, match_id=match_id, decision=body.decision, user_id=p.user_id,
                         note=body.note)
    return {"id": str(m.id), "status": m.status}


@router.post("/sessions/{session_id}/manual-match", status_code=201)
def manual(session_id: uuid.UUID, body: ManualMatchRequest, db: DB,
           p: Principal = require("reconciliation:write")) -> dict:
    m = svc.manual_match(db, tenant_id=p.tenant_id, session_id=session_id, transaction_id=body.transaction_id,
                         book_entry_id=body.book_entry_id, user_id=p.user_id, note=body.note)
    return {"id": str(m.id), "status": m.status}


@router.patch("/exceptions/{exception_id}")
def update_exception(exception_id: uuid.UUID, body: ExceptionUpdate, db: DB,
                     p: Principal = require("reconciliation:write")) -> dict:
    e = svc.resolve_exception(db, tenant_id=p.tenant_id, exception_id=exception_id, status=body.status,
                              user_id=p.user_id, note=body.note)
    return S.recon_exception(e)


@router.get("/rules")
def get_rules(db: DB, p: Principal = require("reconciliation:read")) -> dict:
    return svc.tenant_rules(db, p.tenant_id).to_dict()


@router.put("/rules")
def put_rules(body: ReconRules, db: DB, p: Principal = require("settings:manage")) -> dict:
    s = db.scalar(select(SystemSetting).where(SystemSetting.tenant_id == p.tenant_id,
                                              SystemSetting.key == "reconciliation_rules"))
    value = body.model_dump(mode="json")
    before = s.value if s else None
    if s is None:
        db.add(SystemSetting(tenant_id=p.tenant_id, key="reconciliation_rules", value=value, updated_by=p.user_id))
    else:
        s.value, s.updated_by = value, p.user_id
    audit(db, action="reconciliation.rules.update", tenant_id=p.tenant_id, actor_user_id=p.user_id, before=before,
          after=value)
    return value
