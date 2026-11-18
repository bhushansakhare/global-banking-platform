"""Framework for *permitted* bank-portal automation using Playwright.

Rules enforced by design:
* An adapter subclass must exist per bank, with selectors verified against that bank's
  portal. There is no universal login script and no selectors are guessed here.
* ``permitted_by_bank_terms`` must be set to a reference (URL/contract clause) proving the
  bank allows automated access; otherwise the adapter refuses to run.
* CAPTCHA and MFA are never bypassed. When encountered, the adapter raises
  ``UserActionRequired``. Bank secrets must be entered directly on the official page.
  The legacy OTP vault below is retained exclusively for synthetic demo jobs.
"""
from __future__ import annotations

import abc
import threading
import time
from typing import ClassVar

from app.integrations.base.adapter import (BankAdapter, Capability, ConfigurationMissing, ConnectionContext,
                                           UserActionRequired)


class OtpVault:
    """Short-lived, in-memory, single-use OTP hand-off between the API and a worker."""

    TTL = 300.0

    def __init__(self) -> None:
        self._items: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def put(self, job_id: str, otp: str) -> None:
        with self._lock:
            self._items[job_id] = (otp, time.monotonic() + self.TTL)

    def take(self, job_id: str) -> str | None:
        with self._lock:
            item = self._items.pop(job_id, None)
        if item and item[1] > time.monotonic():
            return item[0]
        return None


otp_vault = OtpVault()


class BrowserBankAdapter(BankAdapter, abc.ABC):
    capabilities = frozenset({Capability.STATEMENTS})
    permitted_by_bank_terms: ClassVar[str | None] = None  # reference proving automation is permitted
    login_url: ClassVar[str]
    headless: ClassVar[bool] = False

    def _ensure_permitted(self) -> None:
        if not self.permitted_by_bank_terms:
            raise ConfigurationMissing(f"{self.name}: automation not enabled — no documented bank permission")

    def _browser(self):  # pragma: no cover - requires installed browsers
        from playwright.sync_api import sync_playwright
        self._ensure_permitted()
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=self.headless)
        return pw, browser

    def require_otp(self, job_id: str, prompt: str = "Enter the one-time passcode sent by your bank") -> str:
        if not self.is_demo:
            raise UserActionRequired("Complete verification directly on the official bank page", action="reauth")
        otp = otp_vault.take(job_id)
        if otp is None:
            raise UserActionRequired(prompt, action="otp")
        return otp

    @staticmethod
    def detect_captcha(page) -> bool:  # pragma: no cover - requires a browser
        """Detect common CAPTCHA widgets so the run pauses for a human instead of proceeding."""
        for sel in ("iframe[src*='recaptcha']", "iframe[src*='hcaptcha']", "[class*='captcha']"):
            if page.locator(sel).count():
                return True
        return False

    def pause_for_captcha(self) -> None:
        raise UserActionRequired("The bank portal is showing a CAPTCHA. Complete it manually, then resume.",
                                 action="captcha")

    @abc.abstractmethod
    def login(self, page, ctx: ConnectionContext, job_id: str) -> None:
        """Bank-specific, verified login flow."""
