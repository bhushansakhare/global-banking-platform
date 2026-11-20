"""Visible, ephemeral Playwright sessions. Secrets are entered only on the bank page.

No tracing, authentication screenshots, storage-state exports, payload logging or private API integrations.
Optional statement-screen images are captured only after account confirmation and validated download.
Every Playwright object is created and disposed on the worker thread that owns it.
"""
from __future__ import annotations

import re
import time
import csv
import html
import zipfile
from io import BytesIO, StringIO
from contextlib import suppress
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

from app.core.config import get_settings
from app.integrations.base.adapter import ConfigurationMissing, ProviderError, UserActionRequired
from app.integrations.banks.profiles import BrowserProfile, validate_url
from app.statements.detect import detect_format


class ProfileReviewRequired(ProviderError):
    code = "browser_profile_review_required"


class BankAccessBlocked(ProviderError):
    code = "bank_access_blocked"


class BankAuthorizationDenied(UserActionRequired):
    code = "bank_authorization_denied"

    def __init__(self):
        super().__init__("The bank declined this login session (ACCESS_DENIED). No statements were retrieved. "
                         "Open a fresh official bank login and verify access manually. "
                         "If manual login is also denied, contact the bank; if it works, this browser workflow needs review. "
                         "Automatic login retries are paused.", action="bank_access_denied")


class BankSessionEnded(UserActionRequired):
    code = "bank_session_ended"

    def __init__(self):
        super().__init__("The bank ended this session and opened its logout page. "
                         "No statement retrieval was verified; automatic login retries are paused.", action="bank_session_ended")


class BankObservationCaptured(Exception):
    """The matched account balance/screen check completed without a file download."""


def check_bank_access(page, access_state: dict) -> None:
    """A denial is not authentication-in-progress and must not trigger repeated login attempts."""
    status = access_state.get("status")
    # An authentication rejection may be returned in a successful HTTP 200 callback page.
    # Never log the URL, auth parameters or the bank's free-form error_description.
    location = page.url if isinstance(page.url, str) else ""
    if urlsplit(location).path.rstrip("/") == "/retail-app/logoutuser":
        raise BankSessionEnded()
    query = parse_qs(urlsplit(location).query)
    errors = {value.casefold() for key in ("error", "error_description") for value in query.get(key, [])}
    if access_state.get("auth_error"):
        errors.add(access_state["auth_error"])
    if errors & {"access_denied", "authorization_denied", "consent_denied"}:
        raise BankAuthorizationDenied()
    if errors & {"login_required", "interaction_required", "session_expired", "invalid_state", "invalid_grant"}:
        raise UserActionRequired("The bank requires a fresh login session. Open the official bank login again; "
                                 "old authentication links cannot be reused. No automatic retry was made.", action="reauth")
    # Some gateways return HTTP 200 with a denial document rather than HTTP 403.
    from playwright.sync_api import Error
    try:
        title = page.title().strip().casefold()
    except Error as exc:
        # Completing OTP/app approval can replace the document while this poll is reading it.
        # Wait for the next poll; never resubmit credentials because of a document transition.
        if any(marker in str(exc) for marker in ("Execution context was destroyed", "Cannot find context with specified id")):
            return
        raise
    if status in (403, 429) or title in ("403 forbidden", "access denied", "request rejected", "too many requests"):
        raise BankAccessBlocked("The bank refused this browser session (access denied or rate limited). "
                                "No statement was retrieved in this attempt. "
                                "Use the bank's normal login or an approved connection method; automatic retries are stopped.")


def validate_download(content: bytes, name: str, formats: list[str]) -> None:
    detected = detect_format(content, name)
    if detected.format not in formats or not content:
        raise ProfileReviewRequired("Downloaded file does not match the reviewed statement formats")
    try:
        if detected.format == "pdf":
            if not detected.page_count:
                raise ValueError("No PDF pages")
        elif detected.format == "xlsx":
            from openpyxl import load_workbook
            with zipfile.ZipFile(BytesIO(content)) as archive:
                if (sum(x.file_size for x in archive.infolist()) > get_settings().max_upload_mb * 1024 * 1024 * 20
                        or archive.testzip()):
                    raise ValueError("Invalid Excel archive")
            workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
            try:
                if not workbook.sheetnames:
                    raise ValueError("No worksheets")
            finally:
                workbook.close()
        elif detected.format == "xls":
            import xlrd
            workbook = xlrd.open_workbook(file_contents=content, on_demand=True)
            try:
                if not workbook.nsheets:
                    raise ValueError("No worksheets")
            finally:
                workbook.release_resources()
        else:
            text = content.decode("utf-8-sig") if not content.startswith(b"\xff\xfe") else content.decode("utf-16")
            if re.search(r"<(?:!doctype|html|form|script)\b", text[:2048], re.I):
                raise ValueError("HTML is not a statement")
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
            rows = csv.reader(StringIO(text), dialect)
            first = next(rows)
            second = next(rows)
            if len(first) < 2 or len(second) != len(first):
                raise ValueError("Malformed CSV")
    except Exception:
        raise ProfileReviewRequired("Statement file is incomplete or corrupt; no import was performed") from None


def guard_request(route, profile: BrowserProfile, blocked: list[bool], access_state: dict | None = None) -> None:
    """Check each redirect before following it. Playwright continue_ alone cannot guard redirect chains."""
    navigation = route.request.is_navigation_request()
    hosts = profile.domains if navigation else [*profile.domains, *profile.resource_domains]
    response = None
    try:
        validate_url(route.request.url, hosts, resolve=True)
        response = route.fetch(max_redirects=0, timeout=45000)
        if navigation and access_state is not None and response.status in (403, 429):
            access_state["status"] = response.status
        if 300 <= response.status < 400 and response.headers.get("location"):
            target = urljoin(route.request.url, response.headers["location"])
            validate_url(target, hosts, resolve=True)
            # A new document navigation gives the next redirect its own domain check.
            # Never replay a password-bearing POST for 307/308 or proxy redirected assets blindly.
            if not navigation or (route.request.method != "GET" and response.status in (307, 308)):
                raise ValueError("Redirect requires profile review")
            route.fulfill(status=200, content_type="text/html", body=(
                '<!doctype html><meta http-equiv="refresh" content="0;url='
                + html.escape(target, quote=True) + '"><title>Opening official bank</title>'))
        else:
            route.fulfill(response=response)
    except Exception:
        if navigation:
            blocked[0] = True
        with suppress(Exception):
            route.abort()
    finally:
        if response is not None:
            with suppress(Exception):
                response.dispose()


AUTH_CALLBACK_ERRORS = {"access_denied", "authorization_denied", "consent_denied", "login_required",
                        "interaction_required", "session_expired", "invalid_state", "invalid_grant"}


def remember_auth_error(location: str, access_state: dict) -> None:
    """Remember only a known error code; never retain the callback URL or its tokens."""
    if not isinstance(location, str):
        return
    parsed = urlsplit(location)
    stage = ("login" if parsed.path.startswith("/auth/") else "callback" if parsed.path.endswith("/select-context")
             else "retail" if parsed.path.startswith("/retail-app") else "other")
    stages = access_state.setdefault("navigation_stages", [])
    if not stages or stages[-1] != stage:
        stages.append(stage)
        del stages[:-12]
    query = parse_qs(parsed.query)
    for key in ("error", "error_description"):
        for value in query.get(key, []):
            code = value.casefold()
            if code in AUTH_CALLBACK_ERRORS:
                access_state.setdefault("auth_error", code)
                return


def route_bank_websocket(ws, profile: BrowserProfile, access_state: dict | None = None) -> None:
    """Permit secure official-bank live approval channels without inspecting messages."""
    try:
        if not ws.url.startswith("wss://"):
            raise ValueError("Secure bank connection required")
        validate_url("https://" + ws.url[6:], [*profile.domains, *profile.resource_domains], resolve=True)
        ws.connect_to_server()
        if access_state is not None:
            access_state["secure_channels_opened"] = min(99, access_state.get("secure_channels_opened", 0) + 1)
    except Exception:
        if access_state is not None:
            access_state["secure_channels_blocked"] = min(99, access_state.get("secure_channels_blocked", 0) + 1)
        # Neither the URL (which can carry auth tokens) nor messages are logged.
        with suppress(Exception):
            ws.close()


def install_native_domain_guard(context, page, profile: BrowserProfile, blocked: list[bool], access_state: dict):
    """Let Chromium send native browser requests; validate each request including redirect hops.

    No auth form is replayed through an HTTP client and no request body/cookie is read.
    CDP Fetch pauses each request before transmission, including redirected requests.
    """
    session = context.new_cdp_session(page)

    def paused(event):
        request_id = event["requestId"]
        document = event.get("resourceType") == "Document"
        try:
            hosts = profile.domains if document else [*profile.domains, *profile.resource_domains]
            validate_url(event["request"]["url"], hosts, resolve=True)
            session.send("Fetch.continueRequest", {"requestId": request_id})
        except Exception:
            if document:
                blocked[0] = True
            else:
                # Host/type only: never retain URL queries, request bodies or credentials.
                host = urlsplit(event["request"]["url"]).hostname
                hosts = access_state.setdefault("blocked_resource_hosts", [])
                if host and host not in hosts and len(hosts) < 12:
                    hosts.append(host)
            with suppress(Exception):
                session.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"})

    def response_received(response):
        if (response.status >= 400 and response.request.resource_type in ("xhr", "fetch")
                and isinstance(response.url, str)):
            failure = {"host": urlsplit(response.url).hostname, "status": response.status}
            failures = access_state.setdefault("failed_resource_responses", [])
            if failure not in failures and len(failures) < 12:
                failures.append(failure)
        if isinstance(response.url, str) and urlsplit(response.url).path.endswith("/protocol/openid-connect/token"):
            access_state["auth_exchange_status"] = response.status
        if response.request.is_navigation_request():
            remember_auth_error(response.url, access_state)
            if response.status in (403, 429):
                access_state["status"] = response.status

    def navigated(frame):
        if frame == page.main_frame:
            remember_auth_error(frame.url, access_state)

    session.on("Fetch.requestPaused", paused)
    session.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})
    page.on("response", response_received)
    page.on("framenavigated", navigated)
    return session


def visible(page, selector: str | None) -> bool:
    return bool(selector and page.locator(selector).count() == 1 and page.locator(selector).is_visible())


def read_only_click(target, selector: str) -> None:
    label = " ".join([selector, target.inner_text(), target.get_attribute("aria-label") or ""])
    if re.search(r"(?:payment|transfer|withdraw|beneficiar|send.money|pay.now|confirm.transaction)", label, re.I):
        raise ProfileReviewRequired("Financial action controls cannot be used in a statement workflow")
    target.click()


def autofill_customer_id(page, profile: BrowserProfile, customer_id: str) -> None:
    reviewed = profile.login_fields_reviewed or (profile.reviewed and profile.permission == "permitted")
    if not reviewed or profile.review_required or not customer_id or not profile.customer_id_selector:
        return
    field = page.locator(profile.customer_id_selector)
    # SPA entry points redirect to a freshly generated authentication page after DOMContentLoaded.
    # An absent field during that transition is not evidence that the bank changed its login form.
    field.wait_for(state="visible", timeout=45000)
    if field.count() != 1 or field.get_attribute("type") == "password" or field.get_attribute("autocomplete") == "one-time-code":
        raise ProfileReviewRequired("Customer ID field changed; profile review required")
    field.fill(customer_id)
    if profile.continue_selector and page.locator('input[type="password"]:visible').count() == 0:
        read_only_click(page.locator(profile.continue_selector), profile.continue_selector)


def submit_saved_login(page, profile: BrowserProfile, customer_id: str, password: str) -> None:
    if not customer_id or not password or not profile.public()["capabilities"]["saved_login"]:
        raise ConfigurationMissing("Save credentials and review this bank's login fields before authenticating")
    autofill_customer_id(page, profile, customer_id)
    field = page.locator(profile.password_selector)
    field.wait_for(state="visible", timeout=15000)
    if field.count() != 1 or field.get_attribute("type") != "password":
        raise ProfileReviewRequired("Bank password field changed; saved login was not submitted")
    field.fill(password)
    submit = page.locator(profile.submit_selector)
    if submit.count() != 1:
        raise ProfileReviewRequired("Bank login submit control changed; review required")
    read_only_click(submit, profile.submit_selector)


def open_bank_qr_login(page, profile: BrowserProfile) -> bool:
    """Select HDFC's named login option once; never inspect, copy or regenerate its QR."""
    if profile.key != "hdfc_bank":
        return False
    from playwright.sync_api import Error, TimeoutError
    label = re.compile(r"^\s*(?:Click here to Scan QR Code(?:[.!][\s\S]*)?|Login via QR Code)\s*$", re.I)
    card = page.get_by_role("button", name=re.compile(r"^\s*Login without Password\b", re.I)).and_(page.locator(":visible"))
    hint = page.get_by_text(label).and_(page.locator(":visible"))
    # The live bank exposes a clickable role=button card. Its QR hint can be a
    # separate noninteractive element, so prefer the actual card over its text.
    for target, timeout in ((card, 15000), (hint, 3000)):
        try:
            target.wait_for(state="visible", timeout=timeout)
            if target.count() != 1:
                return False
            validate_url(page.url, profile.domains, resolve=True)
            read_only_click(target, "HDFC login QR option")
            return True
        except TimeoutError:
            continue
        except (Error, ValueError, OSError):
            return False
    return False


def authenticated(page, profile: BrowserProfile) -> bool:
    return bool(urlsplit(page.url).path == profile.authenticated_path
                and visible(page, profile.authenticated_selector))


def navigation_review(page, masked_number: str) -> dict:
    """Bounded read-only selector evidence for an unconfigured statement workflow.

    Never collect input values, HTML, hrefs, query strings, cookies or response bodies.
    """
    controls = page.locator('a, button, [role="tab"], [role="button"], select, input[type="date"]').evaluate_all("""nodes => nodes
        .filter(n => n.getClientRects().length > 0).slice(0, 200).map(n => ({
            tag: n.tagName.toLowerCase(), id: n.id || '', role: n.getAttribute('role') || '',
            label: (n.getAttribute('aria-label') || (n.tagName === 'INPUT' ? '' : n.innerText) || '').slice(0, 100)
        }))""")
    safe = []
    for item in controls:
        label = re.sub(r"\d{5,}", "[masked]", item["label"])
        if not re.search(r"\b(account|statement|download|export|pdf|csv|excel|history|date|view|search|logout|savings|banking|qr|scan|kavach)\b", label, re.I) \
                and item["tag"] != "input":
            continue
        selector = None
        identifier = item["id"]
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,60}", identifier) and not re.search(r"\d{5,}", identifier):
            selector = f"#{identifier}"
        safe.append({"tag": item["tag"], "selector": selector, "label": label, "role": item["role"]})
    suffix = re.sub(r"\D", "", masked_number)[-4:]
    # Candidate identity selectors contain no account number values in the evidence record.
    candidates = page.locator("span, p, dd").evaluate_all("""(nodes, suffix) => nodes.filter(n =>
        n.children.length === 0 && n.getClientRects().length > 0 && n.innerText.length < 80 &&
        suffix.length === 4 && n.innerText.includes(suffix)).slice(0, 8).map(n => ({id:n.id || '', tag:n.tagName.toLowerCase()}))""", suffix)
    account_selectors = [f"#{x['id']}" for x in candidates if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,60}", x["id"])
                         and not re.search(r"\d{5,}", x["id"])]
    return {"path": re.sub(r"\d{5,}", "[masked]", urlsplit(page.url).path.split(";")[0]), "controls": safe,
            "account_selector_candidates": account_selectors, "verified": False}


def confirm_account(page, profile: BrowserProfile, masked_number: str) -> None:
    # Require the known masked account identity before downloading. Never select the first account blindly.
    suffix = re.sub(r"\D", "", masked_number)[-4:]
    if len(suffix) != 4 or not visible(page, profile.account_selector):
        raise UserActionRequired("Select the linked account on the bank page, then run sync again.", action="reauth")
    label = page.locator(profile.account_selector).inner_text()
    if not re.search(rf"(?<!\d){re.escape(suffix)}(?!\d)", label):
        # Full numbers also allowed, ending in the exact suffix.
        if not re.search(rf"\d*{re.escape(suffix)}(?!\d)", label):
            raise ProfileReviewRequired("The bank page account does not match this linked account")


def session_review(page, masked_number, access_state, qr_option_selected):
    evidence = navigation_review(page, masked_number)
    evidence.update(qr_option_selected=qr_option_selected,
        navigation_stages=list(access_state.get("navigation_stages", [])),
        secure_channels_opened=access_state.get("secure_channels_opened", 0),
        secure_channels_blocked=access_state.get("secure_channels_blocked", 0),
        blocked_resource_hosts=list(access_state.get("blocked_resource_hosts", [])),
        auth_exchange_status=access_state.get("auth_exchange_status"),
        failed_resource_responses=list(access_state.get("failed_resource_responses", [])))
    return evidence


def retrieve(profile: BrowserProfile, *, customer_id: str, masked_number: str,
             date_from: date, date_to: date, progress, login_only: bool = False, password: str = "",
    on_review=None, verification_wait_seconds: int | None = None, on_statement_screenshot=None,
    qr_login: bool = False, session_control=None) -> list[tuple[str, bytes]]:
    settings = get_settings()
    if not settings.browser_automation_enabled:
        raise ConfigurationMissing("Set BROWSER_AUTOMATION_ENABLED=true on the local Windows desktop")
    if qr_login and (customer_id or password):
        raise ConfigurationMissing("QR login must not submit a Customer ID or password")
    if not login_only and not profile.retrieval_supported:
        raise ConfigurationMissing("Statement retrieval requires a reviewed, permitted bank profile")
    try:
        validate_url(profile.login_url, profile.domains, resolve=True)
    except (ValueError, OSError):
        raise ProfileReviewRequired("The reviewed bank domain could not be securely validated") from None
    from playwright.sync_api import Error, sync_playwright

    files: list[tuple[str, bytes]] = []
    qr_option_opened = False
    blocked = [False]
    access_state: dict = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False, channel=settings.browser_channel)
            context = None
            try:
                context = browser.new_context(accept_downloads=True, ignore_https_errors=False,
                                              service_workers="block")
                context.route_web_socket("**/*", lambda ws: route_bank_websocket(ws, profile, access_state))
                # Keep authentication in a single visible page; no uncontrolled popup sessions.
                page = context.new_page()
                install_native_domain_guard(context, page, profile, blocked, access_state)
                context.on("page", lambda popup: popup.close() if popup != page else None)
                page.set_default_timeout(15000)
                progress(10, "Opening official bank login; complete authentication in the visible browser")
                page.goto(profile.login_url, wait_until="domcontentloaded", timeout=45000)
                check_bank_access(page, access_state)
                # A reviewed Customer ID field is independent of unverified statement retrieval.
                # A password-bearing login form remains under the customer's direct control.
                if qr_login:
                    qr_option_opened = open_bank_qr_login(page, profile)
                    progress(15, "Bank QR login selected; scan and approve with the official bank app" if qr_option_opened else
                             "Choose QR login on the bank page, then scan and approve with the official bank app")
                elif password:
                    submit_saved_login(page, profile, customer_id, password)
                    progress(15, "Bank login submitted; complete OTP, CAPTCHA or app approval on the bank page")
                else:
                    autofill_customer_id(page, profile, customer_id)
                wait_seconds = verification_wait_seconds if verification_wait_seconds is not None else settings.browser_login_timeout_seconds
                if not 60 <= wait_seconds <= 720:
                    raise ConfigurationMissing("Bank verification wait must be between 60 and 720 seconds")
                deadline = time.monotonic() + wait_seconds
                last_review_time, previous_review = 0.0, None
                login_confirmed = False
                while time.monotonic() < deadline:
                    if blocked[0]:
                        raise ProfileReviewRequired("Bank navigation reached an unreviewed domain")
                    if page.is_closed():
                        if login_only:
                            return []
                        raise UserActionRequired("Bank browser closed before retrieval completed", action="reauth")
                    try:
                        check_bank_access(page, access_state)
                    except UserActionRequired:
                        if on_review:
                            with suppress(Error):
                                on_review(session_review(page, masked_number, access_state, qr_option_opened))
                        raise
                    if visible(page, profile.failed_selector):
                        raise UserActionRequired("Bank login failed. Verify directly on the official page.", action="reauth")
                    if visible(page, profile.expired_selector):
                        raise UserActionRequired("Bank session expired; authenticate again", action="reauth")
                    path = urlsplit(page.url).path.split(";")[0]
                    if (login_only and profile.key == "hdfc_bank" and
                        (path == "/retail-app/dashboard" or path.startswith("/retail-app/my-accounts/")) and
                        page.get_by_role("button", name="Logout", exact=True).count() == 1):
                        if not login_confirmed:
                            login_confirmed = True
                            deadline = time.monotonic() + 600
                            progress(30, "Bank login confirmed; preparing statement capture")
                        if session_control:
                            statement_button = page.get_by_role("button", name="Get Statement for Savings Accounts", exact=True)
                            if path == "/retail-app/dashboard" and statement_button.count() == 1:
                                read_only_click(statement_button, "Get Statement for Savings Accounts")
                            if path.startswith("/retail-app/my-accounts/"):
                                try:
                                    captured = session_control(page)
                                except BankObservationCaptured:
                                    return []
                                if captured:
                                    files.extend(captured)
                                    break
                    if not login_only and authenticated(page, profile):
                        break
                    if login_only and on_review and time.monotonic() - last_review_time >= 5:
                        last_review_time = time.monotonic()
                        try:
                            evidence = (session_review(page, masked_number, access_state, qr_option_opened) if qr_login
                                        else navigation_review(page, masked_number))
                            evidence["authentication_confirmed"] = login_confirmed
                            if evidence != previous_review:
                                on_review(evidence)
                                previous_review = evidence
                        except Error:
                            pass  # the customer may be navigating between bank pages
                    remaining = max(0, int(deadline - time.monotonic()))
                    progress(30 if login_confirmed else 15,
                             f"{'Bank login confirmed; statement capture' if login_confirmed else 'Waiting for authentication on the official bank page'} ({remaining}s remaining)")
                    page.wait_for_timeout(1000)
                else:
                    raise UserActionRequired("Bank login timed out; open the bank browser again", action="reauth")
                if login_only:
                    return files
                progress(40, "Authentication confirmed; checking linked account")
                confirm_account(page, profile, masked_number)
                for step in profile.steps:
                    if blocked[0] or visible(page, profile.expired_selector):
                        raise UserActionRequired("Bank session expired; authenticate again", action="reauth")
                    target = page.locator(step.selector)
                    if target.count() != 1:
                        raise ProfileReviewRequired("Statement navigation changed; profile review required")
                    if step.action == "click":
                        read_only_click(target, step.selector)
                    elif step.action == "select":
                        target.select_option(step.value)
                    elif step.action == "account":
                        target.select_option(label=masked_number)
                    else:
                        if target.get_attribute("type") in ("password", "hidden"):
                            raise ProfileReviewRequired("Statement date field changed; profile review required")
                        value = date_from if step.action == "date_from" else date_to
                        target.fill(value.strftime(profile.date_format))
                    progress(55, "Navigating the reviewed statement workflow")
                confirm_account(page, profile, masked_number)
                if blocked[0] or visible(page, profile.expired_selector):
                    raise UserActionRequired("Bank session expired; authenticate again", action="reauth")
                with page.expect_download(timeout=45000) as pending:
                    read_only_click(page.locator(profile.download_selector), profile.download_selector)
                download = pending.value
                if blocked[0] or download.failure():
                    raise ProfileReviewRequired("Statement download failed or used an unreviewed domain")
                downloaded = download.path()
                if downloaded is None:
                    raise ProfileReviewRequired("Statement download did not produce a file")
                path = Path(downloaded)
                if not 0 < path.stat().st_size <= settings.max_upload_mb * 1024 * 1024:
                    raise ProfileReviewRequired("Statement download is empty or exceeds the file limit")
                content = path.read_bytes()
                name = Path(download.suggested_filename).name
                validate_download(content, name, profile.formats)
                files.append((name, content))
                if on_statement_screenshot:
                    # Capture only a confirmed account's statement screen, never an authentication form.
                    confirm_account(page, profile, masked_number)
                    check_bank_access(page, access_state)
                    if page.locator('input[type="password"]:visible, input[autocomplete="one-time-code"]:visible').count() == 0:
                        try:
                            on_statement_screenshot(page.screenshot(type="png", full_page=False,
                                mask=[page.locator('input, textarea, [contenteditable="true"]')]))
                        except Error:
                            progress(74, "Statement downloaded; optional bank screen image unavailable")
                if profile.logout_selector and visible(page, profile.logout_selector):
                    page.locator(profile.logout_selector).click()
                progress(75, "Statement downloaded and validated")
            finally:
                if context is not None:
                    with suppress(Exception):
                        context.close()
                with suppress(Exception):
                    browser.close()
    except Error as exc:
        if any(marker in str(exc) for marker in ("Target page, context or browser has been closed",
                                                 "Target closed", "Connection closed while reading from the driver")):
            raise UserActionRequired("The bank browser session closed before verification completed. "
                                     "Open Authenticate when you are ready; no automatic login retry was made.", action="reauth") from None
        # Playwright errors can contain DOM snippets, filled identifiers and temporary auth URLs.
        raise ProfileReviewRequired("Bank browser failed or the page changed; inspect the profile and retry") from None
    return files
