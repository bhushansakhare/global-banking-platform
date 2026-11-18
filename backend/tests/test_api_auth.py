"""Authentication, session rotation, CSRF, RBAC and tenant isolation."""
from __future__ import annotations

from sqlalchemy import select, text

from app.database.session import get_engine, session_scope
from app.models.identity import User
from tests.conftest import PASSWORD, demo_institution_id, setup_owner


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("gbp_csrf")}


def test_setup_once_then_login(client):
    assert client.get("/api/v1/auth/setup-status").json()["setup_required"] is True
    setup_owner(client)
    assert client.get("/api/v1/auth/setup-status").json()["setup_required"] is False
    again = client.post("/api/v1/auth/setup", json={"organisation": "Xyz Ltd", "full_name": "Y Z", "email": "x@example.com",
                                                    "password": PASSWORD, "reporting_currency": "GBP"})
    assert again.status_code == 409
    r = client.post("/api/v1/auth/login", json={"email": "OWNER@example.com", "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["role"] == "owner" and body["expires_in"] == 900
    assert "gbp_rt" in client.cookies and "password_hash" not in str(body)


def test_weak_password_rejected(client):
    r = client.post("/api/v1/auth/setup", json={"organisation": "Acme", "full_name": "A B", "email": "a@example.com",
                                                "password": "weakpassword", "reporting_currency": "GBP"})
    assert r.status_code == 422


def test_failed_login_lockout_and_audit(client):
    setup_owner(client)
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": "Wrong-Pass-123"}
                           ).status_code == 401
    r = client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    assert r.status_code == 401 and r.json()["error"]["code"] == "account_locked"
    with get_engine().connect() as c:
        n = c.execute(text("select count(*) from audit_logs where action='auth.login' and outcome='failure'")).scalar()
    assert n >= 6


def test_unknown_user_same_error(client):
    setup_owner(client)
    r = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"


def test_refresh_rotation_and_reuse_detection(client):
    setup_owner(client)
    old_rt = client.cookies.get("gbp_rt")
    r = client.post("/api/v1/auth/refresh", headers=_csrf(client))
    assert r.status_code == 200
    new_rt = client.cookies.get("gbp_rt")
    assert new_rt != old_rt
    # replay the old (rotated) token → whole family revoked
    client.cookies.set("gbp_rt", old_rt, path="/api/v1/auth")
    r = client.post("/api/v1/auth/refresh", headers=_csrf(client))
    assert r.status_code == 401 and r.json()["error"]["code"] == "session_revoked"
    client.cookies.set("gbp_rt", new_rt, path="/api/v1/auth")
    assert client.post("/api/v1/auth/refresh", headers=_csrf(client)).status_code == 401


def test_refresh_requires_csrf(client):
    setup_owner(client)
    assert client.post("/api/v1/auth/refresh").status_code == 403
    assert client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": "forged"}).status_code == 403


def test_logout_revokes_access(client):
    data = setup_owner(client)
    h = {"Authorization": f"Bearer {data['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=h).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=_csrf(client)).status_code == 204
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401


def test_requires_authentication(client):
    for path in ("/api/v1/accounts", "/api/v1/transactions", "/api/v1/analytics/summary", "/api/v1/audit-logs"):
        assert client.get(path).status_code == 401
    assert client.get("/api/v1/accounts", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_rbac_viewer_cannot_write(client, owner):
    r = client.post("/api/v1/users", json={"email": "viewer@example.com", "full_name": "Vera Viewer",
                                           "role": "viewer", "password": PASSWORD})
    assert r.status_code == 201
    login = client.post("/api/v1/auth/login", json={"email": "viewer@example.com", "password": PASSWORD}).json()
    h = {"Authorization": f"Bearer {login['access_token']}"}
    assert client.get("/api/v1/accounts", headers=h).status_code == 200
    inst = demo_institution_id(client)
    r = client.post("/api/v1/accounts", headers=h, json={"institution_id": inst, "nickname": "X", "currency": "GBP"})
    assert r.status_code == 403
    assert client.post("/api/v1/automation/master", headers=h, json={"enabled": True}).status_code == 403
    assert client.get("/api/v1/audit-logs", headers=h).status_code == 403
    assert client.get("/api/v1/users", headers=h).status_code == 403


def test_tenant_isolation(client, owner):
    inst = demo_institution_id(client)
    acc = client.post("/api/v1/accounts", json={"institution_id": inst, "nickname": "Ops", "currency": "GBP",
                                                "account_number": "12345678"}).json()
    # create a second, unrelated organisation directly
    from app.services.auth import login as svc_login
    from app.models.identity import Organization
    from app.core.security import hash_password
    from app.services.seed import bootstrap_tenant, system_role
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
    assert client.get(f"/api/v1/accounts/{acc['id']}", headers=h).status_code == 404
    assert client.get("/api/v1/accounts", headers=h).json() == []
    assert client.post("/api/v1/automation/run-now", headers=h, json={"account_ids": [acc["id"]]}).status_code == 404
    assert client.get("/api/v1/transactions", headers=h).json()["total"] == 0


def test_account_number_never_exposed(client, owner):
    inst = demo_institution_id(client)
    r = client.post("/api/v1/accounts", json={"institution_id": inst, "nickname": "Ops", "currency": "GBP",
                                              "account_number": "GB29NWBK60161331926819"})
    assert r.status_code == 201
    assert "60161331926819" not in r.text and r.json()["account_number_masked"] == "••••6819"
    with get_engine().connect() as c:
        stored = c.execute(text("select account_number_encrypted from bank_accounts")).scalar()
    assert stored.startswith("v1:") and "6016" not in stored
    dup = client.post("/api/v1/accounts", json={"institution_id": inst, "nickname": "Dup", "currency": "GBP",
                                                "account_number": "GB29NWBK60161331926819"})
    assert dup.status_code == 409


def test_audit_log_is_append_only(client, owner):
    import pytest
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError):
        with get_engine().begin() as c:
            c.execute(text("update audit_logs set action='tampered'"))
    with pytest.raises(DBAPIError):
        with get_engine().begin() as c:
            c.execute(text("delete from audit_logs"))
    with session_scope() as db:
        assert db.scalar(select(User.email)) == "owner@example.com"
