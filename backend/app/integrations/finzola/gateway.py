import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text

from app.automation import queue
from app.core.crypto import decrypt_json, encrypt_json
from app.core.errors import Conflict, ValidationFailed
from app.database.session import get_engine
from app.models.automation import AutomationJob
from app.models.finzola import FinzolaRecord
from app.models.identity import User
from app.models.system import SystemSetting
from app.integrations.finzola import transport
from app.integrations.finzola.contracts import digest, safe_data


def setting(db, tenant, *, lock=False):
    query = select(SystemSetting).where(SystemSetting.tenant_id == tenant, SystemSetting.key == "finzola_gateway")
    return db.scalar(query.with_for_update() if lock else query)


def authorized(db, tenant, user_id, *permissions):
    user = db.get(User, user_id) if user_id else None
    return bool(user and user.tenant_id == tenant and user.is_active and not user.deleted_at
                and set(permissions).issubset({p.code for p in user.role.permissions}))


def config_view(db, tenant):
    row = setting(db, tenant)
    value = row.value if row else {}
    return {"configured": bool(value.get("encrypted_key")), "domains": value.get("domains", ["expenses", "finance"]),
            "authorization_valid": bool(row and authorized(db, tenant, row.updated_by, "settings:manage")),
            "polling_enabled": value.get("polling_enabled", False), "interval_minutes": value.get("interval_minutes", 15),
            "state": value.get("state", {}), "next_due": value.get("next_due"), "last_fetch": value.get("last_fetch")}


def save_config(db, tenant, actor, body):
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": int(digest([str(tenant), "finzola-config"])[:15], 16)})
    row = setting(db, tenant, lock=True)
    old = row.value if row else {}
    encrypted = old.get("encrypted_key")
    if body.api_key is not None:
        encrypted = encrypt_json({"key": body.api_key.get_secret_value()}, f"finzola-gateway:{tenant}")
        db.execute(delete(FinzolaRecord).where(FinzolaRecord.tenant_id == tenant))
    elif body.domains != old.get("domains"):
        db.execute(delete(FinzolaRecord).where(FinzolaRecord.tenant_id == tenant, FinzolaRecord.domain.notin_(body.domains)))
    if not encrypted:
        raise ValidationFailed("Save an approved Finzola gateway key first")
    if row is None:
        row = SystemSetting(tenant_id=tenant, key="finzola_gateway")
        db.add(row)
    changed = body.api_key is not None or set(body.domains) != set(old.get("domains", [])) or row.updated_by != actor
    row.value = {**old, **body.model_dump(exclude={"api_key"}), "encrypted_key": encrypted,
                 "domains": list(dict.fromkeys(body.domains)), "revision": old.get("revision", 0) + int(changed),
                 "state": {} if changed else old.get("state", {}),
                 "next_due": (datetime.now(UTC) + timedelta(minutes=body.interval_minutes)).isoformat() if body.polling_enabled else None}
    row.updated_by = actor
    db.flush()
    if changed:
        from app.integrations.finzola.bridge import hold_items
        hold_items(db, tenant, "Gateway scopes/key changed; fetch fresh data")
    return config_view(db, tenant)


def enqueue_fetch(db, tenant, actor=None, *, scheduled=False):
    row = setting(db, tenant)
    if row is None or not row.value.get("encrypted_key"):
        raise ValidationFailed("Finzola gateway is not configured")
    if not authorized(db, tenant, row.updated_by, "settings:manage"):
        raise ValidationFailed("The gateway authorizer no longer has permission")
    db.execute(select(SystemSetting.id).where(SystemSetting.id == row.id).with_for_update())
    active = db.scalar(select(AutomationJob).where(AutomationJob.tenant_id == tenant,
        AutomationJob.job_type == "finzola_fetch", AutomationJob.status.in_(queue.ACTIVE_STATES)).limit(1))
    if active:
        return active, False
    return queue.enqueue(db, tenant_id=tenant, job_type="finzola_fetch", trigger="schedule" if scheduled else "manual",
                         created_by=actor or row.updated_by, max_attempts=1, priority=110)


def enqueue_process(db, tenant, actor=None):
    row = setting(db, tenant)
    if row is None:
        return None
    db.execute(select(SystemSetting.id).where(SystemSetting.id == row.id).with_for_update())
    active = db.scalar(select(AutomationJob).where(AutomationJob.tenant_id == tenant,
        AutomationJob.job_type == "finzola_process", AutomationJob.status.in_(queue.ACTIVE_STATES)).limit(1))
    return (active, False) if active else queue.enqueue(db, tenant_id=tenant, job_type="finzola_process",
        trigger="pipeline", created_by=actor or row.updated_by, max_attempts=1, priority=115)


def validate_envelope(body, domain):
    if not isinstance(body, dict) or not isinstance(body.get("data"), list) or not isinstance(body.get("meta"), dict):
        raise transport.RemoteError("Invalid Finzola response envelope")
    rows, meta = body["data"], body["meta"]
    if meta.get("domain") != domain or type(meta.get("count")) is not int or meta["count"] != len(rows) or len(rows) > 10000:
        raise transport.RemoteError("Finzola domain/count/record limit validation failed")
    ids = set()
    for row in rows:
        identifier = row.get("id") if isinstance(row, dict) else None
        if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 200 or identifier in ids:
            raise transport.RemoteError("Finzola record IDs must be nonempty and unique")
        ids.add(identifier)
    return safe_data(rows)


def fresh_records(db, tenant):
    row = setting(db, tenant)
    if not row or "expenses" not in row.value.get("domains", []):
        raise ValueError("Select and fetch the expenses domain first")
    if not authorized(db, tenant, row.updated_by, "settings:manage"):
        raise ValueError("Gateway authorizer no longer has access; refresh settings with an authorized editor")
    value, batches = row.value, set()
    for domain in value["domains"]:
        state = value.get("state", {}).get(domain, {})
        if state.get("error") or not state.get("last_success"):
            raise ValueError("Selected domains are missing or partially failed; fetch again before financial comparison")
        age = datetime.now(UTC) - datetime.fromisoformat(state["last_success"])
        if age > timedelta(minutes=max(60, value.get("interval_minutes", 15) * 2)):
            raise ValueError("Finzola source is stale; fetch it again")
        batches.add(state.get("batch"))
    if len(batches) != 1 or None in batches:
        raise ValueError("Finzola domains belong to different fetch generations")
    records = db.scalars(select(FinzolaRecord).where(FinzolaRecord.tenant_id == tenant)).all()
    return {domain: [r.data for r in records if r.domain == domain] for domain in value["domains"]}


def fetch_job(ctx):
    db, job = ctx.db, ctx.job
    row = setting(db, job.tenant_id)
    if not row or not authorized(db, job.tenant_id, row.updated_by, "settings:manage") \
            or (job.created_by and not authorized(db, job.tenant_id, job.created_by, "settings:manage")):
        return {"skipped": "Gateway settings/authorizer unavailable"}
    original = dict(row.value)
    key = decrypt_json(original["encrypted_key"], f"finzola-gateway:{job.tenant_id}")["key"]
    batch, succeeded = str(uuid.uuid4()), []
    lock_key = int(digest([str(job.tenant_id), "finzola-fetch"])[:15], 16)
    with get_engine().connect() as lock:
        if not lock.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_key}).scalar():
            from app.automation.handlers import Deferred
            raise Deferred(10, "Another Finzola fetch is running")
        lock.commit()
        try:
            for index, domain in enumerate(original["domains"]):
                ctx.progress(10 + index * 75 // len(original["domains"]), f"Fetching Finzola {domain}")
                error = None
                try:
                    data = validate_envelope(transport.fetch(domain, key), domain)
                except transport.RemoteError as exc:
                    error = str(exc)
                db.refresh(row)
                if row.value.get("revision") != original.get("revision"):
                    return {"skipped": "Gateway settings changed during fetch"}
                value = dict(row.value)
                state = dict(value.get("state", {}))
                prior = state.get(domain, {})
                if error:
                    state[domain] = {**prior, "error": error}
                else:
                    db.execute(delete(FinzolaRecord).where(FinzolaRecord.tenant_id == job.tenant_id, FinzolaRecord.domain == domain))
                    db.add_all([FinzolaRecord(tenant_id=job.tenant_id, domain=domain, external_id=r["id"], generation=batch, data=r) for r in data])
                    state[domain] = {"count": len(data), "last_success": datetime.now(UTC).isoformat(), "error": None, "batch": batch}
                    succeeded.append(domain)
                row.value = {**value, "state": state, "last_fetch": datetime.now(UTC).isoformat()}
                db.commit()  # Each successful domain is atomic; failed domains retain their previous rows.
            if len(succeeded) == len(original["domains"]):
                enqueue_process(db, job.tenant_id, job.created_by)
            else:
                from app.integrations.finzola.bridge import hold_items
                hold_items(db, job.tenant_id, "Partial Finzola fetch failure; financial comparison paused")
            return {"domains_succeeded": succeeded, "partial_failure": len(succeeded) != len(original["domains"])}
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})
            lock.commit()


def schedule_tick(db, now):
    from app.models.automation import AutomationSettings
    for row in db.scalars(select(SystemSetting).where(SystemSetting.key == "finzola_gateway").with_for_update(skip_locked=True)):
        value = row.value
        master = db.scalar(select(AutomationSettings).where(AutomationSettings.tenant_id == row.tenant_id))
        if not master or not master.master_enabled or master.paused:
            continue
        if not value.get("polling_enabled") or not value.get("next_due") or datetime.fromisoformat(value["next_due"]) > now:
            continue
        try:
            enqueue_fetch(db, row.tenant_id, scheduled=True)
        except (ValidationFailed, Conflict):
            continue
        row.value = {**value, "next_due": (now + timedelta(minutes=value["interval_minutes"])).isoformat()}
