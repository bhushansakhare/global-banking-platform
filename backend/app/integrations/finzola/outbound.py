import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, text

from app.automation import queue
from app.core.crypto import decrypt_json, encrypt_json, keyed_hash
from app.core.errors import Conflict, ValidationFailed
from app.database.session import get_engine
from app.integrations.base.adapter import ProviderError, ProviderUnavailable
from app.integrations.finzola import transport
from app.integrations.finzola.bridge import current_source, get_account, valid_evidence
from app.integrations.finzola.contracts import amount_minor, currency_exponent, digest
from app.integrations.finzola.gateway import authorized
from app.models.finzola import FinzolaDelivery, FinzolaDestination, FinzolaItem
from app.models.reconciliation import ReconciliationMatch
from app.models.transactions import Transaction


def destination(db, tenant, org):
    return db.scalar(select(FinzolaDestination).where(FinzolaDestination.tenant_id == tenant,
        FinzolaDestination.external_org_id == org, FinzolaDestination.deleted_at.is_(None)))


def save_destination(db, tenant, actor, body):
    row = db.scalar(select(FinzolaDestination).where(FinzolaDestination.tenant_id == tenant,
        FinzolaDestination.external_org_id == body.external_org_id).with_for_update())
    if row is None and body.webhook_key is None:
        raise ValidationFailed("An organization-generated Finzola webhook key is required")
    if row is None:
        row = FinzolaDestination(tenant_id=tenant, external_org_id=body.external_org_id, revision=0, authorized_by=actor)
        db.add(row)
    if body.webhook_key is not None:
        raw = body.webhook_key.get_secret_value()
        fingerprint = keyed_hash(raw)
        duplicate = db.scalar(select(FinzolaDestination.id).where(FinzolaDestination.key_hash == fingerprint,
            FinzolaDestination.id != row.id))
        if duplicate:
            raise Conflict("This organization key is already assigned to a destination")
        row.encrypted_key = encrypt_json({"key": raw}, f"finzola-destination:{tenant}:{body.external_org_id}")
        row.key_hash, row.revision = fingerprint, (row.revision or 0) + 1
        row.verified_revision, row.verified_at, row.auto_delivery = None, None, False
        row.last_error = None
    else:
        if body.auto_delivery and row.verified_revision != row.revision:
            raise ValidationFailed("Test and verify the current destination key before enabling automatic delivery")
        row.auto_delivery = body.auto_delivery
    row.authorized_by, row.deleted_at = actor, None
    db.flush()
    return row


def require_destination(db, row):
    if not row or row.deleted_at or row.verified_revision != row.revision:
        raise ValidationFailed("Select a currently verified organization destination")
    if not authorized(db, row.tenant_id, row.authorized_by, "settings:manage"):
        raise ValidationFailed("Destination authorizer no longer has permission")


def payload_for(db, tenant, kind, source_id):
    if kind == "debit":
        tx = db.scalar(select(Transaction).where(Transaction.id == source_id, Transaction.tenant_id == tenant))
        if not tx or tx.amount >= 0 or tx.debit <= 0 or tx.credit != 0 or tx.amount != -tx.debit or not valid_evidence(db, tx):
            raise ValidationFailed("Choose a genuine debit from a reviewed, validated bank statement")
        get_account(db, tenant, tx.account_id)
        minor = amount_minor(tx.debit, tx.currency, "major")
        amount = Decimal(minor) / (10 ** currency_exponent(tx.currency))
        result = {"requestId": f"banking-expense-{tenant}-{tx.id}", "eventType": "created", "internalId": str(tx.id),
            "purpose": tx.description[:500] or "Banking debit expense", "amount": float(amount), "currency": tx.currency,
            "expenseDate": tx.booking_date.isoformat(),
            "description": "Banking debit exported for display. This does not approve an expense or initiate payment."}
    else:
        item = db.scalar(select(FinzolaItem).where(FinzolaItem.id == source_id, FinzolaItem.tenant_id == tenant))
        if not item or item.decision or item.status not in ("reconciled", "reconciled_with_exceptions") or not item.session_id:
            raise ValidationFailed("A current Finzola comparison result is required")
        _, expected, source_hash = current_source(db, item)
        if source_hash != item.source_hash or expected != item.expected:
            raise ValidationFailed("Financial source changed; review is required")
        if item.status == "reconciled":
            match = db.scalar(select(ReconciliationMatch).where(ReconciliationMatch.session_id == item.session_id,
                ReconciliationMatch.book_entry_id == item.book_entry_id, ReconciliationMatch.status.in_(("confirmed", "manual"))))
            tx = db.get(Transaction, match.transaction_id) if match else None
            if not tx or not valid_evidence(db, tx):
                raise ValidationFailed("Bank match/review is no longer valid")
        amount = Decimal(expected["amount_minor"]) / (10 ** currency_exponent(expected["currency"]))
        result = {"requestId": f"banking-recon-{tenant}-{digest([item.external_org_id, item.external_id])}",
            "eventType": "reconciliation.updated", "internalId": str(item.book_entry_id), "purpose": expected["purpose"],
            "amount": float(amount), "currency": expected["currency"], "expenseDate": expected["date"],
            "statusKey": item.status, "paymentStatus": f"Bank comparison: {item.status}",
            "description": f"{item.reason}. Comparison report {item.session_id}. This does not approve an expense or initiate payment."}
    if Decimal(str(result["amount"])) != amount:
        raise ValidationFailed("Amount cannot be represented exactly by the receiver's numeric contract")
    return result


def enqueue_delivery(db, tenant, row, kind, source_id, actor):
    require_destination(db, row)
    db.execute(select(FinzolaDestination.id).where(FinzolaDestination.id == row.id).with_for_update())
    if not authorized(db, tenant, actor, "settings:manage", "transactions:read"):
        raise ValidationFailed("Delivery authorizer no longer has permission")
    payload = payload_for(db, tenant, kind, source_id)
    payload_hash = digest(payload)
    existing = db.scalar(select(FinzolaDelivery).where(FinzolaDelivery.tenant_id == tenant,
        FinzolaDelivery.destination_id == row.id, FinzolaDelivery.request_id == payload["requestId"],
        FinzolaDelivery.payload_hash == payload_hash, FinzolaDelivery.revision == row.revision))
    if existing:
        return existing
    delivery = FinzolaDelivery(tenant_id=tenant, destination_id=row.id, source_kind=kind, source_id=source_id,
        request_id=payload["requestId"], payload_hash=payload_hash, payload=payload, revision=row.revision,
        authorized_by=actor, status="queued", attempts=0)
    db.add(delivery)
    db.flush()
    job, _ = queue.enqueue(db, tenant_id=tenant, job_type="finzola_deliver", payload={"delivery_id": str(delivery.id)},
        idempotency_key=f"finzola-delivery:{delivery.id}", trigger="pipeline", created_by=actor, max_attempts=4, priority=120)
    delivery.job_id = job.id
    return delivery


def enqueue_result(db, item):
    row = destination(db, item.tenant_id, item.external_org_id)
    if row and row.auto_delivery and row.verified_revision == row.revision:
        try:
            enqueue_delivery(db, item.tenant_id, row, "comparison", item.id, row.authorized_by)
        except (ValueError, ValidationFailed):
            return None


def test_job(ctx):
    db, job = ctx.db, ctx.job
    row = db.scalar(select(FinzolaDestination).where(FinzolaDestination.id == uuid.UUID(job.payload["destination_id"]),
        FinzolaDestination.tenant_id == job.tenant_id, FinzolaDestination.deleted_at.is_(None)))
    if not row or row.revision != job.payload["revision"] or not authorized(db, job.tenant_id, row.authorized_by, "settings:manage"):
        return {"skipped": "Destination or permissions changed"}
    revision = row.revision
    key = decrypt_json(row.encrypted_key, f"finzola-destination:{job.tenant_id}:{row.external_org_id}")["key"]
    try:
        ack = transport.send(key, {"requestId": "EXP-TEST", "eventType": "test"})
    except transport.RemoteError as exc:
        row.verified_revision, row.auto_delivery, row.last_error = None, False, str(exc)
        db.commit()
        raise ProviderError(str(exc)) from None
    db.refresh(row)
    if row.revision != revision or row.deleted_at:
        return {"skipped": "Destination key changed during test"}
    row.verified_revision, row.verified_at, row.last_error = revision, datetime.now(UTC), None
    return {"verified": True, "acknowledgement": ack, "expense_written": False}


DELIVERY_SLOTS = (0x465A00000000, 0x465A00000001)


def delivery_job(ctx):
    delivery = ctx.db.scalar(select(FinzolaDelivery).where(
        FinzolaDelivery.id == uuid.UUID(ctx.job.payload["delivery_id"]), FinzolaDelivery.tenant_id == ctx.job.tenant_id))
    if not delivery:
        return {"skipped": "Delivery unavailable"}
    destination_lock = int(digest(["finzola-destination", str(delivery.destination_id)])[:15], 16)
    from app.automation.handlers import Deferred
    with get_engine().connect() as lock:
        keys = []
        try:
            if not lock.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": destination_lock}).scalar():
                raise Deferred(3, "Another delivery to this organization is running")
            keys.append(destination_lock)
            for slot in DELIVERY_SLOTS:
                if lock.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": slot}).scalar():
                    keys.append(slot)
                    break
            else:
                raise Deferred(3, "Two Finzola deliveries are already running")
            lock.commit()
            return _deliver_locked(ctx)
        finally:
            for key in reversed(keys):
                lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
            lock.commit()


def _deliver_locked(ctx):
    db, job = ctx.db, ctx.job
    delivery = db.scalar(select(FinzolaDelivery).where(FinzolaDelivery.id == uuid.UUID(job.payload["delivery_id"]),
                                                      FinzolaDelivery.tenant_id == job.tenant_id).with_for_update())
    if delivery is None or delivery.status in ("delivered", "superseded", "cancelled", "failed_permanent"):
        return {"skipped": "Delivery is already terminal"}
    row = db.get(FinzolaDestination, delivery.destination_id)
    try:
        require_destination(db, row)
        if delivery.revision != row.revision or not authorized(db, job.tenant_id, delivery.authorized_by,
                                                              "settings:manage", "transactions:read"):
            raise ValueError("Destination revision or authorizer changed")
        if delivery.source_kind == "comparison" and not row.auto_delivery:
            raise ValueError("Automatic result delivery is disabled")
        fresh = payload_for(db, job.tenant_id, delivery.source_kind, delivery.source_id)
        if digest(fresh) != delivery.payload_hash or digest(delivery.payload) != delivery.payload_hash:
            raise ValueError("Source/result changed since this delivery was queued")
        if delivery.source_kind == "comparison":
            item = db.get(FinzolaItem, delivery.source_id)
            if item.external_org_id != row.external_org_id:
                raise ValueError("Destination organization changed")
    except (ValueError, ValidationFailed) as exc:
        delivery.status, delivery.error = "superseded", str(exc)
        return {"superseded": True}
    delivery.status, delivery.attempts = "running", delivery.attempts + 1
    db.commit()
    key = decrypt_json(row.encrypted_key, f"finzola-destination:{job.tenant_id}:{row.external_org_id}")["key"]
    try:
        ack = transport.send(key, delivery.payload)
    except transport.RemoteError as exc:
        delivery.status = "retry_pending" if exc.transient and job.attempts < job.max_attempts else "failed_permanent"
        delivery.error = str(exc)
        if exc.auth:
            row.verified_revision, row.auto_delivery, row.last_error = None, False, str(exc)
        db.commit()
        if exc.transient:
            raise ProviderUnavailable(str(exc)) from None
        raise ProviderError(str(exc)) from None
    delivery.status, delivery.acknowledgement, delivery.error = "delivered", ack, None
    return {"delivered": True, "acknowledgement": ack, "delivery_id": str(delivery.id)}
