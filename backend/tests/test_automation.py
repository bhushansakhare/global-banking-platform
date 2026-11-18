"""Durable queue, worker, scheduler and failure handling."""
from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from app.automation import queue
from app.database.session import get_engine, get_sessionmaker, session_scope
from app.models.automation import AutomationJob, AutomationSettings
from app.scheduler import scheduler
from app.workers.worker import run_pending_once
from tests.conftest import demo_institution_id


def _account(client, scenario="ok", ext="DEMO_GB_NORTHWIND", currency="GBP"):
    body = {"institution_id": demo_institution_id(client, ext), "nickname": f"Demo {scenario}", "currency": currency}
    if scenario != "ok":
        body["demo_scenario"] = scenario
    r = client.post("/api/v1/accounts", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _tenant(owner) -> uuid.UUID:
    with session_scope() as db:
        return db.scalar(text("select tenant_id from users limit 1"))


def test_enqueue_idempotent(client, owner):
    tid = _tenant(owner)
    with session_scope() as db:
        a, created1 = queue.enqueue(db, tenant_id=tid, job_type="daily_report", idempotency_key="k1")
        b, created2 = queue.enqueue(db, tenant_id=tid, job_type="daily_report", idempotency_key="k1")
    assert created1 and not created2 and a.id == b.id


def test_concurrent_claims_never_overlap(client, owner):
    tid = _tenant(owner)
    with session_scope() as db:
        for i in range(30):
            queue.enqueue(db, tenant_id=tid, job_type="noop", idempotency_key=f"c{i}")
    claimed: list[uuid.UUID] = []
    lock = threading.Lock()

    def worker(n):
        S = get_sessionmaker()
        for _ in range(10):
            with S() as db:
                ids = queue.claim(db, f"w{n}", 2)
                db.commit()
            with lock:
                claimed.extend(ids)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(claimed) == 30 and len(set(claimed)) == 30


def test_backoff_and_permanent_failure(client, owner):
    tid = _tenant(owner)
    with session_scope() as db:
        job, _ = queue.enqueue(db, tenant_id=tid, job_type="noop", max_attempts=2)
        job.attempts = 1
        assert queue.fail(db, job, code="x", message="boom", retryable=True, base_delay=10) is False
        assert job.status == "retry_scheduled" and job.run_after > datetime.now(UTC) + timedelta(seconds=9)
        job.attempts = 2
        assert queue.fail(db, job, code="x", message="boom", retryable=True) is True
        assert job.status == "failed"
    assert 60 <= queue.backoff_seconds(1, 60) <= 75 and 240 <= queue.backoff_seconds(3, 60) <= 300


def test_stale_job_recovered_after_crash(client, owner):
    tid = _tenant(owner)
    with session_scope() as db:
        job, _ = queue.enqueue(db, tenant_id=tid, job_type="noop")
        jid = job.id
    with session_scope() as db:
        queue.claim(db, "dead-worker", 1)
        db.execute(text("update automation_jobs set heartbeat_at = now() - interval '1 hour' where id=:i"), {"i": jid})
    with session_scope() as db:
        assert queue.recover_stale(db) == 1
    with session_scope() as db:
        j = db.get(AutomationJob, jid)
        assert j.status == "retry_scheduled" and j.error_code == "worker_lost"


def test_scheduler_runs_enabled_accounts_only(client, owner):
    a = _account(client)
    b = _account(client, ext="DEMO_IN_SAHYADRI", currency="INR")
    assert client.post(f"/api/v1/accounts/{b['id']}/automation", json={"enabled": False}).status_code == 200
    assert client.put("/api/v1/automation/settings", json={"daily_time": "05:30", "timezone": "Asia/Kolkata"}
                      ).status_code == 200
    s = client.post("/api/v1/automation/master", json={"enabled": True}).json()
    assert s["master_enabled"] and s["next_run_at"]
    with get_engine().begin() as c:
        c.execute(text("update automation_settings set next_run_at = now() - interval '1 minute'"))
    assert scheduler.tick() == 1
    assert scheduler.tick() == 0  # next_run_at moved forward — no duplicate run
    with session_scope() as db:
        jobs = db.scalars(select(AutomationJob).where(AutomationJob.job_type == "collect_account")).all()
        assert [str(j.account_id) for j in jobs] == [a["id"]]
        st = db.scalar(select(AutomationSettings))
        assert st.next_run_at > datetime.now(UTC)


def test_master_off_cancels_queued_but_not_running(client, owner):
    _account(client)
    client.post("/api/v1/automation/master", json={"enabled": True})
    with get_engine().begin() as c:
        c.execute(text("update automation_settings set next_run_at = now() - interval '1 minute'"))
    scheduler.tick()
    with session_scope() as db:
        running = db.scalar(select(AutomationJob).where(AutomationJob.job_type == "collect_account"))
        running.status = "running"
    client.post("/api/v1/automation/master", json={"enabled": False})
    with session_scope() as db:
        statuses = {j.job_type: j.status for j in db.scalars(select(AutomationJob))}
    assert statuses["collect_account"] == "running" and statuses["daily_report"] == "cancelled"


def test_pause_holds_jobs(client, owner):
    acc = _account(client)
    client.post("/api/v1/automation/pause")
    client.post("/api/v1/automation/run-now", json={"account_ids": [acc["id"]]})
    assert run_pending_once() == 0
    client.post("/api/v1/automation/resume")
    assert run_pending_once() >= 1


def test_one_bank_failure_does_not_stop_others(client, owner):
    good = _account(client)
    bad = _account(client, "provider_down", ext="DEMO_US_HUDSON", currency="USD")
    r = client.post("/api/v1/automation/run-now", json={"account_ids": [good["id"], bad["id"]]})
    assert r.status_code == 202 and r.json()["jobs_created"] == 2
    run_pending_once()
    jobs = client.get("/api/v1/jobs", params={"job_type": "collect_account"}).json()["items"]
    by_acc = {j["account_id"]: j for j in jobs}
    assert by_acc[good["id"]]["status"] == "succeeded"
    assert by_acc[bad["id"]]["status"] == "retry_scheduled"
    assert by_acc[bad["id"]]["error_code"] == "provider_unavailable"
    ov = client.get("/api/v1/automation")
    assert ov.status_code == 200, ov.text
    assert ov.json()["jobs_24h"]["succeeded"] >= 1 and len(ov.json()["accounts"]) == 2
    detail = client.get(f"/api/v1/jobs/{by_acc[bad['id']]['id']}").json()
    assert any("retrying" in log["message"] for log in detail["logs"])


def test_expired_consent_fails_and_flags_connection(client, owner):
    acc = _account(client, "consent_expired")
    client.post("/api/v1/automation/run-now", json={"account_ids": [acc["id"]]})
    run_pending_once()
    job = client.get("/api/v1/jobs", params={"account_id": acc["id"]}).json()["items"][0]
    assert job["status"] == "failed" and job["error_code"] == "consent_expired"
    a = client.get(f"/api/v1/accounts/{acc['id']}").json()
    assert a["connection"]["status"] == "consent_expired"
    notes = client.get("/api/v1/notifications").json()["items"]
    assert any(n["type"] == "auth_required" for n in notes) and any(n["type"] == "job_failed" for n in notes)


def test_missing_otp_pauses_then_resumes(client, owner):
    acc = _account(client, "otp_required")
    client.post("/api/v1/automation/run-now", json={"account_ids": [acc["id"]]})
    run_pending_once()
    job = client.get("/api/v1/jobs", params={"account_id": acc["id"]}).json()["items"][0]
    assert job["status"] == "awaiting_user" and job["requires_user_action"] == "otp"
    assert client.post(f"/api/v1/jobs/{job['id']}/resume", json={}).status_code == 422  # OTP required
    assert client.post(f"/api/v1/jobs/{job['id']}/resume", json={"otp": "123456"}).status_code == 200
    run_pending_once(200)
    job = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert job["status"] == "succeeded"
    assert "123456" not in str(job)  # OTP never persisted or logged


def test_retry_failed_job(client, owner):
    acc = _account(client, "auth_failed")
    client.post("/api/v1/automation/run-now", json={"account_ids": [acc["id"]]})
    run_pending_once()
    job = client.get("/api/v1/jobs", params={"account_id": acc["id"]}).json()["items"][0]
    assert job["status"] == "failed"
    conn_id = client.get(f"/api/v1/accounts/{acc['id']}").json()["connection"]["id"]
    client.post(f"/api/v1/connections/{conn_id}/demo-scenario", json={"scenario": "ok"})
    assert client.post(f"/api/v1/jobs/{job['id']}/retry").json()["status"] == "queued"
    run_pending_once(200)
    assert client.get(f"/api/v1/jobs/{job['id']}").json()["status"] == "succeeded"
