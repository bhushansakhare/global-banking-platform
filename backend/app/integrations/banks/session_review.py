"""Account-owner requested, read-only inspection/capture in the owning browser thread.

Commands come from tenant/job-scoped local review records, never arbitrary JavaScript.
Authentication values, cookies and response bodies are excluded from inspection.
"""
import re
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select

from app.core.config import get_settings
from app.models.system import SystemSetting


def masked(text):
    return re.sub(r"\d{5,}", lambda m: "XXXX" + m[0][-4:], str(text or ""))[:300]


def account_matches(target, suffix):
    if len(suffix) != 4 or target.count() != 1:
        return False
    text = target.inner_text()
    if target.evaluate("n => n.tagName === 'INPUT'"):
        text += " " + target.input_value()
    text = re.split(r"\b(?:Available\s+)?Balance\b", text, maxsplit=1, flags=re.I)[0]
    return bool(re.search(rf"{re.escape(suffix)}(?!\d)", re.sub(r"\s+", "", text)))


def inspect_controls(page, suffix):
    rows = page.locator('button, a, select, [role="combobox"], [role="option"], input:not([type="hidden"]):not([type="password"])').evaluate_all(r"""nodes => nodes
      .filter(n => n.getClientRects().length && n.getAttribute('autocomplete') !== 'one-time-code' &&
        !/otp|one.?time|password|security.?code|\bmpin\b/i.test([n.name,n.id,n.getAttribute('aria-label'),n.getAttribute('placeholder')].join(' ')))
      .slice(0,160).map((n,i) => {
        n.setAttribute('data-gbp-review',String(i));
        const group=n.closest('ng-select, bb-account-selector-ui, bb-account-selector') ||
          (n.getAttribute('role')==='combobox' ? n.parentElement : n);
        return {selector:'[data-gbp-review="'+i+'"]',tag:n.tagName.toLowerCase(),role:n.getAttribute('role')||'',
          label:n.getAttribute('aria-label')||n.getAttribute('placeholder')||'',
          text:(group.innerText||'').slice(0,300),value:(n.tagName==='SELECT'||n.getAttribute('role')==='combobox'||n.type==='date'||
            /date/i.test([n.getAttribute('aria-label'),n.getAttribute('placeholder')].join(' ')) ? n.value||'' : '').slice(0,100),
          parent_tag:group.tagName.toLowerCase(),parent_class:(group.className||'').toString().slice(0,180),
          options:n.tagName==='SELECT'?Array.from(n.options).map(o=>o.text).slice(0,30):[]};
      })""")
    for row in rows:
        combined = str(row.get("text", "")) + " " + str(row.get("value", ""))
        row["account_suffix_matches"] = bool(suffix and re.search(rf"{re.escape(suffix)}(?!\d)", re.sub(r"\s+", "", combined)))
        for key in ("text", "value", "label"):
            row[key] = masked(row.get(key))
        row["options"] = [masked(value) for value in row.get("options", [])]
    return {"controls": rows}


ALLOWED_LABEL = re.compile(r"^(?:Get Statement for Savings Accounts|Account Statement|Recent Transactions|"
    r"Last (?:Month|\d+ (?:Days|Months))|This Month|Current Month|Previous Month|Custom(?: Range| Date Range)?|"
    r"Download(?: Statement)?|Generate(?: Statement)?|View Statement|PDF|Excel|CSV|XLSX|All Transactions|"
    r"Switch to Table View|Switch to Smart View|Apply|Search)$", re.I)


def observed_available_amount(text):
    from app.statements.normalize import parse_amount
    if "Available Balance" not in text:
        return None
    amounts = re.findall(r"\(?-?₹\s*-?[\d,]+\.\d{2}\)?", text)
    return parse_amount(amounts[0]) if len(amounts) == 1 else None


def run_command(db, job, page, profile, masked_number, screenshot_callback=None):
    from app.integrations.banks.browser_engine import read_only_click, validate_download
    from app.integrations.banks.profiles import validate_url
    setting = db.scalar(select(SystemSetting).where(SystemSetting.tenant_id == job.tenant_id,
        SystemSetting.key == f"browser_review:{job.id}").execution_options(populate_existing=True))
    if not setting or setting.value.get("status") != "pending":
        return []
    command = dict(setting.value)
    setting.value = {**command, "status": "running"}
    db.commit()
    files = []
    try:
        validate_url(page.url, profile.domains, resolve=True)
        path = urlsplit(page.url).path.split(";")[0]
        if profile.key != "hdfc_bank" or not path.startswith("/retail-app/my-accounts/"):
            raise ValueError("Not an account statement page")
        suffix = re.sub(r"\D", "", masked_number)[-4:]
        action = command.get("action")
        if action == "inspect":
            result = inspect_controls(page, suffix)
        elif action == "capture_observation":
            selector = str(command.get("account_selector", ""))
            if selector != "ng-select.bb-account-selector":
                raise ValueError("Reviewed HDFC account selector required")
            target = page.locator(selector)
            target.get_by_text("Available Balance", exact=True).wait_for(state="visible", timeout=15000)
            if not account_matches(target, suffix):
                raise ValueError("Selected account does not match")
            text = target.inner_text()
            amount = observed_available_amount(text)
            if amount is None:
                raise ValueError("An unambiguous available balance is required")
            if page.locator('input[type="password"]:visible, input[autocomplete="one-time-code"]:visible').count():
                raise ValueError("Authentication must be completed before capture")
            from app.statements.storage import save_generated, sha256_bytes
            image_path = image_sha = None
            if screenshot_callback is not None:
                png = page.screenshot(type="png", full_page=False,
                    mask=[page.locator('input, textarea, [contenteditable="true"]')])
                image_path = save_generated("bank_evidence", job.tenant_id,
                    f"observations/{job.account_id}/{job.id}.png", png)
                image_sha = sha256_bytes(png)
            observation = {"amount": str(amount), "currency": "INR", "balance_type": "available",
                "captured_at": datetime.now(UTC).isoformat(), "source": "bank_page_observed",
                "account_suffix": suffix, "image_path": image_path, "image_sha256": image_sha,
                "screenshot_available": image_path is not None, "job_id": str(job.id)}
            key = f"bank_observation:{job.account_id}"
            saved = db.scalar(select(SystemSetting).where(SystemSetting.tenant_id == job.tenant_id,
                                                          SystemSetting.key == key))
            if saved is None:
                saved = SystemSetting(tenant_id=job.tenant_id, key=key)
                db.add(saved)
            saved.value = observation
            result = {"observation_captured": True, "observation": {k: v for k, v in observation.items()
                if k not in ("image_path", "image_sha256")}, "controls": [{
                    "parent_class": "bb-account-selector", "text": masked(text)}]}
        elif action == "select_period":
            label = str(command.get("label", ""))
            if not re.fullmatch(r"(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{4}|(?:Current|Previous) Financial Year|Custom Date", label):
                raise ValueError("Not a statement period")
            selector = str(command.get("selector", ""))
            if not selector or len(selector) > 500:
                raise ValueError("Statement period selector required")
            target = page.locator(selector)
            if target.count() != 1 or not target.evaluate("n => n.tagName === 'SELECT'"):
                raise ValueError("Statement period control changed")
            target.select_option(label=label)
            result = {"selected_period": label}
        elif action in ("click", "download"):
            label = str(command.get("label", ""))
            if not ALLOWED_LABEL.fullmatch(label):
                raise ValueError("Not a read-only statement control")
            target = page.get_by_text(label, exact=True).and_(page.locator(":visible"))
            target.wait_for(state="visible", timeout=15000)
            if target.count() != 1:
                raise ValueError("Statement control is missing or ambiguous")
            if action == "download":
                selector = str(command.get("account_selector", ""))
                if not selector or len(selector) > 500 or selector in ("body", "html", "*"):
                    raise ValueError("Account identity selector required")
                if not account_matches(page.locator(selector), suffix):
                    raise ValueError("Selected account identity does not match")
                with page.expect_download(timeout=45000) as pending:
                    read_only_click(target, label)
                download = pending.value
                if download.failure() or download.path() is None:
                    raise ValueError("No valid download")
                file = Path(download.path())
                if not 0 < file.stat().st_size <= get_settings().max_upload_mb * 1024 * 1024:
                    raise ValueError("Statement file exceeds size limit")
                content = file.read_bytes()
                extension = Path(download.suggested_filename).suffix.lower()
                name = "bank-statement" + extension
                validate_download(content, name, ["pdf", "csv", "xlsx", "xls"])
                files = [(name, content)]
                if screenshot_callback:
                    with suppress(Exception):
                        screenshot_callback(page.screenshot(type="png", full_page=False,
                            mask=[page.locator('input, textarea, [contenteditable="true"]')]))
                result = {"statement_captured": True, "size_bytes": len(content)}
            else:
                read_only_click(target, label)
                result = {"clicked": label}
        else:
            raise ValueError("Unsupported review action")
        setting.value = {**command, "status": "succeeded", "result": result}
        if action == "capture_observation":
            job.progress_message = "Bank balance and screenshot saved"
    except Exception:
        # Browser exceptions can embed DOM/URLs. Keep them out of job and setting logs.
        setting.value = {**command, "status": "failed", "error": "Statement control changed or account/file validation failed"}
    db.commit()
    return files
