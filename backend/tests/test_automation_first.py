"""Automation-first connections: connection methods, HDFC/Account Aggregator flow, honest health,
per-account automation controls, duplicate-job guard, credential handling and tenant isolation.

The Setu AA Gateway is replaced by an in-process fake that follows the documented request/response
shapes. No real credentials or network calls are used.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text, update

from app.automation import queue
from app.automation.state import compute_health, compute_state
from app.core.config import get_settings
from app.database.session import get_engine, session_scope
from app.integrations.base.adapter import (ConnectionContext, DataNotReady, UserActionRequired)
from app.integrations.providers import india_aa
from app.integrations.providers.india_aa import IndiaAccountAggregatorAdapter
from app.models.automation import AutomationJob, AutomationSettings
from app.models.banking import AccountBalance, BankAccount
from app.models.transactions import Transaction
from app.workers.worker import run_pending_once
from tests.conftest import PASSWORD

MOBILE = "9876543210"


# ---- helpers ----------------------------------------------------------------------------------
def _hdfc_manual_account(client, number="50100123450127") -> dict:
    """Reproduces how the real HDFC account was created: custom institution → statement upload."""
    inst = client.post("/api/v1/institutions", json={"name": "hdfc bank", "country_code": "IN"})
    assert inst.status_code == 201, inst.text
    r = client.post("/api/v1/accounts", json={"institution_id": inst.json()["id"], "nickname": "bhushan sakhare",
                                              "currency": "INR", "account_type": "savings",
                                              "account_number": number, "country_code": "IN"})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def setu_configured(monkeypatch):
    monkeypatch.setenv("SETU_AA_CLIENT_ID", "test-client")
    monkeypatch.setenv("SETU_AA_CLIENT_SECRET", "test-secret-value")
    monkeypatch.setenv("SETU_AA_PRODUCT_INSTANCE_ID", "pi-test")
    monkeypatch.setenv("SETU_AA_WEBHOOK_TOKEN", "hook-token-123")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class FakeSetu:
    """Minimal fake of the documented Setu AA Gateway endpoints."""

    def __init__(self, masked="XXXXXXXX0127", consent_status="ACTIVE"):
        self.calls: list[tuple[str, str, dict]] = []
        self.consent_status = consent_status
        self.session_polls = 0
        self.masked = masked
        today = datetime.now(UTC).date()
        self.txns = [
            {"txnId": "T1", "type": "CREDIT", "mode": "UPI", "amount": "25000.00", "currentBalance": "125000.00",
             "transactionTimestamp": f"{today - timedelta(days=3)}T10:15:00+05:30",
             "valueDate": str(today - timedelta(days=3)), "narration": "SALARY NOV", "reference": "REF001"},
            {"txnId": "T2", "type": "DEBIT", "mode": "CARD", "amount": "1499.50", "currentBalance": "123500.50",
             "transactionTimestamp": f"{today - timedelta(days=2)}T18:00:00+05:30",
             "valueDate": str(today - timedelta(days=2)), "narration": "POS GROCERY", "reference": "REF002"},
        ]

    def __call__(self, provider, method, url, **kw):
        self.calls.append((method, url, kw))
        headers = kw.get("headers") or {}
        assert headers.get("x-product-instance-id") == "pi-test"
        if method == "POST" and url.endswith("/consents"):
            assert kw["json"]["vua"] == MOBILE and kw["json"]["fiTypes"] == ["DEPOSIT"]
            return {"id": "consent-abc", "url": "https://fiu-sandbox.setu.co/consents/webview/consent-abc",
                    "status": "PENDING", "detail": {}}
        if method == "GET" and "/consents/" in url:
            return {"id": "consent-abc", "status": self.consent_status,
                    "detail": {"consentExpiry": (datetime.now(UTC) + timedelta(days=365)).isoformat()}}
        if method == "POST" and url.endswith("/sessions"):
            assert kw["json"]["consentId"] == "consent-abc"
            return {"id": "session-1", "status": "PENDING", "fips": None}
        if method == "GET" and url.endswith("/sessions/session-1"):
            self.session_polls += 1
            if self.session_polls == 1:
                return {"id": "session-1", "status": "PENDING", "fips": None}
            return {"id": "session-1", "status": "COMPLETED", "fips": [{"fipID": "HDFC-FIP", "accounts": [{
                "linkRefNumber": "link-0127", "maskedAccNumber": self.masked, "FIstatus": "READY",
                "data": {"account": {
                    "maskedAccNumber": self.masked, "type": "deposit",
                    "summary": {"currentBalance": "123500.50", "currency": "INR", "type": "SAVINGS",
                                "balanceDateTime": datetime.now(UTC).isoformat()},
                    "transactions": {"transaction": self.txns}}}}]}]}
        raise AssertionError(f"unexpected call {method} {url}")


def _drain_deferred() -> int:
    with get_engine().begin() as c:
        c.execute(text("update automation_jobs set run_after = now() where status in ('queued','retry_scheduled')"))
    return run_pending_once()


# ---- connection methods -------------------------------------------------------------------------
def test_hdfc_methods_are_reported_honestly_without_provider_setup(client, owner):
    acc = _hdfc_manual_account(client)
    r = client.get(f"/api/v1/accounts/{acc['id']}/connection-methods")
    assert r.status_code == 200, r.text
    m = {o["method"]: o for o in r.json()["methods"]}
    assert m["official_bank_api"]["available"] is False
    assert m["official_bank_api"]["status"] == "awaiting_bank_approval"
    aa = m["authorised_data_provider"]
    assert aa["provider_code"] == "india_account_aggregator"
    assert aa["available"] is False and aa["status"] == "provider_setup_required" and aa["setup_steps"]
    assert aa["capabilities"]["transactions"] is True and aa["capabilities"]["statements"] is False
    assert aa["capabilities"]["available_balance"] is False  # AA gives a "current" balance only
    assert m["permitted_browser_automation"]["available"] is False
    assert m["permitted_browser_automation"]["status"] == "unverified"
    assert m["manual_statement_import"]["available"] is True
    assert not any(o["recommended"] for o in r.json()["methods"])  # nothing automatic is available yet


def test_methods_with_provider_configured_recommend_aa(client, owner, setu_configured):
    r = client.get("/api/v1/banks/connection-methods", params={"country": "IN", "bank_name": "HDFC Bank"})
    aa = next(o for o in r.json() if o["method"] == "authorised_data_provider")
    assert aa["available"] and aa["recommended"] and aa["environment"] == "sandbox"
    assert "SANDBOX" in aa["reason"]


def test_gb_custom_bank_does_not_offer_aggregator_it_cannot_reach(client, owner):
    r = client.get("/api/v1/banks/connection-methods", params={"country": "GB", "bank_name": "Some Bank"})
    dp = next(o for o in r.json() if o["method"] == "authorised_data_provider")
    assert dp["available"] is False and dp["status"] == "unsupported"


# ---- honest health / state ---------------------------------------------------------------------
def test_manual_account_is_never_healthy_and_cannot_fake_sync(client, owner):
    acc = _hdfc_manual_account(client)
    assert acc["connection_health"] == "not_applicable"
    assert acc["automation"]["status"] == "not_configured"
    r = client.post(f"/api/v1/accounts/{acc['id']}/sync")
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_configured"


def test_migration_data_fix_resets_fake_healthy(client, owner):
    """Simulates the legacy row (healthy without any sync) and applies the migration's correction SQL."""
    acc = _hdfc_manual_account(client)
    with get_engine().begin() as c:
        c.execute(text("update bank_accounts set connection_health='healthy', last_successful_sync_at=null"))
        c.execute(text("""UPDATE bank_accounts a SET connection_health = 'not_applicable'
                          FROM bank_connections c WHERE c.id = a.connection_id
                          AND c.connection_method = 'manual_statement_import'"""))
        health = c.execute(text("select connection_health from bank_accounts where id=:i"), {"i": acc["id"]}).scalar()
    assert health == "not_applicable"


def test_state_machine_statuses():
    from app.models.banking import BankConnection, BankProvider

    def make(code="india_account_aggregator", status="active", **kw):
        prov = BankProvider(code=code, name=code, provider_type="account_aggregator")
        conn = BankConnection(status=status, consecutive_failures=kw.pop("failures", 0),
                              connection_method="authorised_data_provider")
        conn.provider = prov
        acc = BankAccount(nickname="x", automation_enabled=kw.pop("enabled", True),
                          automation_paused=kw.pop("paused", False), sync_schedule=None,
                          last_successful_sync_at=kw.pop("last_ok", None))
        acc.connection = conn
        return acc

    settings = AutomationSettings(master_enabled=True, paused=False, next_run_at=datetime.now(UTC) + timedelta(hours=3))
    real = IndiaAccountAggregatorAdapter.is_configured
    try:
        IndiaAccountAggregatorAdapter.is_configured = lambda self: True  # type: ignore[method-assign]
        assert compute_state(make(status="pending"), settings, None).status == "authorization_required"
        assert compute_state(make(status="consent_expired"), settings, None).status == "authorization_required"
        assert compute_state(make(), settings, None).status == "scheduled"
        assert compute_state(make(enabled=False), settings, None).status == "ready"
        assert compute_state(make(paused=True), settings, None).status == "paused"
        ok = make(last_ok=datetime.now(UTC) - timedelta(hours=1))
        assert compute_state(ok, settings, None).status == "healthy" and compute_health(ok) == "healthy"
        stale = make(last_ok=datetime.now(UTC) - timedelta(days=5))
        assert compute_health(stale) == "degraded"
        assert compute_state(make(failures=1, last_ok=datetime.now(UTC)), settings, None).status == "degraded"
        assert compute_state(make(failures=3), settings, None).status == "failed"
        job = AutomationJob(status="awaiting_user", error_message="OTP", attempts=0, max_attempts=4,
                            run_after=datetime.now(UTC))
        assert compute_state(make(), settings, job).status == "waiting_for_user"
        running = AutomationJob(status="running", attempts=1, max_attempts=4, run_after=datetime.now(UTC))
        assert compute_state(make(), settings, running).status == "running"
        off = AutomationSettings(master_enabled=False, paused=False)
        assert compute_state(make(), off, None).status == "ready"
        IndiaAccountAggregatorAdapter.is_configured = lambda self: False  # type: ignore[method-assign]
        assert compute_state(make(), settings, None).status == "provider_setup_required"
    finally:
        IndiaAccountAggregatorAdapter.is_configured = real  # type: ignore[method-assign]
    assert compute_state(make(code="manual_upload"), settings, None).status == "not_configured"


# ---- the HDFC journey: preserve the account, authorise via AA, sync automatically ----------------
def test_existing_hdfc_account_connects_via_aa_and_syncs(client, owner, setu_configured, monkeypatch):
    fake = FakeSetu()
    monkeypatch.setattr(india_aa, "request_json", fake)
    acc = _hdfc_manual_account(client)
    aid = acc["id"]
    old_conn = acc["connection"]["id"]

    # 1. switch the EXISTING account to the Account Aggregator (no delete / recreate)
    r = client.post(f"/api/v1/accounts/{aid}/connection-method", json={"provider_code": "india_account_aggregator"})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["id"] == aid and a["account_number_masked"].endswith("0127") and a["nickname"] == "bhushan sakhare"
    assert a["connection"]["id"] != old_conn and a["connection"]["status"] == "pending"
    assert a["automation"]["status"] == "authorization_required"
    assert a["connection_health"] == "unknown"
    assert client.post(f"/api/v1/accounts/{aid}/sync").json()["error"]["code"] == "authorization_required"

    # 2. start consent — only a mobile number, never a bank password
    r = client.post(f"/api/v1/accounts/{aid}/authorize", json={"mobile": f"+91 {MOBILE}"})
    assert r.status_code == 200, r.text
    assert r.json()["authorisation_url"].startswith("https://fiu-sandbox.setu.co/")
    assert MOBILE not in r.text

    # 3. user approves in the AA; verification asks the provider and queues the first sync
    r = client.post(f"/api/v1/accounts/{aid}/authorize/verify")
    assert r.status_code == 200, r.text
    assert r.json()["connection"]["status"] == "active"
    assert MOBILE not in r.text

    # 4. worker: session created → data not ready → deferred (not failed, not success)
    assert run_pending_once() == 1
    with session_scope() as db:
        job = db.scalar(select(AutomationJob).where(AutomationJob.account_id == uuid.UUID(aid)))
        assert job.status == "queued" and job.error_code == "data_not_ready"
        assert job.payload["adapter_state"]["aa_session_id"] == "session-1"
        acc_row = db.get(BankAccount, uuid.UUID(aid))
        assert acc_row.last_successful_sync_at is None and acc_row.connection_health == "unknown"
    _drain_deferred()  # session still pending
    _drain_deferred()  # session completed
    with session_scope() as db:
        job = db.scalar(select(AutomationJob).where(AutomationJob.account_id == uuid.UUID(aid),
                                                    AutomationJob.job_type == "collect_account"))
        assert job.status == "succeeded", (job.status, job.error_message)
        assert job.result["data_retrieved"] is True and job.result["transactions_new"] == 2
        txs = db.scalars(select(Transaction).where(Transaction.account_id == uuid.UUID(aid))
                         .order_by(Transaction.booking_date)).all()
        assert [t.amount for t in txs] == [Decimal("25000.0000"), Decimal("-1499.5000")]
        assert {t.provider_transaction_id for t in txs} == {"T1", "T2"}
        bal = db.scalars(select(AccountBalance).where(AccountBalance.account_id == uuid.UUID(aid))).all()
        assert [(b.balance_type, b.amount, b.verification_status, b.source) for b in bal] == [
            ("current", Decimal("123500.5000"), "provider_reported", "provider")]
        acc_row = db.get(BankAccount, uuid.UUID(aid))
        assert acc_row.connection_health == "healthy" and acc_row.last_successful_sync_at is not None
        assert acc_row.external_account_id == "link-0127"
    client.post("/api/v1/automation/master", json={"enabled": True})
    a = client.get(f"/api/v1/accounts/{aid}").json()
    assert a["automation"]["status"] == "healthy"
    assert a["balances"]["current"]["verification_status"] == "provider_reported"

    # 5. re-sync: idempotent — no duplicate transactions
    fake.session_polls = 0
    assert client.post(f"/api/v1/accounts/{aid}/sync").status_code == 202
    for _ in range(3):
        _drain_deferred()
    with session_scope() as db:
        assert db.scalar(select(text("count(*)")).select_from(Transaction).where(
            Transaction.account_id == uuid.UUID(aid))) == 2

    # 6. the mobile number is encrypted at rest and absent from audit logs and job logs
    with get_engine().connect() as c:
        enc = c.execute(text("select encrypted_credentials from bank_connections where external_reference='consent-abc'")
                        ).scalar()
        assert enc.startswith("v1:") and MOBILE not in enc
        assert c.execute(text("select count(*) from audit_logs where cast(meta as text) like :m "
                              "or cast(after as text) like :m"), {"m": f"%{MOBILE}%"}).scalar() == 0
        assert c.execute(text("select count(*) from automation_job_logs where cast(context as text) like :m "
                              "or message like :m"), {"m": f"%{MOBILE}%"}).scalar() == 0


def test_consent_missing_the_account_requires_user_action(client, owner, setu_configured, monkeypatch):
    monkeypatch.setattr(india_aa, "request_json", FakeSetu(masked="XXXXXXXX9999"))
    acc = _hdfc_manual_account(client)
    aid = acc["id"]
    client.post(f"/api/v1/accounts/{aid}/connection-method", json={"provider_code": "india_account_aggregator"})
    client.post(f"/api/v1/accounts/{aid}/authorize", json={"mobile": MOBILE})
    client.post(f"/api/v1/accounts/{aid}/authorize/verify")
    run_pending_once()
    _drain_deferred()
    _drain_deferred()
    with session_scope() as db:
        job = db.scalar(select(AutomationJob).where(AutomationJob.account_id == uuid.UUID(aid)))
        assert job.status == "awaiting_user" and job.requires_user_action == "consent"
        assert "0127" in job.error_message
        assert db.get(BankAccount, uuid.UUID(aid)).last_successful_sync_at is None
    assert client.get(f"/api/v1/accounts/{aid}").json()["automation"]["status"] == "waiting_for_user"


def test_pending_consent_does_not_activate(client, owner, setu_configured, monkeypatch):
    monkeypatch.setattr(india_aa, "request_json", FakeSetu(consent_status="PENDING"))
    acc = _hdfc_manual_account(client)
    aid = acc["id"]
    client.post(f"/api/v1/accounts/{aid}/connection-method", json={"provider_code": "india_account_aggregator"})
    client.post(f"/api/v1/accounts/{aid}/authorize", json={"mobile": MOBILE})
    r = client.post(f"/api/v1/accounts/{aid}/authorize/verify").json()
    assert r["connection"]["status"] == "requires_auth"
    assert r["automation"]["status"] == "authorization_required"
    with session_scope() as db:
        assert db.scalar(select(AutomationJob).where(AutomationJob.account_id == uuid.UUID(aid))) is None


def test_authorize_rejects_invalid_mobile_and_unconfigured_provider(client, owner):
    acc = _hdfc_manual_account(client)
    aid = acc["id"]
    r = client.post(f"/api/v1/accounts/{aid}/connection-method", json={"provider_code": "india_account_aggregator"})
    assert r.status_code == 200 and r.json()["automation"]["status"] == "provider_setup_required"
    r = client.post(f"/api/v1/accounts/{aid}/authorize", json={"mobile": MOBILE})
    assert r.status_code == 422 and r.json()["error"]["code"] == "provider_setup_required"


def test_unavailable_method_cannot_be_selected(client, owner):
    acc = _hdfc_manual_account(client)
    r = client.post(f"/api/v1/accounts/{acc['id']}/connection-method", json={"provider_code": "plaid"})
    assert r.status_code == 422


# ---- adapter unit tests -------------------------------------------------------------------------
def test_aa_adapter_session_lifecycle(setu_configured, monkeypatch):
    fake = FakeSetu()
    monkeypatch.setattr(india_aa, "request_json", fake)
    ad = IndiaAccountAggregatorAdapter()
    ctx = ConnectionContext(connection_id="c1", tenant_id="t", institution_external_id="x",
                            external_reference="consent-abc", account_masked_number="••••0127", account_currency="INR")
    today = datetime.now(UTC).date()
    with pytest.raises(DataNotReady) as e:
        ad.prepare_sync(ctx, today - timedelta(days=7), today)
    ctx.job_state.update(e.value.state)
    with pytest.raises(DataNotReady):
        ad.prepare_sync(ctx, today - timedelta(days=7), today)
    ad.prepare_sync(ctx, today - timedelta(days=7), today)
    assert [b.balance_type for b in ad.get_balances(ctx)] == ["current"]
    txs = ad.get_transactions(ctx, today - timedelta(days=7), today)
    assert [t.amount for t in txs] == [Decimal("25000.00"), Decimal("-1499.50")]
    assert ad.capability_matrix()["statements"] is False
    ctx2 = ConnectionContext(connection_id="c2", tenant_id="t", institution_external_id="x",
                             external_reference="consent-abc", account_masked_number="••••5555",
                             job_state={"aa_session_id": "session-1"})
    with pytest.raises(UserActionRequired):
        ad.prepare_sync(ctx2, today - timedelta(days=7), today)


# ---- automation controls / guards ----------------------------------------------------------------
def test_account_automation_actions_and_duplicate_job_guard(client, owner):
    from tests.conftest import demo_institution_id
    acc = client.post("/api/v1/accounts", json={"institution_id": demo_institution_id(client), "nickname": "Demo",
                                                "currency": "GBP"}).json()
    aid = acc["id"]
    assert acc["connection_health"] == "unknown"  # not healthy before a verified sync
    for action, (enabled, paused) in {"pause": (True, True), "resume": (True, False), "disable": (False, False),
                                      "enable": (True, False)}.items():
        r = client.post(f"/api/v1/accounts/{aid}/automation/action", json={"action": action})
        assert r.status_code == 200, r.text
        assert (r.json()["automation_enabled"], r.json()["automation_paused"]) == (enabled, paused)
    first = client.post(f"/api/v1/accounts/{aid}/sync").json()
    second = client.post(f"/api/v1/accounts/{aid}/sync").json()
    assert first["created"] is True and second["created"] is False and first["job_id"] == second["job_id"]
    with session_scope() as db:
        tid = db.scalar(text("select tenant_id from users limit 1"))
        job, created = queue.enqueue(db, tenant_id=tid, job_type="collect_account", account_id=uuid.UUID(aid),
                                     idempotency_key="another-key")
        assert created is False and str(job.id) == first["job_id"]
    run_pending_once()
    hist = client.get(f"/api/v1/accounts/{aid}/sync-history").json()
    assert hist["total"] >= 1 and hist["items"][0]["status"] == "succeeded"


def test_paused_account_is_skipped_by_schedule(client, owner):
    from app.scheduler import scheduler
    from tests.conftest import demo_institution_id
    acc = client.post("/api/v1/accounts", json={"institution_id": demo_institution_id(client), "nickname": "Demo",
                                                "currency": "GBP"}).json()
    client.post(f"/api/v1/accounts/{acc['id']}/automation/action", json={"action": "pause"})
    client.post("/api/v1/automation/master", json={"enabled": True})
    with session_scope() as db:
        db.execute(update(AutomationSettings).values(next_run_at=datetime.now(UTC) - timedelta(minutes=1)))
    scheduler.tick()
    with session_scope() as db:
        n = db.scalar(select(text("count(*)")).select_from(AutomationJob)
                      .where(AutomationJob.account_id == uuid.UUID(acc["id"])))
    assert n == 0


def test_per_account_schedule_validation(client, owner):
    from tests.conftest import demo_institution_id
    acc = client.post("/api/v1/accounts", json={"institution_id": demo_institution_id(client), "nickname": "Demo",
                                                "currency": "GBP"}).json()
    assert client.patch(f"/api/v1/accounts/{acc['id']}", json={"sync_schedule": "not a cron"}).status_code == 422
    r = client.patch(f"/api/v1/accounts/{acc['id']}", json={"sync_schedule": "0 */6 * * *",
                                                           "timezone": "Asia/Kolkata"})
    assert r.status_code == 200 and r.json()["sync_schedule"] == "0 */6 * * *"
    assert client.patch(f"/api/v1/accounts/{acc['id']}", json={"timezone": "Mars/Base"}).status_code == 422


# ---- webhook & isolation --------------------------------------------------------------------------
def test_webhook_disabled_without_token(client):
    assert client.post("/api/v1/webhooks/aa/setu", json={"consentId": "x"}).status_code == 404


def test_webhook_requires_token(client, setu_configured):
    assert client.post("/api/v1/webhooks/aa/setu?token=wrong", json={"consentId": "x"}).status_code == 401
    r = client.post("/api/v1/webhooks/aa/setu?token=hook-token-123", json={"consentId": "unknown"})
    assert r.status_code == 200


def test_connection_endpoints_are_tenant_isolated(client, owner):
    from app.core.security import hash_password
    from app.models.identity import Organization, User
    from app.services.auth import login as svc_login
    from app.services.seed import bootstrap_tenant, system_role

    acc = _hdfc_manual_account(client)
    with session_scope() as db:
        org = Organization(name="Other Co", slug="other-co")
        db.add(org)
        db.flush()
        db.add(User(tenant_id=org.id, email="intruder@example.com", full_name="In Truder",
                    password_hash=hash_password(PASSWORD), role_id=system_role(db, "owner").id))
        bootstrap_tenant(db, org.id)
    with session_scope() as db:
        tok = svc_login(db, email="intruder@example.com", password=PASSWORD, ip=None, ua=None).access_token
    h = {"Authorization": f"Bearer {tok}"}
    aid = acc["id"]
    assert client.get(f"/api/v1/accounts/{aid}/connection-methods", headers=h).status_code == 404
    assert client.post(f"/api/v1/accounts/{aid}/connection-method", headers=h,
                       json={"provider_code": "manual_upload"}).status_code == 404
    assert client.post(f"/api/v1/accounts/{aid}/authorize", headers=h, json={"mobile": MOBILE}).status_code == 404
    assert client.post(f"/api/v1/accounts/{aid}/sync", headers=h).status_code == 404
    assert client.post(f"/api/v1/accounts/{aid}/automation/action", headers=h,
                       json={"action": "pause"}).status_code == 404
    assert client.get(f"/api/v1/accounts/{aid}/sync-history", headers=h).status_code == 404
