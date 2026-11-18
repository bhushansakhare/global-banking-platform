"""Adapter registry + provider catalogue metadata (seeded into bank_providers)."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.integrations.base.adapter import BankAdapter, CapabilityNotSupported

_ADAPTERS: dict[str, type[BankAdapter]] = {}


def register(cls: type[BankAdapter]) -> type[BankAdapter]:
    _ADAPTERS[cls.code] = cls
    return cls


def get_adapter(code: str) -> BankAdapter:
    _load()
    cls = _ADAPTERS.get(code)
    if cls is None:
        raise CapabilityNotSupported(f"No adapter implementation is available for provider '{code}'")
    return cls()


def has_adapter(code: str) -> bool:
    _load()
    return code in _ADAPTERS


def registered_codes() -> list[str]:
    _load()
    return sorted(_ADAPTERS)


_loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    # import side-effect registration
    from app.integrations.providers import browser, demo, gocardless, india_aa, manual_upload, plaid  # noqa: F401
    _loaded = True


@dataclass(frozen=True)
class ProviderSpec:
    code: str
    name: str
    provider_type: str
    countries: list[str]
    currencies: list[str]
    capabilities: list[str]
    auth_method: str
    consent_required: bool
    refresh_frequency: str
    integration_status: str
    is_demo: bool = False
    documentation_url: str | None = None
    min_request_interval_seconds: float = 0
    notes: str = ""
    extra: dict = field(default_factory=dict)


EU = ["AT", "BE", "DE", "DK", "ES", "FI", "FR", "IE", "IT", "NL", "PL", "PT", "SE"]

PROVIDER_CATALOGUE: list[ProviderSpec] = [
    ProviderSpec(
        code="manual_upload", name="Statement Upload (any bank)", provider_type="manual", countries=["*"],
        currencies=["*"], capabilities=["statements"], auth_method="none", consent_required=False,
        refresh_frequency="on upload", integration_status="production_verified",
        notes="Works with any institution worldwide: users upload official statement exports (PDF/XLSX/XLS/CSV). "
              "No bank credentials are stored.",
    ),
    ProviderSpec(
        code="demo", name="Demo Bank Simulator", provider_type="demo", countries=["GB", "IN", "US", "DE", "AE"],
        currencies=["GBP", "INR", "USD", "EUR", "AED"],
        capabilities=["institutions", "accounts", "balances", "transactions", "statements", "refresh"],
        auth_method="none", consent_required=False, refresh_frequency="daily", integration_status="demo",
        is_demo=True,
        notes="Generates clearly labelled synthetic data for evaluation. Never a live bank connection.",
    ),
    ProviderSpec(
        code="gocardless_bad", name="GoCardless Bank Account Data (PSD2)", provider_type="aggregator",
        countries=["GB", *EU], currencies=["GBP", "EUR", "SEK", "DKK", "PLN"],
        capabilities=["institutions", "accounts", "balances", "transactions"], auth_method="oauth_redirect_consent",
        consent_required=True, refresh_frequency="up to 4 times per day (provider limit)",
        integration_status="implemented_unverified",
        documentation_url="https://developer.gocardless.com/bank-account-data/overview",
        min_request_interval_seconds=1.0,
        notes="Client implemented against the public v2 API. Requires SECRET_ID/SECRET_KEY and independent "
              "verification. Check current onboarding availability with the provider.",
    ),
    ProviderSpec(
        code="plaid", name="Plaid", provider_type="aggregator", countries=["US", "CA", "GB"],
        currencies=["USD", "CAD", "GBP"], capabilities=["institutions", "accounts", "balances", "transactions"],
        auth_method="plaid_link_public_token", consent_required=True, refresh_frequency="provider managed",
        integration_status="implemented_unverified", documentation_url="https://plaid.com/docs/api/",
        min_request_interval_seconds=0.5,
        notes="Client implemented against the public REST API (/institutions/get, /accounts/balance/get, "
              "/transactions/sync). Requires PLAID_CLIENT_ID/PLAID_SECRET and independent verification.",
    ),
    ProviderSpec(
        code="india_account_aggregator", name="India Account Aggregator (Setu AA Gateway)",
        provider_type="account_aggregator", countries=["IN"], currencies=["INR"],
        capabilities=["accounts", "balances", "transactions", "refresh"], auth_method="aa_consent_artefact",
        consent_required=True, refresh_frequency="up to 6 fetches/day per consent (configurable)",
        integration_status="implemented_unverified",
        documentation_url="https://docs.setu.co/data/account-aggregator",
        min_request_interval_seconds=0.5,
        notes="RBI NBFC-AA framework. The customer approves a consent inside the Account Aggregator (no bank "
              "password is shared with this platform). Requires FIU onboarding with Setu (SETU_AA_* settings). "
              "Client implemented against Setu's public AA Gateway docs; unverified until tested with real FIU "
              "credentials. Provides the bank-reported current balance and transactions — not PDF statements.",
    ),
    ProviderSpec(
        code="browser_automation", name="Bank Portal Automation (permitted banks only)", provider_type="browser",
        countries=[], currencies=[], capabilities=["statements"], auth_method="user_assisted_mfa",
        consent_required=True, refresh_frequency="user-assisted", integration_status="implemented_unverified",
        notes="Configurable, visible Playwright sessions. Reviewed bank profiles require documented permission "
              "and account identity checks. No banking password or OTP is collected; login is interactive.",
    ),
]
