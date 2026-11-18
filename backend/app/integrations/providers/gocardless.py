"""GoCardless Bank Account Data (formerly Nordigen) — UK/EU PSD2 AIS.

Implemented against the public v2 API. Status: implemented_unverified. Verify with
real credentials and confirm current onboarding availability before production use.
"""
from __future__ import annotations

import threading
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.core.config import get_settings
from app.integrations.base.adapter import (ConnectionMethod, AccountData, AuthenticationRequired, BalanceData, BankAdapter, Capability,
                                           ConfigurationMissing, ConnectionContext, ConnectionStatus,
                                           ConsentExpired, InstitutionData, ProviderError, TransactionData)
from app.integrations.base.http import request_json
from app.integrations.registry import register

BASE = "https://bankaccountdata.gocardless.com/api/v2"
_token_lock = threading.Lock()
_token: dict[str, Any] = {}


def _balance_type(kind: str) -> str:
    k = kind.lower()
    if "available" in k:
        return "available"
    if "booked" in k:
        return "ledger"
    return "reported"


@register
class GoCardlessAdapter(BankAdapter):
    code = "gocardless_bad"
    name = "GoCardless Bank Account Data"
    connection_method = ConnectionMethod.DATA_PROVIDER
    balance_types = frozenset({"available", "ledger", "reported"})

    def is_configured(self) -> bool:
        s = get_settings()
        return bool(s.gocardless_secret_id and s.gocardless_secret_key)

    min_request_interval = 1.0
    capabilities = frozenset({Capability.INSTITUTIONS, Capability.ACCOUNTS, Capability.BALANCES,
                              Capability.TRANSACTIONS, Capability.REFRESH, Capability.DISCONNECT})

    def _access_token(self) -> str:
        s = get_settings()
        if not (s.gocardless_secret_id and s.gocardless_secret_key):
            raise ConfigurationMissing("GoCardless is not configured (GOCARDLESS_SECRET_ID / GOCARDLESS_SECRET_KEY)")
        with _token_lock:
            if _token.get("access") and _token.get("exp", 0) > time.time() + 60:
                return str(_token["access"])
            data = request_json(self.code, "POST", f"{BASE}/token/new/",
                                json={"secret_id": s.gocardless_secret_id, "secret_key": s.gocardless_secret_key})
            _token.update(access=data["access"], exp=time.time() + int(data.get("access_expires", 3600)))
            return str(data["access"])

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return request_json(self.code, "GET", f"{BASE}{path}", params=params, min_interval=self.min_request_interval,
                            headers={"Authorization": f"Bearer {self._access_token()}", "accept": "application/json"})

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        return request_json(self.code, "POST", f"{BASE}{path}", json=body, min_interval=self.min_request_interval,
                            headers={"Authorization": f"Bearer {self._access_token()}"})

    # -- consent flow (used by the connection API) ----------------------------
    def create_requisition(self, institution_id: str, redirect_url: str, reference: str,
                           history_days: int = 90, valid_days: int = 90) -> dict[str, Any]:
        agreement = self._post("/agreements/enduser/", {
            "institution_id": institution_id, "max_historical_days": history_days,
            "access_valid_for_days": valid_days, "access_scope": ["balances", "details", "transactions"],
        })
        req = self._post("/requisitions/", {"redirect": redirect_url, "institution_id": institution_id,
                                            "reference": reference, "agreement": agreement["id"],
                                            "user_language": "EN"})
        return {"requisition_id": req["id"], "link": req["link"], "agreement_id": agreement["id"]}

    # -- contract ---------------------------------------------------------------
    def check_connection(self, ctx: ConnectionContext) -> ConnectionStatus:
        if not ctx.external_reference:
            return ConnectionStatus(False, "requires_auth", "No requisition on this connection")
        try:
            req = self._get(f"/requisitions/{ctx.external_reference}/")
        except ConsentExpired as exc:
            return ConnectionStatus(False, "consent_expired", str(exc))
        except AuthenticationRequired as exc:
            return ConnectionStatus(False, "requires_auth", str(exc))
        except ProviderError as exc:
            return ConnectionStatus(False, "error", str(exc))
        status = req.get("status")
        if status == "LN":
            return ConnectionStatus(True, "active", "Requisition linked")
        if status in ("EX", "RJ", "SU"):
            return ConnectionStatus(False, "consent_expired", f"Requisition status {status}")
        return ConnectionStatus(False, "requires_auth", f"Requisition awaiting user consent ({status})")

    def refresh_connection(self, ctx: ConnectionContext) -> ConnectionStatus:
        return self.check_connection(ctx)

    def get_institutions(self, country_code: str) -> list[InstitutionData]:
        data = self._get("/institutions/", {"country": country_code.lower()})
        return [InstitutionData(i["id"], i["name"], country_code.upper(), i.get("bic"), i.get("logo"), [],
                                True, True, False) for i in data]

    def get_accounts(self, ctx: ConnectionContext) -> list[AccountData]:
        req = self._get(f"/requisitions/{ctx.external_reference}/")
        out: list[AccountData] = []
        for acc_id in req.get("accounts", []):
            det = self._get(f"/accounts/{acc_id}/details/").get("account", {})
            iban = det.get("iban") or ""
            out.append(AccountData(acc_id, det.get("name") or det.get("product") or "Account",
                                   det.get("currency") or "EUR", det.get("cashAccountType") or "current",
                                   f"••••{iban[-4:]}" if iban else None, det.get("ownerName")))
        return out

    def get_balances(self, ctx: ConnectionContext) -> list[BalanceData]:
        if not ctx.account_external_id:
            raise ProviderError("Account external id required for GoCardless balances")
        data = self._get(f"/accounts/{ctx.account_external_id}/balances/")
        out = []
        for b in data.get("balances", []):
            amt = b.get("balanceAmount", {})
            ref = b.get("referenceDate") or b.get("lastChangeDateTime")
            as_of = datetime.fromisoformat(ref.replace("Z", "+00:00")) if ref and "T" in ref else (
                datetime.combine(date.fromisoformat(ref), datetime.min.time(), UTC) if ref else datetime.now(UTC))
            if as_of.tzinfo is None:
                as_of = as_of.replace(tzinfo=UTC)
            out.append(BalanceData(_balance_type(b.get("balanceType", "")), Decimal(str(amt.get("amount", "0"))),
                                   amt.get("currency") or ctx.account_currency or "EUR", as_of,
                                   {"balanceType": b.get("balanceType")}))
        return out

    def get_transactions(self, ctx: ConnectionContext, date_from: date, date_to: date) -> list[TransactionData]:
        data = self._get(f"/accounts/{ctx.account_external_id}/transactions/",
                         {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()})
        out = []
        for t in data.get("transactions", {}).get("booked", []):
            amt = t.get("transactionAmount", {})
            desc = t.get("remittanceInformationUnstructured") or " ".join(
                t.get("remittanceInformationUnstructuredArray", []) or []) or t.get("creditorName") or \
                t.get("debtorName") or ""
            out.append(TransactionData(
                booking_date=date.fromisoformat(t["bookingDate"]),
                value_date=date.fromisoformat(t["valueDate"]) if t.get("valueDate") else None,
                amount=Decimal(str(amt.get("amount", "0"))), currency=amt.get("currency") or "EUR",
                description=desc, reference=t.get("endToEndId") or t.get("entryReference"),
                provider_transaction_id=t.get("transactionId") or t.get("internalTransactionId"),
                running_balance=Decimal(str(t["balanceAfterTransaction"]["balanceAmount"]["amount"]))
                if t.get("balanceAfterTransaction") else None,
                raw={"bankTransactionCode": t.get("bankTransactionCode")},
            ))
        return out

    def disconnect_account(self, ctx: ConnectionContext) -> None:
        if ctx.external_reference:
            request_json(self.code, "DELETE", f"{BASE}/requisitions/{ctx.external_reference}/",
                         headers={"Authorization": f"Bearer {self._access_token()}"})
