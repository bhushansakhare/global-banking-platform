import hashlib
import json
import re
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

DOMAINS = ("organizations", "users", "vendors", "expenses", "approvals", "finance", "logs")
Domain = Literal["organizations", "users", "vendors", "expenses", "approvals", "finance", "logs"]


class GatewayInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: SecretStr | None = Field(default=None, min_length=8, max_length=4096)
    domains: list[Domain] = Field(min_length=1, max_length=7)
    polling_enabled: bool = False
    interval_minutes: Literal[5, 15, 60] = 15

    @field_validator("api_key")
    @classmethod
    def safe_key(cls, value):
        if value and (not value.get_secret_value().isascii() or any(ord(c) < 33 or ord(c) > 126 for c in value.get_secret_value())):
            raise ValueError("The key must contain printable ASCII without whitespace")
        return value


class MappingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_org_id: str = Field(min_length=1, max_length=120)
    account_id: uuid.UUID
    routing_field: Literal["default", "vendor_id", "bank_account_id"] = "default"
    routing_value: str = Field(default="", max_length=120)
    amount_field: Literal["net_amount", "amount"] = "net_amount"
    amount_units: Literal["major", "minor"]
    date_field: Literal["due_date", "expected_payment_date", "payment_date"] = "due_date"
    reference_field: Literal["transaction_reference", "invoice_number"] = "invoice_number"
    direction: Literal["debit", "credit"] = "debit"
    enabled: bool = True

    @model_validator(mode="after")
    def route(self):
        if (self.routing_field == "default") != (self.routing_value == ""):
            raise ValueError("Default routing needs an empty value; specific routing needs an exact value")
        return self


class DestinationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_org_id: str = Field(min_length=1, max_length=120)
    webhook_key: SecretStr | None = Field(default=None, min_length=8, max_length=4096)
    auto_delivery: bool = False

    @field_validator("webhook_key")
    @classmethod
    def safe_key(cls, value):
        return GatewayInput.safe_key(value)


class SendInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: uuid.UUID
    external_org_id: str = Field(min_length=1, max_length=120)


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=1000)
    decision: Literal["replace_unmatched", "retain", "cancel"] = "retain"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str,
                                     allow_nan=False).encode()).hexdigest()


def safe_data(value):
    if isinstance(value, dict):
        return {k: safe_data(v) for k, v in value.items() if not re.search(
            r"password|secret|token|api.?key|authorization|^pin$|^otp$|^__proto__$|^constructor$|^prototype$", k, re.I)}
    if isinstance(value, list):
        return [safe_data(v) for v in value]
    return value


def is_test(value):
    flags = ("is_test", "is_demo", "is_mock", "test_only", "isDemo", "isMock", "test", "demo", "synthetic")
    return any(value.get(k) in (True, 1, "true") for k in flags) or bool(re.search(
        r"(?:^|[\s_.-])(?:test|demo|dummy|synthetic|sample)(?:[\s_.-]|$)",
        " ".join(str(value.get(k, "")) for k in ("title", "invoice_number", "id", "source")), re.I))


def currency_exponent(currency):
    zero = {"JPY", "KRW", "VND", "CLP", "ISK", "PYG", "RWF", "UGX", "VUV", "XAF", "XOF", "XPF", "BIF", "DJF", "GNF", "KMF"}
    three = {"BHD", "KWD", "OMR", "TND", "JOD", "LYD", "IQD"}
    two = {"INR", "USD", "EUR", "GBP", "CHF", "AUD", "CAD", "NZD", "SGD", "HKD", "CNY", "AED", "SAR", "ZAR", "BRL", "MXN", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "THB", "MYR", "IDR", "PHP", "PKR", "BDT", "LKR", "NPR", "TRY", "ILS", "EGP"}
    if currency in zero:
        return 0
    if currency in three:
        return 3
    if currency in two:
        return 2
    raise ValueError("Currency precision is not configured for this currency")


def amount_minor(value, currency, units):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)) or not str(value).strip():
        raise ValueError("A positive explicit amount is required")
    try:
        number = Decimal(str(value))
        minor = number * (10 ** currency_exponent(currency)) if units == "major" else number
        if not minor.is_finite() or minor <= 0 or minor != minor.to_integral_value() or minor > 9007199254740991:
            raise ValueError("Amount must convert to exact, positive minor units without rounding")
        return int(minor)
    except InvalidOperation:
        raise ValueError("Invalid amount") from None


def expectation(expense, finance, mapping, currency):
    if is_test(expense):
        raise ValueError("TEST_ONLY: synthetic sources are excluded")
    if str(expense.get("status", "")).lower() not in ("approved", "paid"):
        raise ValueError("EXPENSE_NOT_APPROVED: waiting for source approval")
    if expense.get("org_id") != mapping.external_org_id or expense.get("currency") != currency:
        raise ValueError("Organization and currency must exactly match the mapped account")
    options = mapping.options
    joined = [r for r in finance if r.get("expense_id") == expense["id"]]
    if any(r.get("org_id", expense["org_id"]) != expense["org_id"] for r in joined):
        raise ValueError("Finance organization mismatch")
    use_finance = options["date_field"] == "payment_date" or options["reference_field"] == "transaction_reference"
    if use_finance and len(joined) > 1:
        raise ValueError("AMBIGUOUS_FINANCE: allocation requires review")
    evidence = joined[0] if joined else {}
    if use_finance and (is_test(evidence) or evidence.get("currency", currency) != currency):
        raise ValueError("Finance evidence is synthetic or uses a different currency")
    when = evidence.get("payment_date") if options["date_field"] == "payment_date" else expense.get(options["date_field"])
    try:
        if not isinstance(when, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", when):
            raise ValueError()
        date.fromisoformat(when)
    except ValueError:
        raise ValueError("A valid explicitly mapped payment/due date is required") from None
    reference = evidence.get("transaction_reference") if options["reference_field"] == "transaction_reference" else expense.get("invoice_number")
    if not isinstance(reference, str) or not reference.strip() or len(reference) > 200:
        raise ValueError("The mapped payment reference is missing")
    minor = amount_minor(expense.get(options["amount_field"]), currency, options["amount_units"])
    return {"date": when, "reference": reference.strip(), "amount_minor": minor,
            "amount": str(Decimal(minor) / (10 ** currency_exponent(currency))), "currency": currency,
            "direction": options["direction"], "purpose": str(expense.get("title") or expense.get("purpose") or "Finzola expected payment")[:500]}
