"""Common, read-only bank integration contract.

Every provider adapter implements ONLY the capabilities it genuinely supports and
declares them in ``capabilities``. Calling an undeclared capability raises
``CapabilityNotSupported`` instead of returning fabricated data.

There are deliberately no payment / transfer operations in this interface.
"""
from __future__ import annotations

import abc
import enum
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar


class Capability(str, enum.Enum):
    INSTITUTIONS = "institutions"
    ACCOUNTS = "accounts"
    BALANCES = "balances"
    TRANSACTIONS = "transactions"
    STATEMENTS = "statements"
    REFRESH = "refresh"
    DISCONNECT = "disconnect"


# ---- errors -----------------------------------------------------------------
class ProviderError(Exception):
    code = "provider_error"
    retryable = False

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class ProviderUnavailable(ProviderError):
    code = "provider_unavailable"
    retryable = True


class RateLimited(ProviderError):
    code = "provider_rate_limited"
    retryable = True


class AuthenticationRequired(ProviderError):
    """User must re-authenticate with the bank/provider."""

    code = "auth_required"


class ConsentExpired(ProviderError):
    code = "consent_expired"


class UserActionRequired(ProviderError):
    """MFA / OTP / CAPTCHA step that a human must complete. Never bypassed."""

    code = "user_action_required"

    def __init__(self, message: str, *, action: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, details=details)
        self.action = action  # otp|captcha|reauth|consent


class DataNotReady(ProviderError):
    """The provider accepted the request but the data is produced asynchronously (e.g. an AA data
    session). The job is re-checked later; this is not a failure and must never be reported as success."""

    code = "data_not_ready"
    retryable = True

    def __init__(self, message: str, *, retry_after: int = 45, state: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.state = state or {}


class CapabilityNotSupported(ProviderError):
    code = "capability_not_supported"


class ConfigurationMissing(ProviderError):
    code = "provider_not_configured"


# ---- data contracts ---------------------------------------------------------
@dataclass(frozen=True)
class InstitutionData:
    external_id: str
    name: str
    country_code: str
    bic: str | None = None
    logo_url: str | None = None
    currencies: list[str] = field(default_factory=list)
    supports_balances: bool = False
    supports_transactions: bool = False
    supports_statements: bool = False


@dataclass(frozen=True)
class AccountData:
    external_account_id: str
    name: str
    currency: str
    account_type: str = "current"
    masked_number: str | None = None
    holder_name: str | None = None


@dataclass(frozen=True)
class BalanceData:
    balance_type: str  # available|ledger|current|statement_closing|statement_opening|reported
    amount: Decimal
    currency: str
    as_of: datetime
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TransactionData:
    booking_date: date
    amount: Decimal  # signed: credit positive / debit negative
    currency: str
    description: str
    value_date: date | None = None
    reference: str | None = None
    provider_transaction_id: str | None = None
    running_balance: Decimal | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StatementData:
    external_statement_id: str
    period_start: date
    period_end: date
    filename: str
    content: bytes
    currency: str | None = None


@dataclass(frozen=True)
class ConnectionStatus:
    ok: bool
    status: str  # active|requires_auth|consent_expired|error
    message: str = ""
    consent_expires_at: datetime | None = None


@dataclass
class ConnectionContext:
    """Everything an adapter needs for one connection. Credentials are decrypted just-in-time."""

    connection_id: str
    tenant_id: str
    institution_external_id: str
    external_reference: str | None
    credentials: dict[str, Any] = field(default_factory=dict)
    account_external_id: str | None = None
    account_currency: str | None = None
    account_masked_number: str | None = None
    account_type: str | None = None
    # Mutable, non-secret per-job state persisted between deferred attempts (e.g. an AA data-session id).
    job_state: dict[str, Any] = field(default_factory=dict)


class ConnectionMethod(str, enum.Enum):
    OFFICIAL_API = "official_bank_api"
    DATA_PROVIDER = "authorised_data_provider"   # open banking aggregator / RBI Account Aggregator
    BROWSER = "permitted_browser_automation"
    MANUAL = "manual_statement_import"           # fallback only
    DEMO = "demo"


# Fine-grained, user-facing capability matrix. Every adapter states each item explicitly.
CAPABILITY_KEYS = ("account_information", "available_balance", "ledger_balance", "current_balance",
                   "transactions", "statements", "historical_data", "automatic_sync")


class BankAdapter(abc.ABC):
    code: ClassVar[str]
    name: ClassVar[str]
    capabilities: ClassVar[frozenset[Capability]] = frozenset()
    is_demo: ClassVar[bool] = False
    min_request_interval: ClassVar[float] = 0.0
    connection_method: ClassVar[ConnectionMethod] = ConnectionMethod.DATA_PROVIDER
    # balance types this adapter can genuinely return (never relabelled)
    balance_types: ClassVar[frozenset[str]] = frozenset()

    def supports(self, cap: Capability) -> bool:
        return cap in self.capabilities

    def capability_matrix(self) -> dict[str, bool]:
        """Explicit true/false for every user-facing capability. Unsupported items are False, never omitted."""
        caps = self.capabilities
        remote = bool(caps & {Capability.BALANCES, Capability.TRANSACTIONS, Capability.STATEMENTS})
        return {
            "account_information": Capability.ACCOUNTS in caps,
            "available_balance": "available" in self.balance_types,
            "ledger_balance": "ledger" in self.balance_types,
            "current_balance": "current" in self.balance_types,
            "transactions": Capability.TRANSACTIONS in caps,
            "statements": Capability.STATEMENTS in caps,
            "historical_data": Capability.TRANSACTIONS in caps or Capability.STATEMENTS in caps,
            "automatic_sync": remote,
        }

    def is_configured(self) -> bool:
        """True when platform-level credentials for this provider are present (not per-user consent)."""
        return True

    def environment(self) -> str:
        """sandbox|production|demo|local — shown to users so test data is never mistaken for live data."""
        return "demo" if self.is_demo else "production"

    def prepare_sync(self, ctx: ConnectionContext, date_from: date, date_to: date) -> None:
        """Hook called once per collection before data calls (e.g. to create/await a data session).
        May raise DataNotReady."""
        return None

    def _require(self, cap: Capability) -> None:
        if cap not in self.capabilities:
            raise CapabilityNotSupported(f"{self.name} does not support '{cap.value}'")

    @abc.abstractmethod
    def check_connection(self, ctx: ConnectionContext) -> ConnectionStatus: ...

    def get_institutions(self, country_code: str) -> list[InstitutionData]:
        self._require(Capability.INSTITUTIONS)
        raise NotImplementedError

    def get_accounts(self, ctx: ConnectionContext) -> list[AccountData]:
        self._require(Capability.ACCOUNTS)
        raise NotImplementedError

    def get_balances(self, ctx: ConnectionContext) -> list[BalanceData]:
        self._require(Capability.BALANCES)
        raise NotImplementedError

    def get_transactions(self, ctx: ConnectionContext, date_from: date, date_to: date) -> list[TransactionData]:
        self._require(Capability.TRANSACTIONS)
        raise NotImplementedError

    def get_statements(self, ctx: ConnectionContext, date_from: date, date_to: date) -> list[StatementData]:
        self._require(Capability.STATEMENTS)
        raise NotImplementedError

    def refresh_connection(self, ctx: ConnectionContext) -> ConnectionStatus:
        self._require(Capability.REFRESH)
        raise NotImplementedError

    def disconnect_account(self, ctx: ConnectionContext) -> None:
        """Default: nothing to revoke remotely (local disconnect only)."""
        return None
