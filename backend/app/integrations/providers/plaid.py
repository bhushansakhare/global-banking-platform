"""Plaid adapter (US / CA / UK) — read-only products only.

Implemented against Plaid's public REST API. Status: implemented_unverified.
It must be verified with real sandbox/production credentials before being
relied on (see docs/BANK_INTEGRATIONS.md).

Credential flow: the frontend obtains a ``public_token`` via Plaid Link using a
``link_token`` created here; it is exchanged for an ``access_token`` which is
stored encrypted on the connection.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.core.config import get_settings
from app.integrations.base.adapter import (ConnectionMethod, AccountData, AuthenticationRequired, BalanceData, BankAdapter, Capability,
                                           ConfigurationMissing, ConnectionContext, ConnectionStatus,
                                           InstitutionData, ProviderError, RateLimited, TransactionData,
                                           UserActionRequired)
from app.integrations.base.http import request_json
from app.integrations.registry import register

_HOSTS = {"sandbox": "https://sandbox.plaid.com", "production": "https://production.plaid.com"}


@register
class PlaidAdapter(BankAdapter):
    connection_method = ConnectionMethod.DATA_PROVIDER
    balance_types = frozenset({"available", "current"})

    def is_configured(self) -> bool:
        s = get_settings()
        return bool(s.plaid_client_id and s.plaid_secret)

    def environment(self) -> str:
        return "production" if get_settings().plaid_env == "production" else "sandbox"

    code = "plaid"
    name = "Plaid"
    min_request_interval = 0.5
    capabilities = frozenset({Capability.INSTITUTIONS, Capability.ACCOUNTS, Capability.BALANCES,
                              Capability.TRANSACTIONS, Capability.DISCONNECT})

    def _auth(self) -> dict[str, str]:
        s = get_settings()
        if not (s.plaid_client_id and s.plaid_secret):
            raise ConfigurationMissing("Plaid is not configured (PLAID_CLIENT_ID / PLAID_SECRET)")
        return {"client_id": s.plaid_client_id, "secret": s.plaid_secret}

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        host = _HOSTS.get(get_settings().plaid_env, _HOSTS["sandbox"])
        try:
            return request_json(self.code, "POST", f"{host}{path}", json={**self._auth(), **body},
                                min_interval=self.min_request_interval)
        except ProviderError as exc:
            err = (exc.details.get("body") or {}) if isinstance(exc.details, dict) else {}
            code = err.get("error_code") if isinstance(err, dict) else None
            if code == "ITEM_LOGIN_REQUIRED":
                raise UserActionRequired("Plaid requires the user to re-authenticate via Link",
                                         action="reauth") from exc
            if code in ("INVALID_ACCESS_TOKEN", "ITEM_NOT_FOUND"):
                raise AuthenticationRequired("Plaid access token is invalid") from exc
            if code == "RATE_LIMIT_EXCEEDED":
                raise RateLimited("Plaid rate limit exceeded") from exc
            raise

    @staticmethod
    def _token(ctx: ConnectionContext) -> str:
        tok = ctx.credentials.get("access_token")
        if not tok:
            raise AuthenticationRequired("No Plaid access token on this connection")
        return str(tok)

    # -- link helpers (used by the connection API) ----------------------------
    def create_link_token(self, user_ref: str, country_codes: list[str]) -> str:
        body = {"client_name": get_settings().app_name, "language": "en", "country_codes": country_codes,
                "user": {"client_user_id": user_ref}, "products": ["transactions"]}
        return str(self._post("/link/token/create", body)["link_token"])

    def exchange_public_token(self, public_token: str) -> dict[str, str]:
        data = self._post("/item/public_token/exchange", {"public_token": public_token})
        return {"access_token": data["access_token"], "item_id": data["item_id"]}

    # -- contract ---------------------------------------------------------------
    def check_connection(self, ctx: ConnectionContext) -> ConnectionStatus:
        try:
            self._post("/item/get", {"access_token": self._token(ctx)})
            return ConnectionStatus(True, "active", "Plaid item reachable")
        except (UserActionRequired, AuthenticationRequired) as exc:
            return ConnectionStatus(False, "requires_auth", str(exc))
        except ProviderError as exc:
            return ConnectionStatus(False, "error", str(exc))

    def get_institutions(self, country_code: str) -> list[InstitutionData]:
        out: list[InstitutionData] = []
        offset = 0
        while True:
            data = self._post("/institutions/get", {"count": 500, "offset": offset,
                                                    "country_codes": [country_code.upper()],
                                                    "options": {"include_optional_metadata": True}})
            for i in data.get("institutions", []):
                products = set(i.get("products", []))
                out.append(InstitutionData(i["institution_id"], i["name"], country_code.upper(), None, None, [],
                                           "balance" in products, "transactions" in products, False))
            offset += 500
            if offset >= int(data.get("total", 0)) or offset >= 5000:
                return out

    def get_accounts(self, ctx: ConnectionContext) -> list[AccountData]:
        data = self._post("/accounts/get", {"access_token": self._token(ctx)})
        return [AccountData(a["account_id"], a.get("official_name") or a["name"],
                            (a.get("balances") or {}).get("iso_currency_code") or "USD",
                            a.get("subtype") or a.get("type") or "current",
                            f"••••{a['mask']}" if a.get("mask") else None)
                for a in data.get("accounts", [])]

    def get_balances(self, ctx: ConnectionContext) -> list[BalanceData]:
        body: dict[str, Any] = {"access_token": self._token(ctx)}
        if ctx.account_external_id:
            body["options"] = {"account_ids": [ctx.account_external_id]}
        data = self._post("/accounts/balance/get", body)
        now = datetime.now(UTC).replace(microsecond=0)
        out: list[BalanceData] = []
        for a in data.get("accounts", []):
            b = a.get("balances") or {}
            cur = b.get("iso_currency_code") or b.get("unofficial_currency_code") or "USD"
            if b.get("available") is not None:
                out.append(BalanceData("available", Decimal(str(b["available"])), cur, now, {"account": a["account_id"]}))
            if b.get("current") is not None:
                out.append(BalanceData("current", Decimal(str(b["current"])), cur, now, {"account": a["account_id"]}))
        return out

    def get_transactions(self, ctx: ConnectionContext, date_from: date, date_to: date) -> list[TransactionData]:
        out: list[TransactionData] = []
        offset = 0
        while True:
            body: dict[str, Any] = {"access_token": self._token(ctx), "start_date": date_from.isoformat(),
                                    "end_date": date_to.isoformat(), "options": {"count": 500, "offset": offset}}
            if ctx.account_external_id:
                body["options"]["account_ids"] = [ctx.account_external_id]
            data = self._post("/transactions/get", body)
            for t in data.get("transactions", []):
                if t.get("pending"):
                    continue
                # Plaid: positive amount = money leaving the account
                out.append(TransactionData(
                    booking_date=date.fromisoformat(t["date"]),
                    value_date=date.fromisoformat(t["authorized_date"]) if t.get("authorized_date") else None,
                    amount=-Decimal(str(t["amount"])),
                    currency=t.get("iso_currency_code") or "USD",
                    description=t.get("name") or t.get("merchant_name") or "",
                    reference=t.get("payment_meta", {}).get("reference_number"),
                    provider_transaction_id=t["transaction_id"],
                    raw={"category": t.get("personal_finance_category")},
                ))
            offset += 500
            if offset >= int(data.get("total_transactions", 0)):
                return out

    def disconnect_account(self, ctx: ConnectionContext) -> None:
        self._post("/item/remove", {"access_token": self._token(ctx)})
