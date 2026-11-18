"""Demo Bank Simulator.

Produces deterministic, clearly labelled synthetic data so the full pipeline can be
evaluated without a real bank. Everything it produces is flagged ``is_demo`` by the
callers; it is never reported as a live bank connection.

Scenario switches (set on the demo connection) let tests exercise failure paths:
``scenario`` = ``ok`` | ``otp_required`` | ``provider_down`` | ``consent_expired`` | ``auth_failed``.
"""
from __future__ import annotations

import csv
import hashlib
import io
import random
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache

from app.integrations.base.adapter import (ConnectionMethod, AccountData, AuthenticationRequired, BalanceData, BankAdapter, Capability,
                                           ConnectionContext, ConnectionStatus, ConsentExpired, InstitutionData,
                                           ProviderUnavailable, StatementData, TransactionData, UserActionRequired)
from app.integrations.registry import register

EPOCH = date(2025, 1, 1)
CENT = Decimal("0.01")

DEMO_INSTITUTIONS: list[InstitutionData] = [
    InstitutionData("DEMO_GB_NORTHWIND", "Northwind Demo Bank", "GB", "NWDBGB2L", None, ["GBP"], True, True, True),
    InstitutionData("DEMO_IN_SAHYADRI", "Sahyadri Demo Bank", "IN", "SHYDINBB", None, ["INR"], True, True, True),
    InstitutionData("DEMO_US_HUDSON", "Hudson Demo Trust", "US", "HDSNUS33", None, ["USD"], True, True, True),
    InstitutionData("DEMO_DE_RHEIN", "Rhein Demo Bank", "DE", "RHDMDEFF", None, ["EUR"], True, True, True),
    InstitutionData("DEMO_AE_GULF", "Gulf Demo Bank", "AE", "GLFDAEAD", None, ["AED"], True, True, True),
]

_COUNTERPARTIES = {
    "credit": ["Customer receipt", "Invoice payment received", "Card settlement", "Interest credit", "Refund"],
    "debit": ["Supplier payment", "Payroll", "Office rent", "Utilities", "Bank charges", "Card purchase",
              "Software subscription", "Tax payment", "Travel expense", "Insurance premium"],
}


def _rng(*parts: object) -> random.Random:
    seed = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(seed[:16], 16))


def _q(v: float) -> Decimal:
    return Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP)


@lru_cache(maxsize=64)
def _ledger(account_key: str, currency: str, until: date) -> tuple[Decimal, tuple[tuple, ...]]:
    """Deterministic ledger from EPOCH to ``until`` → (opening at EPOCH, rows)."""
    scale = {"INR": 80, "AED": 3.7, "EUR": 0.92, "USD": 1, "GBP": 0.79}.get(currency, 1)
    r0 = _rng(account_key, "open")
    balance = _q(r0.uniform(40_000, 250_000) * scale)
    opening = balance
    rows: list[tuple] = []
    day = EPOCH
    seq = 0
    while day <= until:
        r = _rng(account_key, day.isoformat())
        if day.weekday() < 5:
            for _ in range(r.randint(0, 4)):
                seq += 1
                is_credit = r.random() < 0.42
                kind = "credit" if is_credit else "debit"
                label = r.choice(_COUNTERPARTIES[kind])
                amount = _q(r.uniform(50, 9_500 if is_credit else 6_000) * scale)
                if label == "Bank charges":
                    amount = _q(r.uniform(2, 40) * scale)
                signed = amount if is_credit else -amount
                balance += signed
                ref = f"DM{day.strftime('%y%m%d')}{seq:05d}"
                value_date = day + timedelta(days=1 if r.random() < 0.15 else 0)
                rows.append((day, value_date, f"{label} {r.randint(100, 999)}", ref, signed, balance))
        day += timedelta(days=1)
    return opening, tuple(rows)


@register
class DemoAdapter(BankAdapter):
    code = "demo"
    name = "Demo Bank Simulator"
    is_demo = True
    connection_method = ConnectionMethod.DEMO
    balance_types = frozenset({"ledger", "available"})
    capabilities = frozenset({Capability.INSTITUTIONS, Capability.ACCOUNTS, Capability.BALANCES,
                              Capability.TRANSACTIONS, Capability.STATEMENTS, Capability.REFRESH})

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _scenario(ctx: ConnectionContext) -> str:
        return str(ctx.credentials.get("scenario", "ok"))

    def _guard(self, ctx: ConnectionContext) -> None:
        sc = self._scenario(ctx)
        if sc == "provider_down":
            raise ProviderUnavailable("Demo provider is simulating downtime")
        if sc == "consent_expired":
            raise ConsentExpired("Demo consent has expired; re-authorisation required")
        if sc == "auth_failed":
            raise AuthenticationRequired("Demo bank rejected the stored authorisation")
        if sc == "otp_required" and not ctx.credentials.get("otp_verified"):
            raise UserActionRequired("Demo bank requires a one-time passcode", action="otp")

    @staticmethod
    def _key(ctx: ConnectionContext) -> str:
        return f"{ctx.institution_external_id}:{ctx.account_external_id or 'primary'}"

    # -- contract ------------------------------------------------------------
    def check_connection(self, ctx: ConnectionContext) -> ConnectionStatus:
        try:
            self._guard(ctx)
        except UserActionRequired as exc:
            return ConnectionStatus(False, "requires_auth", str(exc))
        except ConsentExpired as exc:
            return ConnectionStatus(False, "consent_expired", str(exc))
        except AuthenticationRequired as exc:
            return ConnectionStatus(False, "requires_auth", str(exc))
        except ProviderUnavailable as exc:
            return ConnectionStatus(False, "error", str(exc))
        return ConnectionStatus(True, "active", "Demo connection (synthetic data)")

    def refresh_connection(self, ctx: ConnectionContext) -> ConnectionStatus:
        return self.check_connection(ctx)

    def get_institutions(self, country_code: str) -> list[InstitutionData]:
        return [i for i in DEMO_INSTITUTIONS if country_code in ("*", i.country_code)]

    def get_accounts(self, ctx: ConnectionContext) -> list[AccountData]:
        self._guard(ctx)
        inst = next((i for i in DEMO_INSTITUTIONS if i.external_id == ctx.institution_external_id), None)
        currency = inst.currencies[0] if inst else "GBP"
        return [
            AccountData("primary", "Operating Account", currency, "current", "••••4821", "Demo Organisation Ltd"),
            AccountData("reserve", "Reserve Account", currency, "savings", "••••9034", "Demo Organisation Ltd"),
        ]

    def get_balances(self, ctx: ConnectionContext) -> list[BalanceData]:
        self._guard(ctx)
        today = datetime.now(UTC).date()
        currency = ctx.account_currency or "GBP"
        _, rows = _ledger(self._key(ctx), currency, today)
        ledger = rows[-1][5] if rows else _ledger(self._key(ctx), currency, today)[0]
        pending_hold = _q(_rng(self._key(ctx), "hold", today).uniform(0, 900))
        now = datetime.now(UTC).replace(microsecond=0)
        return [
            BalanceData("ledger", ledger, currency, now, {"demo": True}),
            BalanceData("available", ledger - pending_hold, currency, now, {"demo": True, "holds": str(pending_hold)}),
        ]

    def get_transactions(self, ctx: ConnectionContext, date_from: date, date_to: date) -> list[TransactionData]:
        self._guard(ctx)
        currency = ctx.account_currency or "GBP"
        _, rows = _ledger(self._key(ctx), currency, date_to)
        return [
            TransactionData(booking_date=d, value_date=vd, description=desc, reference=ref, amount=amt,
                            currency=currency, provider_transaction_id=ref, running_balance=bal,
                            raw={"demo": True})
            for (d, vd, desc, ref, amt, bal) in rows if date_from <= d <= date_to
        ]

    def get_statements(self, ctx: ConnectionContext, date_from: date, date_to: date) -> list[StatementData]:
        """One CSV statement per *completed* calendar month that overlaps the window."""
        self._guard(ctx)
        currency = ctx.account_currency or "GBP"
        today = datetime.now(UTC).date()
        out: list[StatementData] = []
        month = date(date_from.year, date_from.month, 1)
        while month <= date_to:
            nxt = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
            end = nxt - timedelta(days=1)
            if end < today and end >= EPOCH:
                out.append(self._render_statement(ctx, currency, month, end))
            month = nxt
        return out

    def _render_statement(self, ctx: ConnectionContext, currency: str, start: date, end: date) -> StatementData:
        opening0, rows = _ledger(self._key(ctx), currency, end)
        prior = [r for r in rows if r[0] < start]
        opening = prior[-1][5] if prior else opening0
        period = [r for r in rows if start <= r[0] <= end]
        closing = period[-1][5] if period else opening
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["DEMO STATEMENT - SYNTHETIC DATA - NOT A REAL BANK DOCUMENT"])
        w.writerow(["Account", self._key(ctx)])
        w.writerow(["Currency", currency])
        w.writerow(["Period", start.isoformat(), end.isoformat()])
        w.writerow([])
        w.writerow(["Date", "Value Date", "Description", "Reference", "Debit", "Credit", "Balance"])
        w.writerow([start.strftime("%d/%m/%Y"), "", "Opening Balance", "", "", "", f"{opening:.2f}"])
        for d, vd, desc, ref, amt, bal in period:
            w.writerow([d.strftime("%d/%m/%Y"), vd.strftime("%d/%m/%Y"), desc, ref,
                        f"{-amt:.2f}" if amt < 0 else "", f"{amt:.2f}" if amt > 0 else "", f"{bal:.2f}"])
        w.writerow([end.strftime("%d/%m/%Y"), "", "Closing Balance", "", "", "", f"{closing:.2f}"])
        ext_id = f"{self._key(ctx)}:{start:%Y-%m}"
        return StatementData(ext_id, start, end, f"demo-statement-{start:%Y-%m}.csv",
                             buf.getvalue().encode("utf-8"), currency)
