"""Only synthetic bank profiles/pages/files. Never contacts a real banking account."""
import socket
import uuid
from datetime import date
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.automation import browser_jobs
from app.core.config import get_settings
from app.core.crypto import decrypt_json
from app.database.session import session_scope
from app.integrations.banks.browser_engine import ProfileReviewRequired, authenticated, confirm_account
from app.integrations.banks.profiles import BrowserProfile, HDFC, find_profile, validate_url
from app.models.automation import AutomationJob
from app.models.banking import BankAccount
from app.models.system import SystemSetting
from app.models.transactions import Transaction
from app.workers.worker import run_pending_once
from tests.test_automation_first import _hdfc_manual_account


def permitted_profile():
    return {"key": "example_bank", "name": "Example Bank", "country": "IN", "currencies": ["INR"],
            "website": "https://bank.example/", "login_url": "https://bank.example/login",
            "domains": ["bank.example"], "official_reference": "https://bank.example/help",
            "permission": "permitted", "permission_reference": "https://bank.example/terms",
            "reviewed": True, "customer_id_selector": "#customer", "continue_selector": "#continue",
            "authenticated_selector": "#signed-in", "authenticated_path": "/accounts",
            "account_selector": "#account", "download_selector": "#download", "formats": ["csv"],
            "steps": [{"action": "date_from", "selector": "#from"}, {"action": "date_to", "selector": "#to"}]}


@pytest.fixture
def browser_enabled(monkeypatch):
    monkeypatch.setenv("BROWSER_AUTOMATION_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("url", ["http://bank.example/", "https://bank.example.evil.test/",
    "https://bank.example@evil.test/", "https://bank.example:8443/", "javascript:alert(1)",
    "https://bank.example/login?state=temporary", "https://bank.example/login?code_challenge=temporary"])
def test_reject_unreviewed_login_destinations(url):
    with pytest.raises(ValueError):
        validate_url(url, ["bank.example"])


def test_private_dns_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="public"):
        validate_url("https://bank.example/", ["bank.example"], resolve=True)


def test_profiles_require_review_permission_and_account_identity():
    body = permitted_profile()
    body["permission_reference"] = None
    with pytest.raises(ValidationError):
        BrowserProfile.model_validate(body)
    body = permitted_profile()
    body["account_selector"] = None
    with pytest.raises(ValidationError):
        BrowserProfile.model_validate(body)
    assert HDFC.retrieval_supported is False
    assert HDFC.public()["capabilities"]["browser_login"] == "unverified"


@pytest.mark.parametrize("content,name,formats", [
    (b"<html>Login,OTP</html>", "statement.csv", ["csv"]),
    (b"Date,Description\n", "statement.csv", ["csv"]),
    (b"%PDF-1.4\ntruncated", "statement.pdf", ["pdf"]),
    (b"PK\x03\x04truncated", "statement.xlsx", ["xlsx"]),
])
def test_corrupt_and_login_documents_rejected(content, name, formats):
    from app.integrations.banks.browser_engine import validate_download
    with pytest.raises(ProfileReviewRequired):
        validate_download(content, name, formats)


def test_redirect_to_unknown_domain_is_blocked_before_following(monkeypatch):
    from app.integrations.banks import browser_engine
    validate = browser_engine.validate_url
    monkeypatch.setattr(browser_engine, "validate_url", lambda url, hosts, **kw: validate(url, hosts))
    route = Mock()
    route.request.url = "https://bank.example/login"
    route.request.is_navigation_request.return_value = True
    route.request.method = "GET"
    route.fetch.return_value.status = 302
    route.fetch.return_value.headers = {"location": "https://evil.example/steal"}
    blocked = [False]
    browser_engine.guard_request(route, BrowserProfile.model_validate(permitted_profile()), blocked)
    assert blocked == [True]
    route.abort.assert_called_once()
    route.fulfill.assert_not_called()
    route.fetch.assert_called_once_with(max_redirects=0, timeout=45000)


def test_verified_redirect_starts_a_separately_guarded_navigation(monkeypatch):
    from app.integrations.banks import browser_engine
    validate = browser_engine.validate_url
    monkeypatch.setattr(browser_engine, "validate_url", lambda url, hosts, **kw: validate(url, hosts))
    route = Mock()
    route.request.url = "https://bank.example/login"
    route.request.is_navigation_request.return_value = True
    route.request.method = "GET"
    route.fetch.return_value.status = 302
    route.fetch.return_value.headers = {"location": "/accounts"}
    blocked = [False]
    browser_engine.guard_request(route, BrowserProfile.model_validate(permitted_profile()), blocked)
    assert blocked == [False]
    assert "https://bank.example/accounts" in route.fulfill.call_args.kwargs["body"]
    route.abort.assert_not_called()


def test_authentication_requires_positive_marker_and_exact_path():
    page = Mock(url="https://bank.example/accounts")
    page.locator.return_value.count.return_value = 1
    page.locator.return_value.is_visible.return_value = True
    profile = BrowserProfile.model_validate(permitted_profile())
    assert authenticated(page, profile)
    page.url = "https://bank.example/login"
    assert not authenticated(page, profile)
    page.url = "https://bank.example/accounts"
    page.locator.return_value.is_visible.return_value = False
    assert not authenticated(page, profile)


def test_reviewed_hdfc_customer_id_field_can_be_filled_without_claiming_retrieval():
    from app.integrations.banks.browser_engine import autofill_customer_id
    page = Mock()
    field = page.locator.return_value
    field.count.return_value = 1
    field.get_attribute.side_effect = lambda name: {"type": "text", "autocomplete": "off"}.get(name)
    autofill_customer_id(page, HDFC, "SYNTHETIC-CUSTOMER")
    field.wait_for.assert_called_once_with(state="visible", timeout=45000)
    field.fill.assert_called_once_with("SYNTHETIC-CUSTOMER")
    field.input_value.assert_not_called()
    assert HDFC.retrieval_supported is False


def test_password_bearing_form_is_never_auto_submitted():
    from app.integrations.banks.browser_engine import autofill_customer_id
    profile = BrowserProfile.model_validate({**HDFC.model_dump(), "continue_selector": "#kc-login"})
    field, password, submit = Mock(), Mock(), Mock()
    field.count.return_value = password.count.return_value = 1
    field.get_attribute.return_value = "text"
    page = Mock()
    page.locator.side_effect = lambda selector: {"#username": field, 'input[type="password"]:visible': password,
                                               "#kc-login": submit}[selector]
    autofill_customer_id(page, profile, "SYNTHETIC-CUSTOMER")
    field.fill.assert_called_once()
    submit.click.assert_not_called()
    password.input_value.assert_not_called()


def test_unreviewed_identifier_field_is_not_filled():
    from app.integrations.banks.browser_engine import autofill_customer_id
    profile = BrowserProfile.model_validate({**HDFC.model_dump(), "login_fields_reviewed": False})
    page = Mock()
    autofill_customer_id(page, profile, "SYNTHETIC-CUSTOMER")
    page.locator.assert_not_called()


def test_wrong_bank_account_is_rejected_before_download():
    page = Mock()
    page.locator.return_value.count.return_value = 1
    page.locator.return_value.is_visible.return_value = True
    page.locator.return_value.inner_text.return_value = "Savings XXXX9999"
    with pytest.raises(ProfileReviewRequired):
        confirm_account(page, BrowserProfile.model_validate(permitted_profile()), "XXXX0127")


def test_profile_registration_is_dynamic_and_tenant_scoped(client, owner):
    body = permitted_profile()
    r = client.put("/api/v1/banks/browser-profiles/example_bank", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["capabilities"]["automatic_statement_retrieval"] is True
    found = client.get("/api/v1/banks/browser-profiles", params={"country": "IN", "q": "bank.example"}).json()
    assert [p["key"] for p in found] == ["example_bank"]
    with session_scope() as db:
        tenant = db.scalar(select(SystemSetting.tenant_id).where(SystemSetting.key == "browser_profile:example_bank"))
        assert find_profile(db, tenant, "example_bank").retrieval_supported
        with pytest.raises(StopIteration):
            find_profile(db, uuid.uuid4(), "example_bank")


def test_hdfc_login_only_preserves_existing_account(client, owner, browser_enabled):
    account = _hdfc_manual_account(client)
    r = client.post(f"/api/v1/accounts/{account['id']}/browser/start", json={"profile_key": "hdfc_bank",
                    "customer_id": "CUSTOMER-123", "login_only": True})
    assert r.status_code == 202, r.text
    second = client.post(f"/api/v1/accounts/{account['id']}/browser/start", json={"profile_key": "hdfc_bank", "login_only": True})
    assert second.json()["job_id"] == r.json()["job_id"]
    current = client.get(f"/api/v1/accounts/{account['id']}").json()
    assert current["connection"]["id"] == account["connection"]["id"]
    assert current["last_successful_sync_at"] is None
    with session_scope() as db:
        acc = db.get(BankAccount, uuid.UUID(account["id"]))
        assert "CUSTOMER-123" not in acc.browser_identifier_encrypted
        assert decrypt_json(acc.browser_identifier_encrypted, str(acc.tenant_id))["customer_id"] == "CUSTOMER-123"
        job = db.get(AutomationJob, uuid.UUID(r.json()["job_id"]))
        assert "CUSTOMER-123" not in str(job.payload)
    blocked = client.post(f"/api/v1/accounts/{account['id']}/browser/start", json={"profile_key": "hdfc_bank"})
    assert blocked.status_code == 409
    assert client.get(f"/api/v1/accounts/{account['id']}/browser").json()["has_customer_id"] is True


CSV = b"Date,Description,Debit,Credit,Balance\n2026-11-01,Opening,0,0,1000\n2026-11-02,Salary,0,250,1250\n"


def configure_account(client):
    account = _hdfc_manual_account(client)
    assert client.put("/api/v1/banks/browser-profiles/example_bank", json=permitted_profile()).status_code == 200
    r = client.post(f"/api/v1/accounts/{account['id']}/browser/start", json={"profile_key": "example_bank",
                    "customer_id": "CUSTOMER-123", "date_from": "2026-11-01", "date_to": "2026-11-02"})
    assert r.status_code == 202, r.text
    return account, r.json()["job_id"]


def test_download_processing_and_duplicate_import(client, owner, browser_enabled, monkeypatch):
    seen = []
    def fake_retrieve(profile, **kw):
        seen.append(kw)
        return [("statement.csv", CSV)]
    monkeypatch.setattr(browser_jobs, "retrieve", fake_retrieve)
    account, job_id = configure_account(client)
    run_pending_once()
    with session_scope() as db:
        job = db.get(AutomationJob, uuid.UUID(job_id))
        assert job.status == "succeeded", job.error_message
        assert job.result["statements_new"] == 1
        assert db.scalar(select(Transaction).where(Transaction.account_id == uuid.UUID(account["id"]))) is not None
        acc = db.get(BankAccount, uuid.UUID(account["id"]))
        assert acc.last_successful_sync_at is not None
        assert acc.connection.status == "requires_auth"
    assert seen[0]["customer_id"] == "CUSTOMER-123"
    assert seen[0]["masked_number"].endswith("0127")
    again = client.post(f"/api/v1/accounts/{account['id']}/browser/start", json={"profile_key": "example_bank"})
    assert again.status_code == 202
    run_pending_once()
    with session_scope() as db:
        job = db.get(AutomationJob, uuid.UUID(again.json()["job_id"]))
        assert job.result["statements_duplicate"] == 1


def test_corrupt_download_never_records_sync(client, owner, browser_enabled, monkeypatch):
    monkeypatch.setattr(browser_jobs, "retrieve", lambda *a, **kw: [("statement.pdf", b"<html>Login required</html>")])
    account, job_id = configure_account(client)
    run_pending_once()
    with session_scope() as db:
        assert db.get(AutomationJob, uuid.UUID(job_id)).status == "failed"
        assert db.get(BankAccount, uuid.UUID(account["id"])).last_successful_sync_at is None


def test_scheduler_waits_once_then_user_resume_opens_browser(client, owner, browser_enabled, monkeypatch):
    account, job_id = configure_account(client)
    with session_scope() as db:
        job = db.get(AutomationJob, uuid.UUID(job_id))
        job.trigger = "schedule"
    fake = Mock(return_value=[("statement.csv", CSV)])
    monkeypatch.setattr(browser_jobs, "retrieve", fake)
    run_pending_once()
    assert not fake.called
    with session_scope() as db:
        job = db.get(AutomationJob, uuid.UUID(job_id))
        assert job.status == "awaiting_user"
    run_pending_once()
    assert not fake.called
    resumed = client.post(f"/api/v1/accounts/{account['id']}/browser/start", json={"profile_key": "example_bank"})
    assert resumed.status_code == 202
    assert resumed.json()["job_id"] == job_id
    run_pending_once()
    assert fake.call_count == 1


def test_profile_removal_blocks_existing_connection(client, owner, browser_enabled):
    account, job_id = configure_account(client)
    with session_scope() as db:
        row = db.scalar(select(SystemSetting).where(SystemSetting.key == "browser_profile:example_bank"))
        db.delete(row)
    run_pending_once()
    with session_scope() as db:
        assert db.get(AutomationJob, uuid.UUID(job_id)).status == "failed"
        assert db.get(BankAccount, uuid.UUID(account["id"])).last_successful_sync_at is None


def test_page_changes_disable_retrieval_until_profile_review(client, owner, browser_enabled, monkeypatch):
    def changed_page(*a, **kw):
        raise ProfileReviewRequired("Statement navigation changed; profile review required")
    monkeypatch.setattr(browser_jobs, "retrieve", changed_page)
    account, job_id = configure_account(client)
    run_pending_once()
    with session_scope() as db:
        job = db.get(AutomationJob, uuid.UUID(job_id))
        assert job.status == "failed" and job.attempts == 1
        profile = find_profile(db, job.tenant_id, "example_bank")
        assert profile.review_required and not profile.retrieval_supported
    blocked = client.post(f"/api/v1/accounts/{account['id']}/browser/start", json={"profile_key": "example_bank"})
    assert blocked.status_code == 409


@pytest.mark.parametrize("bank_denied,saved_login", [(False, False), (True, False), (False, True)])
def test_real_playwright_engine_on_mocked_bank_pages(browser_enabled, monkeypatch, bank_denied, saved_login):
    """Real Edge/Chromium, synthetic HTML and CSV only; every HTTP request is fulfilled in process."""
    from contextlib import contextmanager
    from urllib.parse import urlsplit
    import playwright.sync_api as playwright
    from app.integrations.banks import browser_engine

    real_factory = playwright.sync_playwright
    real_validate = browser_engine.validate_url
    monkeypatch.setattr(browser_engine, "validate_url", lambda url, hosts, **kw: real_validate(url, hosts))
    observed = []

    class RouteProxy:
        def __init__(self, route):
            self._route, self.request = route, route.request

        def abort(self):
            self._route.abort()

        def fetch(self, **kw):
            from types import SimpleNamespace
            path = urlsplit(self.request.url).path
            observed.append(path)
            if path == "/login":
                if bank_denied:
                    return SimpleNamespace(status=403, body="<title>403 Forbidden</title>403 Forbidden",
                                           headers={"Content-Type": "text/html"}, dispose=lambda: None)
                body = '<input id="customer"><button id="continue" onclick="location.href=\'/accounts\'">Continue</button>'
                if saved_login:
                    body += '<input id="bank-password" type="password">'
                    import json
                    body = '<script>setTimeout(() => {document.body.innerHTML = ' + json.dumps(body) + '}, 300)</script>'
            elif path == "/accounts":
                body = ('<span id="signed-in">Signed in</span><span id="account">Savings XXXX0127</span>'
                        '<input id="from"><input id="to"><a id="download" href="/statement">Download</a>')
            elif path == "/statement":
                return SimpleNamespace(status=200, body=CSV, headers={"Content-Type": "text/csv",
                    "Content-Disposition": 'attachment; filename="statement.csv"'}, dispose=lambda: None)
            else:
                body = "Not found"
            return SimpleNamespace(status=200, body=body, headers={"Content-Type": "text/html"}, dispose=lambda: None)

        def fulfill(self, response=None, **kw):
            if response is not None:
                self._route.fulfill(status=response.status, body=response.body, headers=response.headers)
            else:
                self._route.fulfill(**kw)

    class ContextProxy:
        def __init__(self, context):
            self.context = context

        def route(self, pattern, handler):
            self.context.route(pattern, lambda route: handler(RouteProxy(route)))

        def new_cdp_session(self, page):
            context = self
            class MockTransportSession:
                callback = None
                current = None

                def on(self, name, callback):
                    self.callback = callback

                def send(self, method, params):
                    if method == "Fetch.enable":
                        def intercepted(route):
                            self.current = RouteProxy(route)
                            self.callback({"requestId": "synthetic-request",
                                "resourceType": "Document" if route.request.is_navigation_request() else "Other",
                                "request": {"url": route.request.url}})
                        context.context.route("**/*", intercepted)
                    elif method == "Fetch.continueRequest":
                        response = self.current.fetch()
                        self.current.fulfill(response=response)
                    elif method == "Fetch.failRequest":
                        self.current.abort()
            return MockTransportSession()

        def __getattr__(self, name):
            return getattr(self.context, name)

    class BrowserProxy:
        def __init__(self, browser):
            self.browser = browser

        def new_context(self, **kw):
            return ContextProxy(self.browser.new_context(**kw))

        def close(self):
            self.browser.close()

    @contextmanager
    def fake_factory():
        with real_factory() as pw:
            launch = pw.chromium.launch
            def checked_launch(**kw):
                import os
                assert kw["headless"] is False  # production is always headed; CI can hide the synthetic test window
                return BrowserProxy(launch(headless=os.environ.get("BANK_BROWSER_TEST_HEADED") != "1", channel="msedge"))
            monkeypatch.setattr(pw.chromium, "launch", checked_launch)
            yield pw

    monkeypatch.setattr(playwright, "sync_playwright", fake_factory)
    if bank_denied:
        with pytest.raises(browser_engine.BankAccessBlocked):
            browser_engine.retrieve(BrowserProfile.model_validate(permitted_profile()), customer_id="SYNTHETIC-123",
                                    masked_number="XXXX0127", date_from=date(2026, 11, 1), date_to=date(2026, 11, 2),
                                    progress=lambda *a: None)
        assert "/accounts" not in observed and "/statement" not in observed
        return
    body = permitted_profile()
    if saved_login:
        body.update(login_fields_reviewed=True, password_selector="#bank-password", submit_selector="#continue")
    files = browser_engine.retrieve(BrowserProfile.model_validate(body), customer_id="SYNTHETIC-123",
                                    masked_number="XXXX0127", date_from=date(2026, 11, 1), date_to=date(2026, 11, 2),
                                    progress=lambda *a: None, password="SYNTHETIC-PASSWORD" if saved_login else "")
    assert files == [("statement.csv", CSV)]
    assert "/login" in observed and "/accounts" in observed and "/statement" in observed


def test_access_denied_document_is_not_treated_as_login_wait():
    from app.integrations.banks.browser_engine import BankAccessBlocked, check_bank_access
    page = Mock()
    page.title.return_value = "403 Forbidden"
    with pytest.raises(BankAccessBlocked):
        check_bank_access(page, {})


def test_authentication_redirect_does_not_fail_the_browser_session():
    from playwright.sync_api import Error
    from app.integrations.banks.browser_engine import check_bank_access
    page = Mock()
    page.title.side_effect = Error("Execution context was destroyed, most likely because of a navigation")
    check_bank_access(page, {})


@pytest.mark.parametrize("query", ["error=ACCESS_DENIED&error_description=ACCESS_DENIED", "error=access_denied",
    "error_description=ACCESS_DENIED", "error=ACCESS%5FDENIED&state=do-not-log-this"])
def test_bank_callback_access_denied_pauses_without_echoing_auth_parameters(query):
    from app.integrations.banks.browser_engine import BankAuthorizationDenied, check_bank_access
    page = Mock(url="https://now.hdfc.bank.in/retail-app/select-context?" + query)
    with pytest.raises(BankAuthorizationDenied) as error:
        check_bank_access(page, {})
    assert error.value.action == "bank_access_denied"
    assert "do-not-log-this" not in str(error.value)
    page.title.assert_not_called()


def test_denied_authorization_waits_for_user_and_manual_recovery_does_not_resubmit_password(client, owner, browser_enabled, monkeypatch):
    from app.integrations.banks.browser_engine import BankAuthorizationDenied
    account = _hdfc_manual_account(client)
    save_hdfc_login(client, account)
    calls = []
    def refused(*a, **kw):
        calls.append(kw)
        raise BankAuthorizationDenied()
    monkeypatch.setattr(browser_jobs, "retrieve", refused)
    url = f"/api/v1/accounts/{account['id']}/browser/start"
    result = client.post(url, json={"profile_key": "hdfc_bank", "login_only": True, "authenticate": True})
    assert result.status_code == 202, result.text
    run_pending_once()
    assert run_pending_once() == 0
    assert len(calls) == 1
    with session_scope() as db:
        job = db.get(AutomationJob, uuid.UUID(result.json()["job_id"]))
        assert job.status == "awaiting_user" and job.requires_user_action == "bank_access_denied"
        assert job.error_code == "bank_authorization_denied" and job.attempts == 1
        assert db.get(BankAccount, uuid.UUID(account["id"])).last_successful_sync_at is None
    status = client.get(f"/api/v1/accounts/{account['id']}/browser").json()
    assert status["job"]["error_code"] == "bank_authorization_denied"
    recovery = client.post(url, json={"profile_key": "hdfc_bank", "login_only": True})
    assert recovery.status_code == 202 and recovery.json()["job_id"] == result.json()["job_id"]
    monkeypatch.setattr(browser_jobs, "retrieve", lambda *a, **kw: calls.append(kw) or [])
    run_pending_once()
    assert calls[-1]["password"] == ""


def test_bank_access_denial_does_not_retry_or_mark_profile_verified(client, owner, browser_enabled, monkeypatch):
    from app.integrations.banks.browser_engine import BankAccessBlocked
    def refused(*a, **kw):
        raise BankAccessBlocked("The bank refused this browser session")
    monkeypatch.setattr(browser_jobs, "retrieve", refused)
    account, job_id = configure_account(client)
    run_pending_once()
    with session_scope() as db:
        job = db.get(AutomationJob, uuid.UUID(job_id))
        assert job.status == "failed" and job.error_code == "bank_access_blocked"
        assert job.attempts == 1
        assert db.get(BankAccount, uuid.UUID(account["id"])).last_successful_sync_at is None


@pytest.mark.parametrize('url,allowed', [('wss://now.hdfc.bank.in/approval?token=SYNTHETIC', True),
    ('wss://evil.example/approval', False), ('ws://now.hdfc.bank.in/approval', False)])
def test_secure_approval_channels_require_reviewed_bank_domains(monkeypatch, url, allowed):
    from app.integrations.banks import browser_engine
    original = browser_engine.validate_url
    monkeypatch.setattr(browser_engine, 'validate_url', lambda location, hosts, **kw: original(location, hosts))
    ws = Mock(url=url)
    state = {}
    browser_engine.route_bank_websocket(ws, HDFC, state)
    assert ws.connect_to_server.called is allowed
    assert ws.close.called is not allowed
    assert 'SYNTHETIC' not in str(state)


@pytest.mark.parametrize('code', ['ACCESS_DENIED', 'invalid_state', 'session_expired'])
def test_transient_callback_error_is_kept_after_bank_redirects_to_login(code):
    from app.integrations.banks.browser_engine import remember_auth_error, check_bank_access
    from app.integrations.base.adapter import UserActionRequired
    state = {}
    remember_auth_error(f'https://now.hdfc.bank.in/retail-app/select-context?error={code}&code=SECRET&state=SECRET', state)
    remember_auth_error('https://now.hdfc.bank.in/auth/login?state=OTHER-SECRET', state)
    assert state['auth_error'] == code.lower()
    assert state['navigation_stages'] == ['callback', 'login']
    assert 'SECRET' not in str(state)
    with pytest.raises(UserActionRequired):
        check_bank_access(Mock(url='https://now.hdfc.bank.in/auth/login'), state)


def test_native_guard_validates_each_redirect_request_without_reading_payload(monkeypatch):
    from app.integrations.banks import browser_engine
    validate = browser_engine.validate_url
    monkeypatch.setattr(browser_engine, "validate_url", lambda url, hosts, **kw: validate(url, hosts))
    context, page = Mock(), Mock()
    session = context.new_cdp_session.return_value
    blocked = [False]
    browser_engine.install_native_domain_guard(context, page, HDFC, blocked, {})
    callback = session.on.call_args.args[1]
    callback({"requestId": "original", "resourceType": "Document", "request": {"url": HDFC.login_url}})
    session.send.assert_any_call("Fetch.continueRequest", {"requestId": "original"})
    callback({"requestId": "redirect", "resourceType": "Document", "request": {"url": "https://evil.example/"}})
    session.send.assert_any_call("Fetch.failRequest", {"requestId": "redirect", "errorReason": "BlockedByClient"})
    assert blocked == [True]


def test_guard_reports_blocked_resource_host_and_token_status_without_secrets(monkeypatch):
    from app.integrations.banks import browser_engine
    original = browser_engine.validate_url
    monkeypatch.setattr(browser_engine, 'validate_url', lambda url, hosts, **kw: original(url, hosts))
    context, page = Mock(), Mock()
    state = {}
    browser_engine.install_native_domain_guard(context, page, HDFC, [False], state)
    paused = context.new_cdp_session.return_value.on.call_args.args[1]
    paused({'requestId': 'resource', 'resourceType': 'Fetch',
            'request': {'url': 'https://api.unreviewed.example/data?token=SYNTHETIC-SECRET'}})
    assert state['blocked_resource_hosts'] == ['api.unreviewed.example']
    handlers = {call.args[0]: call.args[1] for call in page.on.call_args_list}
    response = Mock(url='https://now.hdfc.bank.in/auth/realms/retail/protocol/openid-connect/token?code=SYNTHETIC-SECRET', status=401)
    response.request.is_navigation_request.return_value = False
    response.request.resource_type = 'fetch'
    handlers['response'](response)
    assert state['auth_exchange_status'] == 401
    assert state['failed_resource_responses'] == [{'host': 'now.hdfc.bank.in', 'status': 401}]
    assert 'SYNTHETIC-SECRET' not in str(state)


def test_bank_logout_route_stops_waiting_without_claiming_authentication():
    from app.integrations.banks.browser_engine import check_bank_access, BankSessionEnded
    with pytest.raises(BankSessionEnded) as error:
        check_bank_access(Mock(url='https://now.hdfc.bank.in/retail-app/logoutuser'), {})
    assert error.value.code == 'bank_session_ended'


@pytest.mark.parametrize('host', ['mbrww.hdfcbank.com', 'nextgenhtml.hdfcbank.com', 'api-now.hdfc.bank.in'])
def test_hdfc_observed_resource_hosts_are_allowed_only_as_resources(monkeypatch, host):
    from app.integrations.banks import browser_engine
    original = browser_engine.validate_url
    monkeypatch.setattr(browser_engine, 'validate_url', lambda url, hosts, **kw: original(url, hosts))
    context, page = Mock(), Mock()
    state, blocked = {}, [False]
    browser_engine.install_native_domain_guard(context, page, HDFC, blocked, state)
    session = context.new_cdp_session.return_value
    paused = session.on.call_args.args[1]
    paused({'requestId': 'asset', 'resourceType': 'Script', 'request': {'url': f'https://{host}/synthetic.js'}})
    session.send.assert_any_call('Fetch.continueRequest', {'requestId': 'asset'})
    assert blocked == [False] and not state.get('blocked_resource_hosts')
    paused({'requestId': 'document', 'resourceType': 'Document', 'request': {'url': f'https://{host}/login'}})
    session.send.assert_any_call('Fetch.failRequest', {'requestId': 'document', 'errorReason': 'BlockedByClient'})
    assert blocked == [True]


def test_native_chromium_redirects_are_guarded_before_network_transmission(monkeypatch):
    """Exercise real CDP interception; serve synthetic responses without contacting any website."""
    import base64
    from contextlib import suppress
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright, Error
    from app.integrations.banks import browser_engine

    validate = browser_engine.validate_url
    monkeypatch.setattr(browser_engine, "validate_url", lambda url, hosts, **kw: validate(url, hosts))
    profile = BrowserProfile.model_validate(permitted_profile())
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        try:
            context = browser.new_context()
            page = context.new_page()
            session = context.new_cdp_session(page)
            send = session.send
            requests = {}
            session.on("Fetch.requestPaused", lambda event: requests.update({event["requestId"]: event["request"]["url"]}))
            def synthetic_send(method, params):
                if method == "Fetch.continueRequest":
                    path = urlsplit(requests[params["requestId"]]).path
                    headers = [{"name": "Content-Type", "value": "text/html"}]
                    code, body = 200, b"<h1>Mock account page</h1>"
                    if path in ("/login", "/external"):
                        code, body = 302, b""
                        headers.append({"name": "Location", "value": "https://bank.example/accounts"
                                        if path == "/login" else "https://evil.example/blocked"})
                    return send("Fetch.fulfillRequest", {"requestId": params["requestId"], "responseCode": code,
                                "responseHeaders": headers, "body": base64.b64encode(body).decode()})
                return send(method, params)
            monkeypatch.setattr(session, "send", synthetic_send)
            monkeypatch.setattr(context, "new_cdp_session", lambda current: session)
            blocked = [False]
            browser_engine.install_native_domain_guard(context, page, profile, blocked, {})
            page.goto("https://bank.example/login", wait_until="domcontentloaded")
            assert urlsplit(page.url).path == "/accounts"
            assert "https://bank.example/login" in requests.values()
            assert "https://bank.example/accounts" in requests.values()
            with pytest.raises(Error):
                page.goto("https://bank.example/external", wait_until="domcontentloaded")
            assert blocked == [True]
        finally:
            with suppress(Exception):
                browser.close()


def save_hdfc_login(client, account, customer="SYNTHETIC-USER", password="SYNTHETIC-PASSWORD"):
    body = {"profile_key": "hdfc_bank", "customer_id": customer}
    if password is not None:
        body["password"] = password
    return client.put(f"/api/v1/accounts/{account['id']}/browser/credentials", json=body)


def test_saved_login_is_encrypted_and_never_returned(client, owner):
    account = _hdfc_manual_account(client)
    result = save_hdfc_login(client, account)
    assert result.status_code == 200, result.text
    assert result.json() == {"saved": True, "has_customer_id": True, "has_password": True}
    status = client.get(f"/api/v1/accounts/{account['id']}/browser")
    assert status.json()["has_password"] is True
    assert "SYNTHETIC-PASSWORD" not in status.text
    assert "SYNTHETIC-USER" not in status.text
    with session_scope() as db:
        row = db.get(BankAccount, uuid.UUID(account["id"]))
        assert "SYNTHETIC-PASSWORD" not in row.browser_identifier_encrypted
        creds = decrypt_json(row.browser_identifier_encrypted, str(row.tenant_id))
        assert creds["password"] == "SYNTHETIC-PASSWORD"
        assert creds["scope"] == HDFC.credential_scope
        assert row.connection.provider.code == "manual_upload"


def test_saved_password_is_preserved_only_for_same_customer(client, owner):
    account = _hdfc_manual_account(client)
    assert save_hdfc_login(client, account).json()["has_password"]
    assert save_hdfc_login(client, account, password=None).json()["has_password"]
    changed = save_hdfc_login(client, account, customer="DIFFERENT-USER", password=None)
    assert changed.status_code == 200 and not changed.json()["has_password"]


def test_forget_saved_login_removes_identifier_and_password(client, owner):
    account = _hdfc_manual_account(client)
    save_hdfc_login(client, account)
    result = client.delete(f"/api/v1/accounts/{account['id']}/browser/credentials")
    assert result.status_code == 200
    status = client.get(f"/api/v1/accounts/{account['id']}/browser").json()
    assert not status["has_password"] and not status["has_customer_id"]


def test_authenticate_requires_saved_login_and_keeps_secrets_out_of_jobs(client, owner, browser_enabled):
    account = _hdfc_manual_account(client)
    body = {"profile_key": "hdfc_bank", "login_only": True, "authenticate": True}
    url = f"/api/v1/accounts/{account['id']}/browser/start"
    assert client.post(url, json=body).status_code == 422
    save_hdfc_login(client, account)
    result = client.post(url, json=body)
    assert result.status_code == 202, result.text
    with session_scope() as db:
        job = db.get(AutomationJob, uuid.UUID(result.json()["job_id"]))
        assert job.payload["authenticate"] is True
        assert "SYNTHETIC-PASSWORD" not in str(job.payload)
        assert "password" not in job.payload


def test_profile_domain_change_cannot_reuse_saved_password(client, owner, browser_enabled):
    account = _hdfc_manual_account(client)
    save_hdfc_login(client, account)
    changed = HDFC.model_dump(mode="json")
    changed["domains"].append("another.example")
    assert client.put("/api/v1/banks/browser-profiles/hdfc_bank", json=changed).status_code == 200
    result = client.post(f"/api/v1/accounts/{account['id']}/browser/start", json={
        "profile_key": "hdfc_bank", "login_only": True, "authenticate": True})
    assert result.status_code == 422
    assert not client.get(f"/api/v1/accounts/{account['id']}/browser").json()["has_password"]


def test_worker_uses_password_only_for_requested_authentication(client, owner, browser_enabled, monkeypatch):
    account = _hdfc_manual_account(client)
    save_hdfc_login(client, account)
    calls = []
    monkeypatch.setattr(browser_jobs, "retrieve", lambda *a, **kw: calls.append(kw) or [])
    url = f"/api/v1/accounts/{account['id']}/browser/start"
    client.post(url, json={"profile_key": "hdfc_bank", "login_only": True})
    run_pending_once()
    assert calls[-1]["password"] == ""
    client.post(url, json={"profile_key": "hdfc_bank", "login_only": True, "authenticate": True})
    run_pending_once()
    assert calls[-1]["password"] == "SYNTHETIC-PASSWORD"


@pytest.mark.parametrize('saved', [False, True])
def test_qr_login_never_loads_or_submits_saved_credentials(client, owner, browser_enabled, monkeypatch, saved):
    account = _hdfc_manual_account(client)
    if saved:
        save_hdfc_login(client, account)
    calls = []
    monkeypatch.setattr(browser_jobs, 'retrieve', lambda *a, **kw: calls.append(kw) or [])
    def no_decryption(*args):
        raise AssertionError('QR worker must not decrypt saved bank credentials')
    monkeypatch.setattr(browser_jobs, 'decrypt_json', no_decryption)
    result = client.post(f"/api/v1/accounts/{account['id']}/browser/start", json={
        'profile_key': 'hdfc_bank', 'login_only': True, 'qr_login': True})
    assert result.status_code == 202, result.text
    run_pending_once()
    assert calls[0]['password'] == '' and calls[0]['customer_id'] == ''
    with session_scope() as db:
        job = db.get(AutomationJob, uuid.UUID(result.json()['job_id']))
        assert job.status == 'succeeded' and job.result['data_retrieved'] is False
        assert db.get(BankAccount, uuid.UUID(account['id'])).last_successful_sync_at is None


def test_qr_login_rejects_password_submission_and_switching_an_active_session(client, owner, browser_enabled):
    account = _hdfc_manual_account(client)
    url = f"/api/v1/accounts/{account['id']}/browser/start"
    base = {'profile_key': 'hdfc_bank', 'login_only': True, 'qr_login': True}
    assert client.post(url, json={**base, 'authenticate': True}).status_code == 422
    assert client.post(url, json={**base, 'customer_id': 'CUSTOMER'}).status_code == 422
    assert client.post(url, json=base).status_code == 202
    assert client.post(url, json={**base, 'qr_login': False}).status_code == 409


@pytest.mark.parametrize('label', ['Click here to Scan QR Code',
    'Click here to Scan QR Code. It’s secure & fast', 'Login via QR Code'])
def test_hdfc_qr_option_is_selected_once_on_the_official_page(monkeypatch, label):
    from app.integrations.banks import browser_engine
    from playwright.sync_api import TimeoutError
    page = Mock(url='https://now.hdfc.bank.in/auth/login')
    page.get_by_role.return_value.and_.return_value.wait_for.side_effect = TimeoutError('No card in this variant')
    target = page.get_by_text.return_value.and_.return_value
    target.count.return_value = 1
    target.inner_text.return_value = label
    target.get_attribute.return_value = ''
    guard = Mock()
    monkeypatch.setattr(browser_engine, 'validate_url', guard)
    assert browser_engine.open_bank_qr_login(page, HDFC) is True
    pattern = page.get_by_text.call_args.args[0]
    assert pattern.search(label)
    assert not pattern.search('Scan & Pay QR Code')
    assert not pattern.search('Confirm payment QR Code')
    guard.assert_called_once_with(page.url, HDFC.domains, resolve=True)
    target.click.assert_called_once()
    page.fill.assert_not_called()


def test_changed_or_ambiguous_qr_option_stays_open_for_manual_selection(monkeypatch):
    from playwright.sync_api import TimeoutError
    from app.integrations.banks import browser_engine
    page = Mock(url='https://now.hdfc.bank.in/auth/login')
    page.get_by_role.return_value.and_.return_value.wait_for.side_effect = TimeoutError('No card in this variant')
    target = page.get_by_text.return_value.and_.return_value
    target.wait_for.side_effect = TimeoutError('Synthetic missing option')
    assert browser_engine.open_bank_qr_login(page, HDFC) is False
    target.click.assert_not_called()
    page.close.assert_not_called()
    target.wait_for.side_effect = None
    target.count.return_value = 2
    assert browser_engine.open_bank_qr_login(page, HDFC) is False
    target.click.assert_not_called()


def test_run_now_remembers_qr_mode_without_requiring_saved_password(client, owner, browser_enabled, monkeypatch):
    account = _hdfc_manual_account(client)
    calls = []
    monkeypatch.setattr(browser_jobs, 'retrieve', lambda *args, **kw: calls.append(kw) or [])
    response = client.post(f"/api/v1/accounts/{account['id']}/browser/start", json={
        'profile_key': 'hdfc_bank', 'login_only': True, 'qr_login': True})
    assert response.status_code == 202, response.text
    run_pending_once()
    status = client.get(f"/api/v1/accounts/{account['id']}/browser").json()
    assert status['preferred_login_method'] == 'qr' and status['has_password'] is False
    response = client.post(f"/api/v1/accounts/{account['id']}/sync")
    assert response.status_code == 202, response.text
    with session_scope() as db:
        job = db.get(AutomationJob, uuid.UUID(response.json()['job_id']))
        assert job.payload['qr_login'] is True and job.payload['authenticate'] is False
    run_pending_once()
    assert calls[-1]['qr_login'] is True
    assert calls[-1]['password'] == '' and calls[-1]['customer_id'] == ''


def test_qr_option_refuses_unreviewed_domain(monkeypatch):
    from app.integrations.banks import browser_engine
    from playwright.sync_api import TimeoutError
    page = Mock(url='https://unreviewed.example/login')
    page.get_by_role.return_value.and_.return_value.wait_for.side_effect = TimeoutError('No card in this variant')
    target = page.get_by_text.return_value.and_.return_value
    target.count.return_value = 1
    monkeypatch.setattr(browser_engine, 'validate_url', Mock(side_effect=ValueError('Wrong bank domain')))
    assert browser_engine.open_bank_qr_login(page, HDFC) is False
    target.click.assert_not_called()


def test_hdfc_qr_uses_real_login_card_before_its_noninteractive_hint(monkeypatch):
    from app.integrations.banks import browser_engine
    page = Mock(url='https://now.hdfc.bank.in/auth/login')
    card = page.get_by_role.return_value.and_.return_value
    card.count.return_value = 1
    card.inner_text.return_value = 'Login without Password NEW Scan using your HDFC Bank App secured with Kavach'
    card.get_attribute.return_value = ''
    monkeypatch.setattr(browser_engine, 'validate_url', Mock())
    assert browser_engine.open_bank_qr_login(page, HDFC) is True
    card.click.assert_called_once()
    page.get_by_text.return_value.and_.return_value.click.assert_not_called()


def test_qr_approved_supported_workflow_can_continue_to_statement_pipeline(client, owner, browser_enabled, monkeypatch):
    account = _hdfc_manual_account(client)
    assert client.put('/api/v1/banks/browser-profiles/example_bank', json=permitted_profile()).status_code == 200
    calls = []
    monkeypatch.setattr(browser_jobs, 'retrieve', lambda *a, **kw: calls.append(kw) or [('statement.csv', CSV)])
    response = client.post(f"/api/v1/accounts/{account['id']}/browser/start", json={
        'profile_key': 'example_bank', 'login_only': False, 'qr_login': True})
    assert response.status_code == 202, response.text
    run_pending_once()
    assert calls[0]['password'] == '' and calls[0]['customer_id'] == '' and calls[0]['login_only'] is False
    assert calls[0]['qr_login'] is True
    with session_scope() as db:
        job = db.get(AutomationJob, uuid.UUID(response.json()['job_id']))
        assert job.status == 'succeeded' and job.result['data_retrieved'] is True
        assert job.result['statements_new'] == 1


def test_saved_password_cannot_be_filled_into_a_text_field():
    from app.integrations.banks.browser_engine import submit_saved_login
    page, customer, password = Mock(), Mock(), Mock()
    customer.count.return_value = password.count.return_value = 1
    customer.get_attribute.return_value = password.get_attribute.return_value = "text"
    page.locator.side_effect = lambda selector: {"#username": customer, "#password": password}[selector]
    with pytest.raises(ProfileReviewRequired):
        submit_saved_login(page, HDFC, "SYNTHETIC-USER", "SYNTHETIC-PASSWORD")
    password.fill.assert_not_called()


def test_navigation_review_excludes_secret_values_and_temporary_urls():
    from app.integrations.banks.browser_engine import navigation_review
    page = Mock(url="https://bank.example/accounts?code=SECRET-CODE&state=SECRET-STATE")
    controls, candidates = Mock(), Mock()
    controls.evaluate_all.return_value = [
        {"tag": "button", "id": "statements", "role": "", "label": "View statements for 50100123450127"},
        {"tag": "button", "id": "show-password", "role": "", "label": "Show Password"},
    ]
    candidates.evaluate_all.return_value = [{"id": "account-number", "tag": "span"}]
    page.locator.side_effect = [controls, candidates]
    result = navigation_review(page, "XXXX0127")
    assert result["path"] == "/accounts"
    assert result["account_selector_candidates"] == ["#account-number"]
    assert len(result["controls"]) == 1
    assert "50100123450127" not in str(result)
    assert "SECRET" not in str(result) and "Password" not in str(result)
    assert result["verified"] is False


def test_login_review_evidence_does_not_claim_sync_success(client, owner, browser_enabled, monkeypatch):
    account = _hdfc_manual_account(client)
    def inspect_only(*a, **kw):
        kw["on_review"]({"path": "/accounts", "controls": [], "verified": False})
        return []
    monkeypatch.setattr(browser_jobs, "retrieve", inspect_only)
    result = client.post(f"/api/v1/accounts/{account['id']}/browser/start", json={"profile_key": "hdfc_bank", "login_only": True})
    assert result.status_code == 202, result.text
    run_pending_once()
    with session_scope() as db:
        job = db.get(AutomationJob, uuid.UUID(result.json()["job_id"]))
        assert job.result["navigation_review"]["path"] == "/accounts"
        assert job.result["data_retrieved"] is False
        assert db.get(BankAccount, uuid.UUID(account["id"])).last_successful_sync_at is None


@pytest.mark.parametrize("endpoint", ["account", "selected", "all"])
def test_automation_run_now_uses_saved_login_instead_of_statement_upload(client, owner, browser_enabled, monkeypatch, endpoint):
    account = _hdfc_manual_account(client)
    assert save_hdfc_login(client, account).status_code == 200
    calls = []
    monkeypatch.setattr(browser_jobs, "retrieve", lambda *a, **kw: calls.append(kw) or [])
    if endpoint == "account":
        result = client.post(f"/api/v1/accounts/{account['id']}/sync")
    else:
        result = client.post("/api/v1/automation/run-now", json={"account_ids": [account["id"]]} if endpoint == "selected" else {})
    assert result.status_code == 202, result.text
    with session_scope() as db:
        job = db.scalar(select(AutomationJob).where(AutomationJob.account_id == uuid.UUID(account["id"])))
        assert job.job_type == "open_bank_login" and job.payload["authenticate"] is True
    run_pending_once()
    assert len(calls) == 1
    assert calls[0]["password"] == "SYNTHETIC-PASSWORD"
    current = client.get(f"/api/v1/accounts/{account['id']}").json()
    assert current["last_successful_sync_at"] is None
    assert current["automation"]["status"] == "ready"


def test_saved_login_run_now_prevents_duplicate_browser_jobs(client, owner, browser_enabled):
    account = _hdfc_manual_account(client)
    save_hdfc_login(client, account)
    url = f"/api/v1/accounts/{account['id']}/sync"
    first, second = client.post(url), client.post(url)
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert second.json()["created"] is False


def test_otp_wait_configuration_rejects_less_than_sixty_seconds():
    from app.core.config import Settings
    with pytest.raises(ValidationError):
        Settings(browser_login_timeout_seconds=59)
    assert Settings(browser_login_timeout_seconds=60).browser_login_timeout_seconds == 60


def test_browser_status_reports_headed_mode_and_otp_wait(client, owner, browser_enabled):
    account = _hdfc_manual_account(client)
    status = client.get(f"/api/v1/accounts/{account['id']}/browser").json()
    assert status["enabled"] is True and status["browser_mode"] == "headed"
    assert status["verification_wait_seconds"] >= 60
    health = client.get("/api/v1/health").json()
    assert health["bank_browser"]["enabled"] is True
    assert health["bank_browser"]["verification_wait_seconds"] >= 60


def test_enabling_browser_resolves_old_configuration_failure_without_faking_sync(client, owner, browser_enabled):
    account = _hdfc_manual_account(client)
    save_hdfc_login(client, account)
    with session_scope() as db:
        from app.automation import queue
        acc = db.get(BankAccount, uuid.UUID(account["id"]))
        job, _ = queue.enqueue(db, tenant_id=acc.tenant_id, account_id=acc.id, job_type="open_bank_login", trigger="manual")
        job.status, job.error_code = "failed", "provider_not_configured"
        job.error_message = "Set BROWSER_AUTOMATION_ENABLED=true"
    current = client.get(f"/api/v1/accounts/{account['id']}").json()
    assert current["automation"]["status"] == "ready"
    assert current["last_successful_sync_at"] is None


@pytest.mark.parametrize("disabled,account_paused,organisation_paused", [(False, True, False), (True, False, False), (False, True, True)])
def test_manual_run_now_opens_saved_bank_even_when_schedules_are_paused(client, owner, browser_enabled, monkeypatch,
                                                                      disabled, account_paused, organisation_paused):
    account = _hdfc_manual_account(client)
    save_hdfc_login(client, account)
    with session_scope() as db:
        acc = db.get(BankAccount, uuid.UUID(account["id"]))
        acc.automation_enabled, acc.automation_paused = not disabled, account_paused
    if organisation_paused:
        assert client.post("/api/v1/automation/pause").status_code == 200
    calls = []
    monkeypatch.setattr(browser_jobs, "retrieve", lambda *a, **kw: calls.append(kw) or [])
    result = client.post("/api/v1/automation/run-now", json={})
    assert result.status_code == 202, result.text
    assert result.json()["jobs_started"] == 1
    assert result.json()["message"] == "1 bank browser login(s) queued"
    run_pending_once()
    assert len(calls) == 1 and calls[0]["password"] == "SYNTHETIC-PASSWORD"
    current = client.get(f"/api/v1/accounts/{account['id']}").json()
    assert current["automation_paused"] == account_paused
    assert current["automation_enabled"] == (not disabled)
    assert current["last_successful_sync_at"] is None


def test_resumed_browser_run_reports_started_instead_of_zero_collection_jobs(client, owner, browser_enabled):
    account = _hdfc_manual_account(client)
    save_hdfc_login(client, account)
    first = client.post("/api/v1/automation/run-now", json={})
    assert first.status_code == 202
    with session_scope() as db:
        job = db.scalar(select(AutomationJob).where(AutomationJob.account_id == uuid.UUID(account["id"])))
        original = job.id
        job.status, job.requires_user_action = "awaiting_user", "reauth"
    second = client.post("/api/v1/automation/run-now", json={})
    assert second.status_code == 202, second.text
    assert second.json()["jobs_created"] == 0
    assert second.json()["jobs_resumed"] == second.json()["jobs_started"] == 1
    assert second.json()["message"] == "1 bank browser login(s) queued"
    with session_scope() as db:
        job = db.get(AutomationJob, original)
        assert job.status == "queued"


def test_already_queued_browser_has_clear_message_without_duplicate(client, owner, browser_enabled):
    account = _hdfc_manual_account(client)
    save_hdfc_login(client, account)
    client.post("/api/v1/automation/run-now", json={})
    second = client.post("/api/v1/automation/run-now", json={})
    assert second.status_code == 202
    assert second.json()["already_in_progress"] == 1
    assert "already queued or running" in second.json()["message"]


def test_empty_run_now_reports_no_eligible_account_instead_of_success_zero(client, owner):
    result = client.post("/api/v1/automation/run-now", json={})
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "no_eligible_accounts"
