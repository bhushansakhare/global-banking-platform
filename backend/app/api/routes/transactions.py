from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import Select, func, or_, select, update

from app.api import serializers as S
from app.api.deps import DB, Principal, require
from app.audit.service import record as audit
from app.core.errors import NotFound, ValidationFailed
from app.models.banking import BankAccount, Institution
from app.models.transactions import BookEntry, Transaction, TransactionCategory
from app.reports.builders import ReportData, Section
from app.reports.renderers import MIME, to_csv, to_xlsx
from app.schemas.requests import BookEntryCreate, BulkCategorise, CategoryCreate
from app.services.banking import get_account
from app.statements.normalize import normalize_table

router = APIRouter(tags=["transactions"])

SORTS = {"date": Transaction.booking_date, "amount": Transaction.amount, "description": Transaction.description,
         "value_date": Transaction.value_date}


def _filtered(p: Principal, account_id, date_from, date_to, q, direction, status, category_id, min_amount,
              max_amount, currency, duplicate_suspect, statement_id) -> Select:
    s = select(Transaction).where(Transaction.tenant_id == p.tenant_id)
    if account_id:
        s = s.where(Transaction.account_id == account_id)
    if statement_id:
        s = s.where(Transaction.statement_id == statement_id)
    if date_from:
        s = s.where(Transaction.booking_date >= date_from)
    if date_to:
        s = s.where(Transaction.booking_date <= date_to)
    if q:
        like = f"%{q}%"
        s = s.where(or_(Transaction.description.ilike(like), Transaction.reference.ilike(like)))
    if direction == "credit":
        s = s.where(Transaction.amount > 0)
    elif direction == "debit":
        s = s.where(Transaction.amount < 0)
    if status:
        s = s.where(Transaction.reconciliation_status == status)
    if category_id:
        s = s.where(Transaction.category_id == category_id)
    if min_amount is not None:
        s = s.where(func.abs(Transaction.amount) >= min_amount)
    if max_amount is not None:
        s = s.where(func.abs(Transaction.amount) <= max_amount)
    if currency:
        s = s.where(Transaction.currency == currency.upper())
    if duplicate_suspect is not None:
        s = s.where(Transaction.duplicate_suspect.is_(duplicate_suspect))
    return s


def _names(db, tenant_id):
    rows = db.execute(select(BankAccount.id, BankAccount.nickname, Institution.name)
                      .join(Institution, Institution.id == BankAccount.institution_id)
                      .where(BankAccount.tenant_id == tenant_id)).all()
    return {r[0]: (r[1], r[2]) for r in rows}


@router.get("/transactions")
def list_transactions(db: DB, p: Principal = require("transactions:read"), account_id: uuid.UUID | None = None,
                      statement_id: uuid.UUID | None = None, date_from: date | None = None,
                      date_to: date | None = None, q: str | None = Query(None, max_length=200),
                      direction: str | None = Query(None, pattern="^(credit|debit)$"), status: str | None = None,
                      category_id: uuid.UUID | None = None, min_amount: Decimal | None = None,
                      max_amount: Decimal | None = None, currency: str | None = None,
                      duplicate_suspect: bool | None = None, sort: str = "date",
                      order: str = Query("desc", pattern="^(asc|desc)$"), page: int = Query(1, ge=1),
                      page_size: int = Query(50, ge=1, le=500)) -> dict:
    base = _filtered(p, account_id, date_from, date_to, q, direction, status, category_id, min_amount, max_amount,
                     currency, duplicate_suspect, statement_id)
    sub = base.subquery()
    totals = db.execute(select(func.count(), func.coalesce(func.sum(sub.c.credit), 0),
                               func.coalesce(func.sum(sub.c.debit), 0), func.count(func.distinct(sub.c.currency)))
                        ).one()
    total = totals[0] or 0
    totals = totals[1:]
    col = SORTS.get(sort, Transaction.booking_date)
    rows = db.scalars(base.order_by(col.asc() if order == "asc" else col.desc(), Transaction.line_number.desc()
                                    if order == "desc" else Transaction.line_number.asc(), Transaction.id)
                      .offset((page - 1) * page_size).limit(page_size)).all()
    names = _names(db, p.tenant_id)
    out = S.page([S.transaction(t, *names.get(t.account_id, (None, None))) for t in rows], total, page, page_size)
    out["totals"] = {"credits": S.money(totals[0]), "debits": S.money(totals[1]),
                     "mixed_currencies": (totals[2] or 0) > 1}
    return out


@router.get("/transactions/export")
def export_transactions(db: DB, p: Principal = require("transactions:read"),
                        fmt: str = Query("csv", pattern="^(csv|xlsx)$"), account_id: uuid.UUID | None = None,
                        date_from: date | None = None, date_to: date | None = None, q: str | None = None,
                        direction: str | None = None, status: str | None = None,
                        ids: str | None = Query(None, description="comma separated ids (bulk selection)")) -> Response:
    base = _filtered(p, account_id, date_from, date_to, q, direction, status, None, None, None, None, None, None)
    if ids:
        try:
            base = base.where(Transaction.id.in_([uuid.UUID(x) for x in ids.split(",") if x]))
        except ValueError as exc:
            raise ValidationFailed("Invalid id list") from exc
    rows = db.scalars(base.order_by(Transaction.booking_date, Transaction.line_number).limit(100000)).all()
    names = _names(db, p.tenant_id)
    sec = Section("Transactions", ["Date", "Value date", "Bank", "Account", "Description", "Reference", "Debit",
                                   "Credit", "Currency", "Balance", "Category", "Reconciliation", "Source", "Data"],
                  [[t.booking_date.isoformat(), t.value_date.isoformat() if t.value_date else "",
                    names.get(t.account_id, ("", ""))[1], names.get(t.account_id, ("", ""))[0], t.description,
                    t.reference or "", float(t.debit) or None, float(t.credit) or None, t.currency,
                    float(t.running_balance) if t.running_balance is not None else None,
                    t.category.name if t.category else "", t.reconciliation_status, t.source,
                    "DEMO" if t.is_demo else "Live"] for t in rows])
    data = ReportData("Transaction export", f"{len(rows)} transactions", [sec], any(t.is_demo for t in rows))
    content = to_csv(data) if fmt == "csv" else to_xlsx(data)
    audit(db, action="transactions.export", tenant_id=p.tenant_id, actor_user_id=p.user_id,
          meta={"format": fmt, "rows": len(rows)})
    db.commit()
    return Response(content, media_type=MIME[fmt],
                    headers={"Content-Disposition": f'attachment; filename="transactions.{fmt}"'})


@router.post("/transactions/categorise")
def bulk_categorise(body: BulkCategorise, db: DB, p: Principal = require("transactions:write")) -> dict:
    if body.category_id and not db.scalar(select(TransactionCategory.id).where(
            TransactionCategory.id == body.category_id, TransactionCategory.tenant_id == p.tenant_id)):
        raise NotFound("Category not found")
    res = db.execute(update(Transaction).where(Transaction.tenant_id == p.tenant_id,
                                               Transaction.id.in_(body.transaction_ids))
                     .values(category_id=body.category_id))
    audit(db, action="transactions.categorise", tenant_id=p.tenant_id, actor_user_id=p.user_id,
          meta={"count": res.rowcount, "category_id": str(body.category_id) if body.category_id else None})
    return {"updated": res.rowcount}


@router.get("/transactions/categories")
def categories(db: DB, p: Principal = require("transactions:read")) -> list[dict]:
    return [{"id": str(c.id), "name": c.name, "colour": c.colour, "keywords": c.keywords, "direction": c.direction,
             "priority": c.priority}
            for c in db.scalars(select(TransactionCategory).where(TransactionCategory.tenant_id == p.tenant_id)
                                .order_by(TransactionCategory.name))]


@router.post("/transactions/categories", status_code=201)
def create_category(body: CategoryCreate, db: DB, p: Principal = require("transactions:write")) -> dict:
    c = TransactionCategory(tenant_id=p.tenant_id, **body.model_dump())
    db.add(c)
    db.flush()
    audit(db, action="category.create", tenant_id=p.tenant_id, actor_user_id=p.user_id, entity_type="category",
          entity_id=c.id, after=body.model_dump())
    return {"id": str(c.id)}


@router.get("/transactions/{transaction_id}")
def get_transaction(transaction_id: uuid.UUID, db: DB, p: Principal = require("transactions:read")) -> dict:
    t = db.scalar(select(Transaction).where(Transaction.id == transaction_id, Transaction.tenant_id == p.tenant_id))
    if t is None:
        raise NotFound("Transaction not found")
    out = S.transaction(t, *_names(db, p.tenant_id).get(t.account_id, (None, None)))
    out["raw_data"] = t.raw_data
    out["fingerprint"] = t.fingerprint
    out["provider_transaction_id"] = t.provider_transaction_id
    return out


# ---- book entries (internal ledger) -----------------------------------------
@router.get("/book-entries")
def list_book_entries(db: DB, p: Principal = require("transactions:read"), account_id: uuid.UUID | None = None,
                      status: str | None = None, date_from: date | None = None, date_to: date | None = None,
                      page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500)) -> dict:
    q = select(BookEntry).where(BookEntry.tenant_id == p.tenant_id, BookEntry.deleted_at.is_(None))
    if account_id:
        q = q.where(BookEntry.account_id == account_id)
    if status:
        q = q.where(BookEntry.reconciliation_status == status)
    if date_from:
        q = q.where(BookEntry.entry_date >= date_from)
    if date_to:
        q = q.where(BookEntry.entry_date <= date_to)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(BookEntry.entry_date.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return S.page([S.book_entry(b) for b in rows], total, page, page_size)


@router.post("/book-entries", status_code=201)
def create_book_entry(body: BookEntryCreate, db: DB, p: Principal = require("transactions:write")) -> dict:
    acc = get_account(db, p.tenant_id, body.account_id)
    b = BookEntry(tenant_id=p.tenant_id, account_id=acc.id, entry_date=body.entry_date, description=body.description,
                  reference=body.reference, amount=body.amount, currency=acc.currency, source="manual",
                  notes=body.notes, is_demo=False)
    db.add(b)
    db.flush()
    audit(db, action="book_entry.create", tenant_id=p.tenant_id, actor_user_id=p.user_id, entity_type="book_entry",
          entity_id=b.id, after=body.model_dump(mode="json"))
    return S.book_entry(b)


@router.post("/book-entries/import", summary="Import cash-book entries from CSV/XLSX (Date, Description, Amount|Debit/Credit, Reference)")
async def import_book_entries(db: DB, account_id: uuid.UUID = Form(...), file: UploadFile = File(...),
                              p: Principal = require("transactions:write")) -> dict:
    acc = get_account(db, p.tenant_id, account_id)
    content = await file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise ValidationFailed("File too large (max 10 MB)")
    from app.statements.detect import detect_format
    det = detect_format(content, file.filename or "")
    if det.format == "csv":
        text = content.decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
    elif det.format in ("xlsx", "xls"):
        import pandas as pd
        df = pd.read_excel(io.BytesIO(content), header=None, dtype=object,
                           engine="openpyxl" if det.format == "xlsx" else "xlrd")
        rows = [[None if (isinstance(v, float) and pd.isna(v)) else v for v in r]
                for r in df.itertuples(index=False, name=None)]
    else:
        raise ValidationFailed("Upload a CSV or Excel file")
    result = normalize_table(rows, account_currency=acc.currency)
    if not result.rows:
        raise ValidationFailed("No entries recognised. Expected columns: Date, Description, Amount (or Debit/Credit)"
                               ", Reference.", details=result.warnings[:5])
    created = skipped = 0
    for r in result.rows:
        ext = f"import:{r.booking_date}:{r.amount}:{(r.reference or '')[:60]}:{r.line_number}"
        if db.scalar(select(BookEntry.id).where(BookEntry.account_id == acc.id, BookEntry.external_ref == ext)):
            skipped += 1
            continue
        db.add(BookEntry(tenant_id=p.tenant_id, account_id=acc.id, entry_date=r.booking_date,
                         description=r.description, reference=r.reference, external_ref=ext, amount=r.amount,
                         currency=acc.currency, source="import", raw_data=r.raw))
        created += 1
    audit(db, action="book_entry.import", tenant_id=p.tenant_id, actor_user_id=p.user_id,
          meta={"created": created, "skipped": skipped, "file": file.filename})
    return {"created": created, "skipped_duplicates": skipped}
