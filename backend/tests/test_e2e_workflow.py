"""End-to-end: user → sign-in → institution → account → automation → collection → statements →
transactions → balances → reconciliation → report → audit history. Uses the clearly labelled demo adapter."""
from __future__ import annotations

import csv
import io

from tests.conftest import PASSWORD, demo_institution_id, setup_owner
from app.workers.worker import run_pending_once


def test_full_workflow(client):
    # 1–2 create user and sign in
    setup_owner(client)
    login = client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": PASSWORD}).json()
    client.headers["Authorization"] = f"Bearer {login['access_token']}"

    # 3 add a bank institution (custom, statement-upload) and see the demo bank
    custom = client.post("/api/v1/institutions", json={"name": "Barclays (upload)", "country_code": "GB"})
    assert custom.status_code == 201 and custom.json()["provider"]["code"] == "manual_upload"
    inst = demo_institution_id(client)

    # 4 add an account via the demo adapter
    acc = client.post("/api/v1/accounts", json={"institution_id": inst, "nickname": "Northwind Operating",
                                                "currency": "GBP", "low_balance_threshold": "1000000"}).json()
    assert acc["is_demo"] is True and acc["connection"]["status"] == "active"

    # book entries so reconciliation has a book side
    rows = client.get("/api/v1/accounts").json()
    assert len(rows) == 1

    # 5–6 configure and enable automation
    assert client.put("/api/v1/automation/settings", json={"daily_time": "06:00", "lookback_days": 30,
                                                           "auto_reconcile": True}).status_code == 200
    assert client.post("/api/v1/automation/master", json={"enabled": True}).json()["master_enabled"] is True

    # 7 execute a collection job (run now) and drain the pipeline
    run = client.post("/api/v1/automation/run-now", json={}).json()
    assert run["jobs_created"] == 1
    processed = run_pending_once(500)
    assert processed >= 3
    jobs = client.get("/api/v1/jobs", params={"run_id": run["run_id"], "page_size": 200}).json()["items"]
    assert all(j["status"] == "succeeded" for j in jobs), [(j["job_type"], j["status"], j["error_message"])
                                                           for j in jobs]
    types = {j["job_type"] for j in jobs}
    assert {"collect_account", "parse_statement", "reconcile_account", "daily_report"} <= types

    # 8–9 statements retrieved and transactions extracted
    sts = client.get("/api/v1/statements", params={"account_id": acc["id"]}).json()
    assert sts["total"] >= 1
    st = sts["items"][0]
    assert st["source"] == "demo" and st["is_demo"] is True
    assert st["processing_status"] in ("processed", "processed_with_warnings")
    assert st["arithmetic_valid"] is True
    detail = client.get(f"/api/v1/statements/{st['id']}").json()
    assert detail["parsing_jobs"][0]["status"] == "succeeded"
    txs = client.get("/api/v1/transactions", params={"account_id": acc["id"], "page_size": 10}).json()
    assert txs["total"] > 10 and all(t["is_demo"] for t in txs["items"])

    # 10 balance snapshots – kept separate per type
    bal = client.get("/api/v1/balances").json()[0]["balances"]
    assert {"available", "ledger", "statement_closing"} <= set(bal)
    assert bal["statement_closing"]["source"] == "statement" and bal["ledger"]["source"] == "demo"

    # duplicate statement upload is detected
    raw = client.get(f"/api/v1/statements/{st['id']}/file")
    assert raw.status_code == 200 and b"DEMO STATEMENT" in raw.content
    dup = client.post("/api/v1/statements/upload", data={"account_id": acc["id"]},
                      files={"file": ("again.csv", raw.content, "text/csv")})
    assert dup.status_code == 201 and dup.json()["duplicate"] is True

    # invalid statement is rejected
    bad = client.post("/api/v1/statements/upload", data={"account_id": acc["id"]},
                      files={"file": ("x.png", b"\x89PNG\r\n\x1a\nabc", "image/png")})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_statement"

    # 11 reconciliation: import a cash-book then run against the statement
    txs_all = client.get("/api/v1/transactions", params={"statement_id": st["id"], "page_size": 500}).json()["items"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Description", "Reference", "Amount"])
    for i, t in enumerate(txs_all):
        if i == 0:
            continue  # leave one bank item unmatched
        w.writerow([t["booking_date"], t["description"], t["reference"], t["amount"]])
    w.writerow([txs_all[0]["booking_date"], "Cheque not yet presented", "CHQ1", "-123.45"])
    imp = client.post("/api/v1/book-entries/import", data={"account_id": acc["id"]},
                      files={"file": ("book.csv", buf.getvalue().encode(), "text/csv")})
    assert imp.status_code == 200 and imp.json()["created"] == len(txs_all)
    rec = client.post("/api/v1/reconciliation/run", json={"account_id": acc["id"], "statement_id": st["id"]})
    assert rec.status_code == 201
    s = rec.json()
    assert s["matched_count"] == len(txs_all) - 1
    assert s["unmatched_bank_count"] == 1 and s["unmatched_book_count"] == 1
    assert s["balance_difference"] == "0.00" and s["status"] == "completed_with_exceptions"
    sess = client.get(f"/api/v1/reconciliation/sessions/{s['id']}").json()
    exc = next(e for e in sess["exceptions"] if e["exception_type"] == "unmatched_book")
    r = client.patch(f"/api/v1/reconciliation/exceptions/{exc['id']}",
                     json={"status": "resolved", "note": "Cheque will clear next month"})
    assert r.status_code == 200 and r.json()["status"] == "resolved"

    # 12 generate and download a report (each format)
    for fmt in ("xlsx", "csv", "pdf"):
        rep = client.post("/api/v1/reports", json={"report_type": "reconciliation", "file_format": fmt})
        assert rep.status_code == 201 and rep.json()["contains_demo_data"] is True
        dl = client.get(f"/api/v1/reports/{rep.json()['id']}/download")
        assert dl.status_code == 200 and len(dl.content) > 200
    exp = client.get("/api/v1/transactions/export", params={"fmt": "csv", "account_id": acc["id"]})
    assert exp.status_code == 200 and b"CONTAINS DEMO DATA" in exp.content

    # dashboard / analytics
    summ = client.get("/api/v1/analytics/summary").json()
    assert summ["total_accounts"] == 1 and summ["demo_accounts"] == 1
    assert summ["balances"]["ledger"]["total_reporting"] is not None
    for path in ("balance-history", "cash-flow", "monthly", "distributions", "reconciliation-performance"):
        assert client.get(f"/api/v1/analytics/{path}").status_code == 200

    # notifications were raised (low balance threshold set very high)
    notes = client.get("/api/v1/notifications").json()
    kinds = {n["type"] for n in notes["items"]}
    assert {"low_balance", "sync_success", "report_ready"} <= kinds

    # 13 audit history
    audit = client.get("/api/v1/audit-logs", params={"page_size": 200}).json()["items"]
    actions = {a["action"] for a in audit}
    assert {"setup.complete", "auth.login", "account.create", "automation.master.on", "automation.run_now",
            "reconciliation.run", "reconciliation.exception.update", "report.generate", "statement.upload"} <= actions


def test_statement_upload_pipeline_manual_bank(client, owner):
    inst = client.post("/api/v1/institutions", json={"name": "HDFC Bank (upload)", "country_code": "IN"}).json()
    acc = client.post("/api/v1/accounts", json={"institution_id": inst["id"], "nickname": "HDFC Current",
                                                "currency": "INR"}).json()
    assert acc["is_demo"] is False
    content = ("Txn Date,Narration,Chq./Ref.No.,Withdrawal Amt.,Deposit Amt.,Closing Balance\n"
               "03/01/2025,NEFT CR CUSTOMER A,N123,,\"50,000.00\",\"1,50,000.00\"\n"
               "04/01/2025,RENT JAN,R77,\"20,000.00\",,\"1,30,000.00\"\n").encode()
    r = client.post("/api/v1/statements/upload", data={"account_id": acc["id"]},
                    files={"file": ("hdfc.csv", content, "text/csv")})
    assert r.status_code == 201, r.text
    st = r.json()["statement"]
    assert st["transaction_count"] == 2 and st["opening_balance"] == "100000.00"
    assert st["closing_balance"] == "130000.00" and st["arithmetic_valid"] is True and st["is_demo"] is False
    txs = client.get("/api/v1/transactions", params={"account_id": acc["id"]}).json()["items"]
    cats = {t["description"]: (t["category"] or {}).get("name") for t in txs}
    assert cats["RENT JAN"] == "Rent & premises"
    # manual-upload accounts never fabricate balances from a provider
    bal = client.get("/api/v1/balances").json()[0]["balances"]
    assert set(bal) == {"statement_opening", "statement_closing"}


def test_duplicate_transactions_flagged(client, owner):
    inst = client.post("/api/v1/institutions", json={"name": "Test Bank", "country_code": "GB"}).json()
    acc = client.post("/api/v1/accounts", json={"institution_id": inst["id"], "nickname": "T", "currency": "GBP"}).json()
    a = b"Date,Description,Reference,Amount,Balance\n01/02/2025,Payment ABC,REF9,-10.00,90.00\n"
    b = b"Date,Description,Reference,Amount,Balance\n01/02/2025,PAYMENT ABC LTD,REF9,-10.00,80.00\n"
    client.post("/api/v1/statements/upload", data={"account_id": acc["id"]}, files={"file": ("a.csv", a, "text/csv")})
    client.post("/api/v1/statements/upload", data={"account_id": acc["id"]}, files={"file": ("b.csv", b, "text/csv")})
    dup = client.get("/api/v1/transactions", params={"account_id": acc["id"], "duplicate_suspect": True}).json()
    assert dup["total"] == 1
    assert any(n["type"] == "duplicate_transaction" for n in client.get("/api/v1/notifications").json()["items"])


def test_demo_environment_seed_and_multi_currency(client, owner):
    r = client.post("/api/v1/settings/demo/seed")
    assert r.status_code == 200 and r.json()["accounts_created"] == 10
    accs = client.get("/api/v1/accounts").json()
    assert len(accs) == 10 and all(a["is_demo"] for a in accs)
    assert {a["currency"] for a in accs} == {"GBP", "INR", "USD", "EUR", "AED"}
    client.post("/api/v1/automation/run-now", json={})
    run_pending_once(1000)
    summ = client.get("/api/v1/analytics/summary").json()
    assert summ["fx_missing"] == [] and len(summ["fx_rates_used"]) == 4
    assert summ["reported_balance"]["unconverted_currencies"] == []
    sessions = client.get("/api/v1/reconciliation/sessions").json()
    assert sessions["total"] >= 5
    assert client.post("/api/v1/settings/demo/clear").json()["accounts_removed"] == 10
    assert client.get("/api/v1/accounts").json() == []
