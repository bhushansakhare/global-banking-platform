import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from app.core.errors import Conflict, ValidationFailed
from app.models.banking import BankAccount
from app.models.finzola import FinzolaItem, FinzolaMapping
from app.models.identity import Organization
from app.models.reconciliation import ReconciliationMatch
from app.models.statements import Statement, StatementParsingJob
from app.models.system import SystemSetting
from app.models.transactions import BookEntry, Transaction
from app.integrations.finzola.contracts import currency_exponent, digest, expectation, is_test
from app.integrations.finzola.gateway import authorized, fresh_records


def get_account(db, tenant, account_id):
    account = db.scalar(select(BankAccount).where(BankAccount.id == account_id, BankAccount.tenant_id == tenant,
        BankAccount.deleted_at.is_(None), BankAccount.is_demo.is_(False)))
    if not account or not account.connection or account.connection.status == "disconnected":
        raise ValidationFailed("Select a genuine, active bank account in this organization")
    return account


def choose_mapping(expense, mappings):
    candidates = [m for m in mappings if m.enabled and m.external_org_id == expense.get("org_id")]
    specific = [m for m in candidates if m.routing_field != "default" and expense.get(m.routing_field) == m.routing_value]
    choices = specific or [m for m in candidates if m.routing_field == "default"]
    return choices[0] if len(choices) == 1 else None


def hold_item(db, item, status, reason):
    if item.decision == "cancelled" and status != "cancelled":
        status, reason = "cancelled", item.reason or reason
    item.status, item.reason = status, reason
    if item.book_entry_id:
        book = db.get(BookEntry, item.book_entry_id)
        if book:
            book.raw_data = {**book.raw_data, "finzola_validation": status}


def hold_items(db, tenant, reason):
    for item in db.scalars(select(FinzolaItem).where(FinzolaItem.tenant_id == tenant)):
        hold_item(db, item, "source_unavailable", reason)


def evidence_signature(db, statement):
    rows = db.scalars(select(Transaction).where(Transaction.tenant_id == statement.tenant_id,
        Transaction.statement_id == statement.id).order_by(Transaction.id)).all()
    return digest([statement.file.sha256 if statement.file else None, str(statement.opening_balance),
        str(statement.closing_balance), str(statement.arithmetic_valid), statement.transaction_count,
        [(str(t.id), t.fingerprint, str(t.amount), t.currency) for t in rows]])


def evidence_review(db, tenant, statement_id, actor, reason):
    statement = db.scalar(select(Statement).where(Statement.id == statement_id, Statement.tenant_id == tenant,
        Statement.deleted_at.is_(None), Statement.is_demo.is_(False)))
    if not statement or statement.processing_status not in ("processed", "processed_with_warnings") \
            or statement.arithmetic_valid is not True or not statement.transaction_count or not statement.file:
        raise ValidationFailed("Review a genuine, extracted statement with valid arithmetic first")
    get_account(db, tenant, statement.account_id)
    from app.statements.storage import verify_integrity
    if not verify_integrity(tenant, statement.file.storage_path, statement.file.sha256):
        raise ValidationFailed("Statement integrity check failed")
    key = f"finzola_evidence:{statement.id}"
    row = db.scalar(select(SystemSetting).where(SystemSetting.tenant_id == tenant, SystemSetting.key == key))
    if row is None:
        row = SystemSetting(tenant_id=tenant, key=key)
        db.add(row)
    row.value = {"signature": evidence_signature(db, statement), "reviewed_at": datetime.now(UTC).isoformat(),
                 "reason": reason}
    row.updated_by = actor
    db.flush()


def valid_evidence(db, transaction, cache=None):
    if transaction.is_demo or transaction.source != "statement" or not transaction.statement_id or is_test(transaction.raw_data):
        return False
    statement = db.get(Statement, transaction.statement_id)
    if not statement or statement.tenant_id != transaction.tenant_id or statement.account_id != transaction.account_id \
            or statement.deleted_at or statement.is_demo or not statement.file \
            or statement.processing_status not in ("processed", "processed_with_warnings") \
            or statement.arithmetic_valid is not True or statement.currency != transaction.currency:
        return False
    if cache is not None and statement.id in cache:
        return cache[statement.id]
    result = reviewed_statement(db, statement)
    if cache is not None:
        cache[statement.id] = result
    return result


def reviewed_statement(db, statement):
    row = db.scalar(select(SystemSetting).where(SystemSetting.tenant_id == statement.tenant_id,
        SystemSetting.key == f"finzola_evidence:{statement.id}"))
    if not row or not authorized(db, statement.tenant_id, row.updated_by, "statements:write"):
        return False
    latest = db.scalar(select(StatementParsingJob).where(StatementParsingJob.statement_id == statement.id)
                       .order_by(StatementParsingJob.created_at.desc()).limit(1))
    if latest and latest.created_at > datetime.fromisoformat(row.value["reviewed_at"]):
        return False
    if row.value.get("signature") != evidence_signature(db, statement):
        return False
    from app.statements.storage import verify_integrity
    return verify_integrity(statement.tenant_id, statement.file.storage_path, statement.file.sha256)


def current_source(db, item, records=None, mappings=None):
    records = fresh_records(db, item.tenant_id) if records is None else records
    expense = next((r for r in records["expenses"] if r.get("id") == item.external_id
                    and r.get("org_id") == item.external_org_id), None)
    mappings = db.scalars(select(FinzolaMapping).where(FinzolaMapping.tenant_id == item.tenant_id)).all() if mappings is None else mappings
    mapping = choose_mapping(expense, mappings) if expense else None
    if not expense or not mapping or not authorized(db, item.tenant_id, mapping.authorized_by,
                                                    "settings:manage", "reconciliation:write"):
        raise ValueError("Source/mapping/authorizer is unavailable")
    account = get_account(db, item.tenant_id, mapping.account_id)
    expected = expectation(expense, records.get("finance", []), mapping, account.currency)
    source_hash = digest([expense, [r for r in records.get("finance", []) if r.get("expense_id") == expense["id"]],
                          str(mapping.id), mapping.revision, expected])
    return mapping, expected, source_hash


def process_job(ctx):
    db, tenant = ctx.db, ctx.job.tenant_id
    lock_key = int(digest([str(tenant), "finzola-fetch"])[:15], 16)
    if not db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": lock_key}).scalar():
        from app.automation.handlers import Deferred
        raise Deferred(10, "Finzola fetch/comparison is already running")
    from app.integrations.finzola.gateway import setting
    setting(db, tenant, lock=True)
    try:
        records = fresh_records(db, tenant)
    except ValueError as exc:
        hold_items(db, tenant, str(exc))
        return {"blocked": str(exc)}
    mappings = db.scalars(select(FinzolaMapping).where(FinzolaMapping.tenant_id == tenant)).all()
    seen, touched = set(), []
    finance_by_expense = {}
    for finance in records.get("finance", []):
        if isinstance(finance.get("expense_id"), str):
            finance_by_expense.setdefault(finance["expense_id"], []).append(finance)
    for expense in records["expenses"]:
        org = expense.get("org_id")
        if not isinstance(org, str) or not org or len(org) > 120:
            continue  # Invalid external identity can never be interpreted as a local tenant.
        identity = (org, expense["id"])
        seen.add(identity)
        item = db.scalar(select(FinzolaItem).where(FinzolaItem.tenant_id == tenant,
            FinzolaItem.external_org_id == org, FinzolaItem.external_id == expense["id"]))
        if item is None:
            item = FinzolaItem(tenant_id=tenant, external_org_id=org, external_id=expense["id"], source_hash=digest(expense))
            db.add(item)
            db.flush()
        touched.append(item)
        if item.decision in ("cancelled", "retained"):
            continue
        if is_test(expense):
            hold_item(db, item, "excluded_test", "Synthetic/demo source excluded")
            continue
        mapping = choose_mapping(expense, mappings)
        if not mapping:
            hold_item(db, item, "requires_attention", "No unique organization/account route matches this expense")
            continue
        try:
            local_records = {"expenses": [expense], "finance": finance_by_expense.get(expense["id"], [])}
            mapping, expected, source_hash = current_source(db, item, local_records, mappings)
        except (ValueError, ValidationFailed) as exc:
            hold_item(db, item, "requires_attention", str(exc))
            continue
        if item.book_entry_id and source_hash != item.source_hash:
            hold_item(db, item, "requires_attention", "SOURCE_CHANGED: review required; existing financial links preserved")
            continue
        item.mapping_id, item.account_id, item.expected, item.source_hash = mapping.id, mapping.account_id, expected, source_hash
        if not item.book_entry_id:
            amount = Decimal(expected["amount_minor"]) / (10 ** currency_exponent(expected["currency"]))
            book = BookEntry(tenant_id=tenant, account_id=mapping.account_id, entry_date=date.fromisoformat(expected["date"]),
                description=expected["purpose"], reference=expected["reference"], currency=expected["currency"],
                amount=-amount if expected["direction"] == "debit" else amount, source="finzola", is_demo=False,
                external_ref=f"finzola:{digest(identity)}", raw_data={"finzola_validation": "valid", "source_item": str(item.id)})
            db.add(book)
            db.flush()
            item.book_entry_id = book.id
        else:
            book = db.get(BookEntry, item.book_entry_id)
            book.raw_data = {**book.raw_data, "finzola_validation": "valid"}
        item.status, item.reason = "awaiting_bank_evidence", "Waiting for reviewed bank statement evidence"
    for item in db.scalars(select(FinzolaItem).where(FinzolaItem.tenant_id == tenant)):
        if (item.external_org_id, item.external_id) not in seen:
            hold_item(db, item, "source_unavailable", "Source expense was removed or withdrawn")
    db.flush()
    organization = db.get(Organization, tenant)
    today = datetime.now(ZoneInfo(organization.timezone or "UTC")).date()
    groups = {}
    for item in touched:
        if item.status != "awaiting_bank_evidence":
            continue
        if date.fromisoformat(item.expected["date"]) > today:
            item.status, item.reason = "awaiting_due_date", "Expected payment date has not arrived"
        else:
            groups.setdefault(item.account_id, []).append(item)
    for account_id, items in groups.items():
        compare_group(db, tenant, account_id, items)
    return {"items_processed": len(touched), "accounts_compared": len(groups)}


def compare_group(db, tenant, account_id, items):
    from app.reconciliation.service import run_reconciliation, tenant_rules
    rules = tenant_rules(db, tenant)
    dates = [date.fromisoformat(i.expected["date"]) for i in items]
    start, end = min(dates) - timedelta(days=rules.date_tolerance_days), max(dates) + timedelta(days=rules.date_tolerance_days)
    cache = {}
    txs = [t for t in db.scalars(select(Transaction).where(Transaction.tenant_id == tenant,
        Transaction.account_id == account_id, Transaction.booking_date.between(start, end))) if valid_evidence(db, t, cache)]
    if not txs:
        return
    signature = digest([[(str(i.id), i.source_hash) for i in sorted(items, key=lambda i: str(i.id))],
                        sorted(str(t.id) for t in txs), rules.to_dict()])
    key = f"finzola_comparison:{account_id}"
    stored = db.scalar(select(SystemSetting).where(SystemSetting.tenant_id == tenant, SystemSetting.key == key))
    if stored and stored.value.get("signature") == signature:
        session_id = uuid.UUID(stored.value["session_id"])
    else:
        session = run_reconciliation(db, tenant_id=tenant, account_id=account_id, period_start=start,
            period_end=end, trigger="automation", reviewed_only=True, book_ids=[i.book_entry_id for i in items])
        session_id = session.id
        if stored is None:
            stored = SystemSetting(tenant_id=tenant, key=key)
            db.add(stored)
        stored.value = {"signature": signature, "session_id": str(session_id)}
    matches = db.scalars(select(ReconciliationMatch).where(ReconciliationMatch.tenant_id == tenant,
                                                          ReconciliationMatch.session_id == session_id)).all()
    for item in items:
        match = next((m for m in matches if m.book_entry_id == item.book_entry_id and m.status in ("confirmed", "manual")), None)
        item.session_id, item.transaction_id = session_id, match.transaction_id if match else None
        item.status = "reconciled" if match else "reconciled_with_exceptions"
        item.reason = "Reviewed bank transaction matched" if match else "No confirmed match; inspect the comparison report"
        from app.integrations.finzola.outbound import enqueue_result
        enqueue_result(db, item)


def review_item(db, item, body):
    if body.decision in ("retain", "cancel"):
        item.decision = "retained" if body.decision == "retain" else "cancelled"
        hold_item(db, item, "cancelled" if body.decision == "cancel" else "requires_attention", body.reason)
        return
    if item.book_entry_id:
        linked = db.scalar(select(ReconciliationMatch.id).where(ReconciliationMatch.tenant_id == item.tenant_id,
            ReconciliationMatch.book_entry_id == item.book_entry_id).limit(1))
        if linked:
            raise Conflict("Existing comparison/manual financial links are retained; replacement is not allowed")
    mapping, expected, source_hash = current_source(db, item)
    if item.book_entry_id:
        old = db.get(BookEntry, item.book_entry_id)
        old.deleted_at = datetime.now(UTC)
        old.external_ref = f"superseded:{old.id}"
    item.book_entry_id, item.session_id, item.transaction_id = None, None, None
    item.decision, item.source_hash, item.expected, item.mapping_id = None, source_hash, expected, mapping.id
    item.status, item.reason = "awaiting_bank_evidence", body.reason
