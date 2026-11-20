"""Tenant-scoped Finzola workspace. Secrets are write-only; bank evidence is separate."""
import csv
import io
import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import func, select

from app.api.deps import DB, Principal, require
from app.audit.service import record as audit
from app.automation import queue
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.integrations.finzola import bridge, gateway, outbound
from app.integrations.finzola.contracts import (DestinationInput, Domain, GatewayInput, MappingInput,
                                               ReviewInput, SendInput, safe_data)
from app.models.automation import AutomationJob
from app.models.finzola import FinzolaDelivery, FinzolaDestination, FinzolaItem, FinzolaMapping, FinzolaRecord

router = APIRouter(prefix="/integrations/finzola", tags=["finzola"])


def scoped(db, model, tenant, identifier):
    row = db.scalar(select(model).where(model.id == identifier, model.tenant_id == tenant))
    if row is None:
        raise NotFound("Finzola record not found")
    return row


def audit_action(db, p, action, identifier=None, **meta):
    audit(db, action=f"finzola.{action}", tenant_id=p.tenant_id, actor_user_id=p.user_id,
          entity_type="finzola", entity_id=identifier, meta=meta, ip=p.ip)


def delivery_view(row, job=None):
    status, error = row.status, row.error
    if status not in ("delivered", "superseded", "cancelled", "failed_permanent") and job:
        if job.status == "failed":
            status, error = "failed_permanent", job.error_message or "Delivery worker stopped before acknowledgement"
        elif job.status == "cancelled":
            status = "cancelled"
    return {"id": str(row.id), "destination_id": str(row.destination_id), "source_kind": row.source_kind,
        "source_id": str(row.source_id), "request_id": row.request_id, "status": status,
        "attempts": row.attempts, "error": error, "acknowledgement": row.acknowledgement,
        "created_at": row.created_at, "job_id": str(row.job_id) if row.job_id else None,
        "payload": row.payload, "revision": row.revision}


@router.get("/custom-api")
def get_config(db: DB, p: Principal = require("settings:read")):
    return gateway.config_view(db, p.tenant_id)


@router.put("/custom-api")
def put_config(body: GatewayInput, db: DB, p: Principal = require("settings:manage")):
    value = gateway.save_config(db, p.tenant_id, p.user_id, body)
    audit_action(db, p, "gateway.save", domains=body.domains, polling=body.polling_enabled)
    return value


@router.post("/custom-api/fetch", status_code=202)
def fetch_now(db: DB, p: Principal = require("settings:manage")):
    job, created = gateway.enqueue_fetch(db, p.tenant_id, p.user_id)
    audit_action(db, p, "gateway.fetch", job.id)
    return {"job_id": str(job.id), "created": created}


@router.get("/custom-api/data/{domain}")
def records(domain: Domain, db: DB, p: Principal = require("settings:read"),
            page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
    query = select(FinzolaRecord).where(FinzolaRecord.tenant_id == p.tenant_id, FinzolaRecord.domain == domain)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(FinzolaRecord.external_id).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [safe_data(r.data) for r in rows], "total": total, "page": page,
            "state": gateway.config_view(db, p.tenant_id)["state"].get(domain)}


def export_response(rows, format, name):
    rows = safe_data(rows)
    if format == "json":
        content, media = json.dumps(safe_data(rows), default=str, allow_nan=False), "application/json"
    else:
        buffer = io.StringIO(newline="")
        fields = sorted({k for row in rows for k in row})
        writer = csv.writer(buffer)
        writer.writerow(["'" + field if field.lstrip().startswith(("=", "+", "-", "@")) else field for field in fields])
        for row in safe_data(rows):
            cells = []
            for key in fields:
                value = row.get(key, "")
                value = json.dumps(value, default=str) if isinstance(value, (dict, list)) else str(value)
                cells.append("'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value)
            writer.writerow(cells)
        content, media = buffer.getvalue(), "text/csv"
    return Response(content=content, media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}.{format}"', "Cache-Control": "no-store"})


@router.get("/custom-api/data/{domain}/export")
def export_records(domain: Domain, db: DB, p: Principal = require("settings:read"),
                   format: str = Query("csv", pattern="^(csv|json)$")):
    rows = db.scalars(select(FinzolaRecord).where(FinzolaRecord.tenant_id == p.tenant_id, FinzolaRecord.domain == domain)
                      .order_by(FinzolaRecord.external_id).limit(10000)).all()
    audit_action(db, p, "records.export", domain=domain)
    return export_response([r.data for r in rows], format, f"finzola-{domain}")


@router.put("/custom-api/pipeline/mapping")
def save_mapping(body: MappingInput, db: DB, p: Principal = require("settings:manage", "reconciliation:write")):
    gateway.setting(db, p.tenant_id, lock=True)
    bridge.get_account(db, p.tenant_id, body.account_id)
    row = db.scalar(select(FinzolaMapping).where(FinzolaMapping.tenant_id == p.tenant_id,
        FinzolaMapping.external_org_id == body.external_org_id, FinzolaMapping.routing_field == body.routing_field,
        FinzolaMapping.routing_value == body.routing_value).with_for_update())
    options = body.model_dump(mode="json", exclude={"external_org_id", "account_id", "routing_field", "routing_value", "enabled"})
    if row is None:
        row = FinzolaMapping(tenant_id=p.tenant_id, external_org_id=body.external_org_id, account_id=body.account_id,
            routing_field=body.routing_field, routing_value=body.routing_value, options=options, enabled=body.enabled,
            revision=1, authorized_by=p.user_id)
        db.add(row)
    else:
        row.account_id, row.options, row.enabled = body.account_id, options, body.enabled
        row.revision, row.authorized_by = row.revision + 1, p.user_id
    db.flush()
    queued = gateway.enqueue_process(db, p.tenant_id, p.user_id)
    audit_action(db, p, "mapping.save", row.id)
    return {"id": str(row.id), "revision": row.revision, "comparison_queued": queued is not None}


@router.post("/custom-api/pipeline/process", status_code=202)
def process(db: DB, p: Principal = require("settings:manage", "reconciliation:write")):
    result = gateway.enqueue_process(db, p.tenant_id, p.user_id)
    if not result:
        raise ValidationFailed("Configure and fetch Finzola data first")
    return {"job_id": str(result[0].id), "created": result[1]}


def item_view(item):
    return {"id": str(item.id), "external_org_id": item.external_org_id, "external_id": item.external_id,
        "account_id": str(item.account_id) if item.account_id else None, "status": item.status, "reason": item.reason,
        "expected": item.expected, "decision": item.decision, "session_id": str(item.session_id) if item.session_id else None,
        "transaction_id": str(item.transaction_id) if item.transaction_id else None, "updated_at": item.updated_at}


@router.get("/custom-api/pipeline")
def pipeline(db: DB, p: Principal = require("settings:read", "accounts:read"), page: int = Query(1, ge=1)):
    maps = db.scalars(select(FinzolaMapping).where(FinzolaMapping.tenant_id == p.tenant_id)).all()
    query = select(FinzolaItem).where(FinzolaItem.tenant_id == p.tenant_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = db.scalars(query.order_by(FinzolaItem.updated_at.desc()).offset((page - 1) * 25).limit(25)).all()
    jobs = db.scalars(select(AutomationJob).where(AutomationJob.tenant_id == p.tenant_id,
        AutomationJob.job_type.in_(("finzola_fetch", "finzola_process", "finzola_deliver", "finzola_test")))
        .order_by(AutomationJob.created_at.desc()).limit(15)).all()
    return {"mappings": [{"id": str(m.id), "external_org_id": m.external_org_id, "account_id": str(m.account_id),
        "routing_field": m.routing_field, "routing_value": m.routing_value, "enabled": m.enabled,
        "revision": m.revision, **m.options} for m in maps], "items": [item_view(i) for i in items], "total": total,
        "jobs": [{"id": str(j.id), "type": j.job_type, "status": j.status, "progress": j.progress_message,
                  "error": j.error_message, "result": j.result} for j in jobs]}


@router.get("/custom-api/pipeline/export")
def pipeline_export(db: DB, p: Principal = require("settings:read"), format: str = Query("csv", pattern="^(csv|json)$")):
    rows = db.scalars(select(FinzolaItem).where(FinzolaItem.tenant_id == p.tenant_id).limit(10000)).all()
    return export_response([item_view(r) for r in rows], format, "finzola-comparison")


@router.post("/custom-api/pipeline/items/{item_id}/review")
def review(item_id: uuid.UUID, body: ReviewInput, db: DB,
           p: Principal = require("settings:manage", "reconciliation:write")):
    item = scoped(db, FinzolaItem, p.tenant_id, item_id)
    try:
        bridge.review_item(db, item, body)
    except ValueError as exc:
        raise ValidationFailed(str(exc)) from None
    gateway.enqueue_process(db, p.tenant_id, p.user_id)
    audit_action(db, p, "source.review", item.id, decision=body.decision, reason=body.reason)
    return item_view(item)


@router.get("/custom-api/pipeline/items/{item_id}/review-preview")
def review_preview(item_id: uuid.UUID, db: DB, p: Principal = require("settings:manage", "reconciliation:write")):
    item = scoped(db, FinzolaItem, p.tenant_id, item_id)
    try:
        mapping, expected, _ = bridge.current_source(db, item)
        return {"existing": item.expected, "current": expected, "account_id": str(mapping.account_id)}
    except (ValueError, ValidationFailed) as exc:
        return {"existing": item.expected, "current": None, "error": str(exc)}


@router.post("/evidence/{statement_id}/review")
def review_bank(statement_id: uuid.UUID, body: ReviewInput, db: DB,
                p: Principal = require("settings:manage", "statements:write")):
    bridge.evidence_review(db, p.tenant_id, statement_id, p.user_id, body.reason)
    queued = gateway.enqueue_process(db, p.tenant_id, p.user_id)
    audit_action(db, p, "evidence.review", statement_id, reason=body.reason)
    return {"reviewed": True, "comparison_queued": queued is not None}


@router.get("/outbound")
def outbound_status(db: DB, p: Principal = require("settings:read"), page: int = Query(1, ge=1)):
    rows = db.scalars(select(FinzolaDestination).where(FinzolaDestination.tenant_id == p.tenant_id,
                                                      FinzolaDestination.deleted_at.is_(None))).all()
    query = select(FinzolaDelivery).where(FinzolaDelivery.tenant_id == p.tenant_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    deliveries = db.scalars(query.order_by(FinzolaDelivery.created_at.desc()).offset((page - 1) * 25).limit(25)).all()
    return {"destinations": [{"id": str(r.id), "external_org_id": r.external_org_id, "has_key": True,
        "revision": r.revision, "verified": r.verified_revision == r.revision and gateway.authorized(db, p.tenant_id, r.authorized_by, "settings:manage"), "verified_at": r.verified_at,
        "auto_delivery": r.auto_delivery and gateway.authorized(db, p.tenant_id, r.authorized_by, "settings:manage"),
        "last_error": r.last_error if gateway.authorized(db, p.tenant_id, r.authorized_by, "settings:manage") else "Destination authorizer no longer has access"} for r in rows],
        "deliveries": [delivery_view(r, db.get(AutomationJob, r.job_id) if r.job_id else None) for r in deliveries], "total": total}


@router.put("/outbound")
def put_outbound(body: DestinationInput, db: DB, p: Principal = require("settings:manage")):
    row = outbound.save_destination(db, p.tenant_id, p.user_id, body)
    audit_action(db, p, "destination.save", row.id)
    if row.auto_delivery:
        gateway.enqueue_process(db, p.tenant_id, p.user_id)
    return {"id": str(row.id), "verified": row.verified_revision == row.revision, "auto_delivery": row.auto_delivery}


@router.post("/outbound/{external_org_id}/test", status_code=202)
def test_outbound(external_org_id: str, db: DB, p: Principal = require("settings:manage")):
    row = outbound.destination(db, p.tenant_id, external_org_id)
    if row is None:
        raise NotFound("Destination not found")
    active = db.scalar(select(AutomationJob).where(AutomationJob.tenant_id == p.tenant_id,
        AutomationJob.job_type == "finzola_test", AutomationJob.payload["destination_id"].astext == str(row.id),
        AutomationJob.status.in_(queue.ACTIVE_STATES)).limit(1))
    job = active or queue.enqueue(db, tenant_id=p.tenant_id, job_type="finzola_test", created_by=p.user_id,
        payload={"destination_id": str(row.id), "revision": row.revision}, max_attempts=1)[0]
    audit_action(db, p, "destination.test", row.id)
    return {"job_id": str(job.id), "verified": False}


@router.delete("/outbound/{external_org_id}")
def delete_destination(external_org_id: str, db: DB, p: Principal = require("settings:manage")):
    row = outbound.destination(db, p.tenant_id, external_org_id)
    if row is None:
        raise NotFound("Destination not found")
    row.deleted_at, row.auto_delivery, row.verified_revision = datetime.now(UTC), False, None
    for delivery in db.scalars(select(FinzolaDelivery).where(FinzolaDelivery.tenant_id == p.tenant_id,
        FinzolaDelivery.destination_id == row.id, FinzolaDelivery.status.in_(("queued", "retry_pending")))):
        delivery.status = "cancelled"
        job = db.get(AutomationJob, delivery.job_id) if delivery.job_id else None
        if job and job.status in ("queued", "retry_scheduled"):
            queue.cancel(db, job, "Finzola destination removed")
    audit_action(db, p, "destination.remove", row.id)
    return {"removed": True}


@router.post("/outbound/transactions/preview")
def preview_debit(body: SendInput, db: DB, p: Principal = require("settings:manage", "transactions:read")):
    outbound.require_destination(db, outbound.destination(db, p.tenant_id, body.external_org_id))
    return {"payload": outbound.payload_for(db, p.tenant_id, "debit", body.source_id), "external_org_id": body.external_org_id}


@router.post("/outbound/transactions/send", status_code=202)
def send_debit(body: SendInput, db: DB, p: Principal = require("settings:manage", "transactions:read")):
    row = outbound.destination(db, p.tenant_id, body.external_org_id)
    delivery = outbound.enqueue_delivery(db, p.tenant_id, row, "debit", body.source_id, p.user_id)
    audit_action(db, p, "debit.send", delivery.id)
    return delivery_view(delivery)


@router.post("/outbound/deliveries/{delivery_id}/retry", status_code=202)
def retry_delivery(delivery_id: uuid.UUID, db: DB, p: Principal = require("settings:manage")):
    delivery = scoped(db, FinzolaDelivery, p.tenant_id, delivery_id)
    if delivery.status != "retry_pending":
        raise Conflict("Only a transient, pending delivery can be retried")
    job = db.get(AutomationJob, delivery.job_id)
    if not job or job.status != "retry_scheduled":
        raise Conflict("Delivery is running or no longer retryable")
    job.run_after = datetime.now(UTC)
    audit_action(db, p, "delivery.retry", delivery.id)
    return delivery_view(delivery)
