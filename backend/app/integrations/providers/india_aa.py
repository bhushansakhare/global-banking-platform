"""India — RBI Account Aggregator (AA) through the Setu AA Gateway (FIU side).

How it works (RBI NBFC-AA framework, ReBIT specifications):
1. The platform (as a Financial Information User through Setu) creates a *consent request*
   for the customer's mobile-number handle (``POST {prefix}/consents``).
2. The customer opens the returned Setu/AA URL, discovers their bank account (e.g. HDFC Bank,
   a Financial Information Provider) and approves the consent inside the AA — the platform never
   sees bank credentials or OTPs.
3. While the consent is ACTIVE the platform creates *data sessions* (``POST {prefix}/sessions``)
   and reads the delivered FI data (``GET {prefix}/sessions/{id}``) — balances (account summary)
   and transactions. Sessions are asynchronous: until the data is ready ``DataNotReady`` is raised
   and the job is re-checked later.

Endpoints, statuses and field names follow Setu's public AA Gateway documentation
(https://docs.setu.co/data/account-aggregator). Status: ``implemented_unverified`` — it has NOT
been exercised against Setu with real FIU credentials. Authentication details differ between
Setu product versions, so the token URL / header scheme is configurable (see docs/BANK_INTEGRATIONS.md).

Statements (PDF) are NOT available through AA; transactions and the summary balance are.
"""
from __future__ import annotations

import threading
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.config import get_settings
from app.integrations.base.adapter import (AccountData, BalanceData, BankAdapter, Capability, ConfigurationMissing,
                                           ConnectionContext, ConnectionMethod, ConnectionStatus, ConsentExpired,
                                           DataNotReady, ProviderError, ProviderUnavailable, TransactionData,
                                           UserActionRequired)
from app.integrations.base.http import request_json
from app.integrations.registry import register

HOSTS = {"sandbox": "https://fiu-sandbox.setu.co", "production": "https://fiu.setu.co"}
# ReBIT purpose 102: customer spending patterns, budget or other reportings
PURPOSE = {"code": "102", "refUri": "https://api.rebit.org.in/aa/purpose/102.xml",
           "text": "Customer spending patterns, budget or other reportings", "category": {"type": "string"}}

CONSENT_ACTIVE = {"ACTIVE", "APPROVED"}
CONSENT_PENDING = {"PENDING"}
CONSENT_DEAD = {"REJECTED", "REVOKED", "EXPIRED", "PAUSED", "FAILED"}
SESSION_DONE = {"COMPLETED", "PARTIAL"}
SESSION_WAIT = {"PENDING", "ACTIVE"}
SESSION_DEAD = {"EXPIRED", "FAILED"}

_token_lock = threading.Lock()
_token: dict[str, Any] = {}


def _dec(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _dt(v: Any) -> datetime | None:
    if not v:
        return None
    s = str(v).replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        try:
            d = datetime.combine(date.fromisoformat(s[:10]), datetime.min.time())
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _last4(masked: str | None) -> str:
    digits = "".join(ch for ch in (masked or "") if ch.isalnum() and ch not in "xX")
    return digits[-4:]


@register
class IndiaAccountAggregatorAdapter(BankAdapter):
    code = "india_account_aggregator"
    name = "India Account Aggregator (Setu AA Gateway)"
    connection_method = ConnectionMethod.DATA_PROVIDER
    min_request_interval = 0.5
    capabilities = frozenset({Capability.ACCOUNTS, Capability.BALANCES, Capability.TRANSACTIONS,
                              Capability.REFRESH, Capability.DISCONNECT})
    # AA DEPOSIT summary exposes `currentBalance` as reported by the bank (FIP) — labelled "current",
    # never "available".
    balance_types = frozenset({"current"})

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}  # session id -> chosen account payload

    # ---- configuration -------------------------------------------------------
    def is_configured(self) -> bool:
        s = get_settings()
        return bool(s.setu_aa_client_id and s.setu_aa_client_secret and s.setu_aa_product_instance_id)

    def environment(self) -> str:
        return get_settings().setu_aa_env

    def _base(self) -> str:
        s = get_settings()
        return (s.setu_aa_base_url or HOSTS[s.setu_aa_env]).rstrip("/") + s.setu_aa_api_prefix.rstrip("/")

    def _headers(self) -> dict[str, str]:
        s = get_settings()
        if not self.is_configured():
            raise ConfigurationMissing(
                "India Account Aggregator is not configured: SETU_AA_CLIENT_ID, SETU_AA_CLIENT_SECRET and "
                "SETU_AA_PRODUCT_INSTANCE_ID are required (obtained after FIU onboarding on Setu Bridge).")
        h = {"Content-Type": "application/json", "x-product-instance-id": s.setu_aa_product_instance_id}
        if s.setu_aa_token_url:
            h["Authorization"] = f"Bearer {self._access_token()}"
        else:
            h["x-client-id"] = s.setu_aa_client_id
            h["x-client-secret"] = s.setu_aa_client_secret
        return h

    def _access_token(self) -> str:
        s = get_settings()
        with _token_lock:
            if _token.get("v") and _token.get("exp", 0) > time.time() + 60:
                return str(_token["v"])
            body = request_json(self.code, "POST", s.setu_aa_token_url,
                                json={"clientID": s.setu_aa_client_id, "secret": s.setu_aa_client_secret,
                                      "grant_type": "client_credentials"})
            data = body.get("data", body) if isinstance(body, dict) else {}
            tok = data.get("access_token") or data.get("token") or data.get("accessToken")
            if not tok:
                raise ProviderError("Setu token endpoint returned no access token")
            ttl = int(data.get("expiresIn") or data.get("expires_in") or 1800)
            _token.update(v=tok, exp=time.time() + ttl)
            return str(tok)

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None,
              params: dict[str, Any] | None = None) -> Any:
        return request_json(self.code, method, f"{self._base()}{path}", json=body, params=params,
                            headers=self._headers(), min_interval=self.min_request_interval)

    # ---- consent (used by ConnectionAuthorizationService) --------------------
    def create_consent(self, *, mobile: str, account_type: str | None, redirect_url: str,
                       reference: str) -> dict[str, Any]:
        s = get_settings()
        now = datetime.now(UTC).replace(microsecond=0)
        body: dict[str, Any] = {
            "consentDuration": {"unit": "MONTH", "value": str(s.setu_aa_consent_months)},
            "vua": mobile,
            "dataRange": {"from": (now - timedelta(days=s.setu_aa_history_days)).isoformat().replace("+00:00", "Z"),
                          "to": (now + timedelta(days=30 * s.setu_aa_consent_months)).isoformat()
                          .replace("+00:00", "Z")},
            "consentMode": "STORE",
            "fetchType": "PERIODIC",
            "consentTypes": ["PROFILE", "SUMMARY", "TRANSACTIONS"],
            "fiTypes": ["DEPOSIT"],
            "purpose": PURPOSE,
            "frequency": {"unit": "DAILY", "value": 6},
            "dataLife": {"unit": "MONTH", "value": s.setu_aa_consent_months},
            "redirectUrl": redirect_url,
            "context": [{"key": "accounttype", "value": "SAVINGS" if account_type == "savings" else "CURRENT"}]
            if account_type in ("savings", "current") else [],
            "additionalParams": {"tags": []},
        }
        resp = self._call("POST", "/consents", body)
        cid, url = resp.get("id"), resp.get("url")
        if not cid or not url:
            raise ProviderError("Account Aggregator did not return a consent id/URL", details={"status": resp.get("status")})
        return {"consent_id": cid, "url": url, "status": resp.get("status", "PENDING"),
                "consent_expiry": (resp.get("detail") or {}).get("consentExpiry"), "reference": reference}

    def consent_status(self, consent_id: str) -> dict[str, Any]:
        resp = self._call("GET", f"/consents/{consent_id}", params={"expanded": "true"})
        detail = resp.get("detail") or {}
        return {"status": str(resp.get("status", "")).upper(), "expires_at": _dt(detail.get("consentExpiry")),
                "accounts": resp.get("accountsLinked") or detail.get("accounts") or []}

    # ---- contract -------------------------------------------------------------
    def check_connection(self, ctx: ConnectionContext) -> ConnectionStatus:
        if not ctx.external_reference:
            return ConnectionStatus(False, "requires_auth", "No Account Aggregator consent has been requested yet")
        try:
            st = self.consent_status(ctx.external_reference)
        except ConsentExpired as exc:
            return ConnectionStatus(False, "consent_expired", str(exc))
        status = st["status"]
        if status in CONSENT_ACTIVE:
            return ConnectionStatus(True, "active", "AA consent is active", st["expires_at"])
        if status in CONSENT_PENDING:
            return ConnectionStatus(False, "requires_auth", "Waiting for the customer to approve the AA consent")
        if status in CONSENT_DEAD:
            return ConnectionStatus(False, "consent_expired", f"AA consent is {status.lower()}")
        return ConnectionStatus(False, "error", f"Unexpected AA consent status '{status or 'unknown'}'")

    def refresh_connection(self, ctx: ConnectionContext) -> ConnectionStatus:
        return self.check_connection(ctx)

    def prepare_sync(self, ctx: ConnectionContext, date_from: date, date_to: date) -> None:
        """Create a data session (once) and wait — across deferred job attempts — until it is delivered."""
        if not ctx.external_reference:
            raise UserActionRequired("Approve the Account Aggregator consent first", action="consent")
        sid = ctx.job_state.get("aa_session_id")
        if not sid:
            frm = datetime.combine(date_from, datetime.min.time(), UTC)
            to = datetime.combine(date_to, datetime.max.time().replace(microsecond=0), UTC)
            resp = self._call("POST", "/sessions", {
                "consentId": ctx.external_reference,
                "dataRange": {"from": frm.isoformat().replace("+00:00", "Z"),
                              "to": to.isoformat().replace("+00:00", "Z")},
                "format": "json"})
            sid = resp.get("id")
            if not sid:
                raise ProviderUnavailable("Account Aggregator did not create a data session")
            raise DataNotReady("AA data session created; waiting for the bank to deliver data",
                               retry_after=30, state={"aa_session_id": sid})
        resp = self._call("GET", f"/sessions/{sid}")
        status = str(resp.get("status", "")).upper()
        if status in SESSION_WAIT:
            raise DataNotReady(f"AA data session {status.lower()}", retry_after=45, state={"aa_session_id": sid})
        if status in SESSION_DEAD:
            # start a fresh session on the next attempt
            raise DataNotReady(f"AA data session {status.lower()}; a new session will be requested",
                               retry_after=60, state={"aa_session_id": None})
        if status not in SESSION_DONE:
            raise ProviderUnavailable(f"Unexpected AA session status '{status or 'unknown'}'")
        self._data[ctx.connection_id] = self._select_account(ctx, resp)

    def _select_account(self, ctx: ConnectionContext, session: dict[str, Any]) -> dict[str, Any]:
        """Pick the delivered account matching this platform account (by link reference or last 4 digits)."""
        want_ref = ctx.account_external_id
        want4 = _last4(ctx.account_masked_number)
        candidates: list[dict[str, Any]] = []
        for fip in session.get("fips") or []:
            for acc in fip.get("accounts") or []:
                data = (acc.get("data") or {}).get("account") or acc.get("decryptedFI", {}).get("account")
                status = str(acc.get("FIstatus") or acc.get("status") or "").upper()
                if not data:
                    if status in ("DENIED", "TIMEOUT", "FAILED"):
                        continue
                    continue
                candidates.append({"fip_id": fip.get("fipID"), "link_ref": acc.get("linkRefNumber")
                                   or data.get("linkedAccRef"), "masked": acc.get("maskedAccNumber")
                                   or data.get("maskedAccNumber"), "account": data})
        if not candidates:
            raise ProviderUnavailable("The bank did not deliver account data in this AA session (partial/denied)")
        for c in candidates:
            if want_ref and c["link_ref"] == want_ref:
                return c
        if want4:
            hits = [c for c in candidates if _last4(c["masked"]) == want4]
            if len(hits) == 1:
                return hits[0]
            if not hits:
                raise UserActionRequired(
                    f"The approved AA consent does not include the account ending {want4}. Re-authorise and select "
                    "that account in the Account Aggregator.", action="consent")
        if len(candidates) == 1:
            return candidates[0]
        raise UserActionRequired("Several accounts were shared; add the account number so the right one can be "
                                 "matched, then sync again.", action="consent")

    def _payload(self, ctx: ConnectionContext) -> dict[str, Any]:
        p = self._data.get(ctx.connection_id)
        if p is None:
            raise ProviderError("prepare_sync must complete before reading AA data")
        return p

    def matched_account(self, ctx: ConnectionContext) -> AccountData:
        p = self._payload(ctx)
        acc = p["account"]
        summary = acc.get("summary") or {}
        holder = ((acc.get("profile") or {}).get("holders") or {}).get("holder")
        if isinstance(holder, list):
            holder = holder[0] if holder else None
        return AccountData(external_account_id=p["link_ref"] or p["masked"] or "aa",
                           name=summary.get("type") or acc.get("type") or "Deposit account",
                           currency=summary.get("currency") or "INR",
                           account_type=str(summary.get("type") or "savings").lower(),
                           masked_number=p["masked"], holder_name=(holder or {}).get("name") if holder else None)

    def get_accounts(self, ctx: ConnectionContext) -> list[AccountData]:
        return [self.matched_account(ctx)]

    def get_balances(self, ctx: ConnectionContext) -> list[BalanceData]:
        acc = self._payload(ctx)["account"]
        summary = acc.get("summary") or {}
        amount = _dec(summary.get("currentBalance"))
        if amount is None:
            return []
        as_of = _dt(summary.get("balanceDateTime")) or _dt((acc.get("transactions") or {}).get("endDate"))
        if as_of is None:
            # never invent a timestamp: without one the balance is not recorded
            return []
        return [BalanceData("current", amount, summary.get("currency") or ctx.account_currency or "INR", as_of,
                            {"source": "account_aggregator", "fip": self._payload(ctx).get("fip_id")})]

    def get_transactions(self, ctx: ConnectionContext, date_from: date, date_to: date) -> list[TransactionData]:
        acc = self._payload(ctx)["account"]
        txs = (acc.get("transactions") or {}).get("transaction") or []
        if isinstance(txs, dict):
            txs = [txs]
        cur = (acc.get("summary") or {}).get("currency") or ctx.account_currency or "INR"
        out: list[TransactionData] = []
        for t in txs:
            amt = _dec(t.get("amount"))
            ts = _dt(t.get("transactionTimestamp")) or _dt(t.get("valueDate"))
            if amt is None or ts is None:
                continue
            kind = str(t.get("type", "")).upper()
            if kind not in ("CREDIT", "DEBIT"):
                continue
            signed = abs(amt) if kind == "CREDIT" else -abs(amt)
            vd = _dt(t.get("valueDate"))
            out.append(TransactionData(
                booking_date=ts.date(), value_date=vd.date() if vd else None, amount=signed, currency=cur,
                description=str(t.get("narration") or t.get("mode") or "").strip(),
                reference=t.get("reference") or None, provider_transaction_id=t.get("txnId") or None,
                running_balance=_dec(t.get("currentBalance")),
                raw={"mode": t.get("mode"), "source": "account_aggregator"}))
        return [t for t in out if date_from <= t.booking_date <= date_to]

    def disconnect_account(self, ctx: ConnectionContext) -> None:
        if ctx.external_reference and self.is_configured():
            self._call("POST", f"/consents/{ctx.external_reference}/revoke")
