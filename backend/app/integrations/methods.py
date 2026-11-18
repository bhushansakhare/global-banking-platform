"""ConnectionMethodService — which connection methods are *genuinely* available for a bank.

Output for each of the four methods (official API, authorised data provider, permitted browser
automation, manual import) states availability, a status, the reason and concrete setup steps.
Nothing is offered as available unless an adapter exists and its platform credentials are present.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.banks.directory import lookup
from app.integrations.base.adapter import ConnectionMethod
from app.integrations.registry import get_adapter, has_adapter
from app.models.banking import BankProvider, Institution

METHOD_LABELS = {
    ConnectionMethod.OFFICIAL_API.value: "Official Bank API",
    ConnectionMethod.DATA_PROVIDER.value: "Authorised Banking Data Provider",
    ConnectionMethod.BROWSER.value: "Permitted Browser Automation",
    ConnectionMethod.MANUAL.value: "Manual Statement Import (fallback)",
    ConnectionMethod.DEMO.value: "Demo Bank Simulator",
}

# availability statuses
AVAILABLE = "available"
PROVIDER_SETUP_REQUIRED = "provider_setup_required"
AWAITING_BANK_APPROVAL = "awaiting_bank_approval"
NOT_PERMITTED = "not_permitted"
UNSUPPORTED = "unsupported"


@dataclass
class MethodOption:
    method: str
    label: str
    available: bool
    status: str
    reason: str
    provider_code: str | None = None
    provider_name: str | None = None
    integration_status: str | None = None
    environment: str | None = None
    automatic: bool = False
    capabilities: dict[str, bool] = field(default_factory=dict)
    setup_steps: list[str] = field(default_factory=list)
    reference: str | None = None
    recommended: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _provider_for_country(db: Session, method: ConnectionMethod, country: str) -> list[BankProvider]:
    types = {ConnectionMethod.DATA_PROVIDER: ("aggregator", "account_aggregator"),
             ConnectionMethod.OFFICIAL_API: ("bank_api",)}[method]
    rows = db.scalars(select(BankProvider).where(BankProvider.provider_type.in_(types))
                      .order_by(BankProvider.name)).all()
    return [p for p in rows if country in (p.countries or []) and has_adapter(p.code)]


def _provider_option(p: BankProvider, method: ConnectionMethod, institution: Institution | None,
                     bank_known_fip: bool | None) -> MethodOption:
    adapter = get_adapter(p.code)
    caps = adapter.capability_matrix()
    configured = adapter.is_configured()
    opt = MethodOption(method=method.value, label=METHOD_LABELS[method.value], available=False, status="",
                       reason="", provider_code=p.code, provider_name=p.name, integration_status=p.integration_status,
                       environment=adapter.environment(), automatic=caps["automatic_sync"], capabilities=caps,
                       reference=p.documentation_url)
    # aggregators such as GoCardless/Plaid only reach banks in their own institution list
    if p.provider_type == "aggregator" and (institution is None or institution.provider_id != p.id):
        opt.status, opt.reason = UNSUPPORTED, (
            f"This bank is not in the {p.name} institution list. Import the provider's institutions "
            "(Institutions page) and choose the bank from that list.")
        return opt
    if not configured:
        opt.status = PROVIDER_SETUP_REQUIRED
        opt.reason = (f"{p.name} is implemented but the platform has no provider credentials yet. "
                      "An administrator must complete provider onboarding and add the credentials.")
        if p.code == "india_account_aggregator":
            opt.setup_steps = [
                "Register your organisation on Setu Bridge (bridge.setu.co) and create an Account Aggregator "
                "FIU product instance (sandbox first)",
                "Copy the client id, client secret and product instance id into backend\\.env as "
                "SETU_AA_CLIENT_ID, SETU_AA_CLIENT_SECRET, SETU_AA_PRODUCT_INSTANCE_ID (SETU_AA_ENV=sandbox)",
                "Register the notification URL http(s)://<your-host>/api/v1/webhooks/aa/setu?token=<SETU_AA_WEBHOOK_TOKEN>",
                "Restart the platform, then click Connect bank on the account",
                "For live data: complete Setu production review / Sahamati onboarding and set SETU_AA_ENV=production",
            ]
        return opt
    opt.available, opt.status = True, AVAILABLE
    opt.reason = "Ready to authorise. You approve access inside the provider — your bank password is never shared."
    if p.integration_status == "implemented_unverified":
        opt.reason += " Integration not yet verified with live data; the first sync result is shown exactly as received."
    if p.code == "india_account_aggregator" and bank_known_fip is not True:
        opt.reason += " Confirm your bank appears in the Account Aggregator's bank list during consent."
    if opt.environment == "sandbox":
        opt.reason += " SANDBOX environment: data comes from the provider's test banks, not your real account."
    return opt


def methods_for(db: Session, *, country_code: str, institution: Institution | None = None,
                bank_name: str | None = None, tenant_id=None) -> list[dict[str, Any]]:
    country = (country_code or (institution.country_code if institution else "")).upper()
    name = bank_name or (institution.name if institution else "")
    facts = lookup(name, country)
    out: list[MethodOption] = []

    # demo institutions only offer the simulator
    if institution is not None and institution.is_demo:
        p = institution.provider
        adapter = get_adapter(p.code)
        return [MethodOption(method=ConnectionMethod.DEMO.value, label=METHOD_LABELS["demo"], available=True,
                             status=AVAILABLE, reason="Synthetic data for evaluation only — never a real bank.",
                             provider_code=p.code, provider_name=p.name, integration_status=p.integration_status,
                             environment="demo", automatic=True, capabilities=adapter.capability_matrix(),
                             recommended=True).as_dict()]

    # 1. Official bank API
    official = _provider_for_country(db, ConnectionMethod.OFFICIAL_API, country)
    if official:
        out.extend(_provider_option(p, ConnectionMethod.OFFICIAL_API, institution, None) for p in official)
    else:
        f = facts.official_api if facts else None
        out.append(MethodOption(
            method=ConnectionMethod.OFFICIAL_API.value, label=METHOD_LABELS["official_bank_api"], available=False,
            status=f.status if f else UNSUPPORTED,
            reason=f.reason if f else "No official API integration exists for this bank in the platform.",
            setup_steps=list(f.setup_steps) if f else [], reference=f.reference if f else None))

    # 2. Authorised data providers (Open Banking aggregators / RBI Account Aggregator)
    providers = _provider_for_country(db, ConnectionMethod.DATA_PROVIDER, country)
    dp = [_provider_option(p, ConnectionMethod.DATA_PROVIDER, institution, facts.aa_fip if facts else None)
          for p in providers]
    reachable = [o for o in dp if o.status != UNSUPPORTED]
    if reachable:
        out.extend(reachable)
    elif dp:
        out.append(dp[0])
    else:
        out.append(MethodOption(method=ConnectionMethod.DATA_PROVIDER.value, label=METHOD_LABELS["authorised_data_provider"],
                                available=False, status=UNSUPPORTED,
                                reason=f"No authorised banking data provider is integrated for country {country or '—'}."))

    # 3. Browser automation — only per-bank adapters with documented permission
    f = facts.browser if facts else None
    out.append(MethodOption(method=ConnectionMethod.BROWSER.value, label=METHOD_LABELS["permitted_browser_automation"],
                            available=False, status=f.status if f else UNSUPPORTED,
                            reason=f.reason if f else "No permitted, bank-specific browser adapter exists for this bank.",
                            reference=f.reference if f else None))
    if tenant_id is not None:
        from app.core.config import get_settings
        from app.integrations.banks.profiles import profiles_for
        profile = next((x for x in profiles_for(db, tenant_id) if x.country == country
                        and name.casefold().strip() in [x.name.casefold(), *[a.casefold() for a in x.aliases]]), None)
        if profile:
            out[-1].status = AVAILABLE if profile.retrieval_supported else profile.permission
            out[-1].available = profile.retrieval_supported and get_settings().browser_automation_enabled
            out[-1].automatic = profile.retrieval_supported
            out[-1].reason = ("Use the bank website panel to open a visible browser, authenticate and retrieve statements. "
                              "Each new session requires user authentication." if profile.retrieval_supported else
                              "Official login is configured. Automated statement retrieval requires a reviewed, "
                              "permitted profile; opening the login page does not establish a connection.")
            out[-1].reference = profile.official_reference

    # 4. Manual import — always available, fallback only
    manual = get_adapter("manual_upload")
    out.append(MethodOption(method=ConnectionMethod.MANUAL.value, label=METHOD_LABELS["manual_statement_import"],
                            available=True, status=AVAILABLE, provider_code="manual_upload",
                            provider_name="Statement Upload", integration_status="production_verified",
                            environment="local", automatic=False, capabilities=manual.capability_matrix()
                            | {"statements": True},
                            reason="Fallback: upload official PDF/CSV/Excel statements yourself. Nothing is collected "
                                   "automatically."))

    best = next((o for o in out if o.available and o.automatic), None)
    if best:
        best.recommended = True
    return [o.as_dict() for o in out]


def resolve_provider(db: Session, *, provider_code: str, country_code: str, institution: Institution | None
                     ) -> tuple[BankProvider, dict[str, Any]]:
    """Validate that `provider_code` is a genuinely available method for this bank."""
    from app.core.errors import ValidationFailed

    options = methods_for(db, country_code=country_code, institution=institution)
    opt = next((o for o in options if o["provider_code"] == provider_code), None)
    if opt is None:
        raise ValidationFailed(f"'{provider_code}' is not a connection method for this bank",
                               code="method_unavailable")
    if not opt["available"] and opt["status"] != PROVIDER_SETUP_REQUIRED:
        raise ValidationFailed(opt["reason"], code=opt["status"])
    prov = db.scalar(select(BankProvider).where(BankProvider.code == provider_code))
    assert prov is not None
    return prov, opt
