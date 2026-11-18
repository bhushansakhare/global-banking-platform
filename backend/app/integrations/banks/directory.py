"""Per-bank integration facts that are NOT provider-wide.

Each entry records what was actually established about a bank's channels — never assumptions.
A method is only offered as *available* when an adapter exists AND (where applicable) the bank or
provider is genuinely reachable. Everything else is reported with an explicit status and reason.

Statuses used here:
  awaiting_bank_approval – the bank offers APIs only to approved partners; onboarding required
  not_permitted          – no documented permission for automated portal access was found; not built
  unsupported            – the channel does not exist for this bank
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class MethodFact:
    status: str
    reason: str
    reference: str | None = None
    setup_steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class BankFacts:
    key: str
    name: str
    country_code: str
    aliases: tuple[str, ...]
    aa_fip: bool | None = None  # True = known to participate as an FIP in the AA network
    official_api: MethodFact | None = None
    browser: MethodFact | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


_NO_BROWSER = MethodFact(
    status="unverified",
    reason="No documented permission from the bank for automated access to its retail internet-banking portal "
           "has been verified. A reviewed bank profile is required for retrieval. Passwords, OTP and device "
           "checks must be completed directly on the official bank page.",
)

BANKS: tuple[BankFacts, ...] = (
    BankFacts(
        key="hdfc_bank", name="HDFC Bank", country_code="IN", aliases=("hdfc", "hdfc bank", "hdfc bank ltd",
                                                                       "hdfc bank limited"),
        aa_fip=True,
        official_api=MethodFact(
            status="awaiting_bank_approval",
            reason="HDFC Bank's developer portal publishes APIs only to registered and approved partners "
                   "(largely corporate/partner products). No public, self-service API for retail savings-account "
                   "statements or balances was found.",
            reference="https://developer.hdfc.bank.in/api-catalog",
            setup_steps=("Register an organisation on the HDFC Bank developer portal",
                         "Apply for the relevant account/statement API product and complete HDFC's approval",
                         "Once credentials are issued, a dedicated adapter can be added against the documented spec"),
        ),
        browser=_NO_BROWSER,
        notes=("HDFC Bank participates in the RBI Account Aggregator network as a Financial Information "
               "Provider (FIP); account discovery happens inside the AA consent screen.",),
    ),
    BankFacts(key="icici_bank", name="ICICI Bank", country_code="IN", aliases=("icici", "icici bank"), aa_fip=True,
              browser=_NO_BROWSER),
    BankFacts(key="sbi", name="State Bank of India", country_code="IN",
              aliases=("sbi", "state bank of india", "state bank"), aa_fip=True, browser=_NO_BROWSER),
    BankFacts(key="axis_bank", name="Axis Bank", country_code="IN", aliases=("axis", "axis bank"), aa_fip=True,
              browser=_NO_BROWSER),
    BankFacts(key="kotak", name="Kotak Mahindra Bank", country_code="IN",
              aliases=("kotak", "kotak mahindra", "kotak mahindra bank"), aa_fip=True, browser=_NO_BROWSER),
)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (name or "").lower()).strip()


def lookup(name: str, country_code: str | None) -> BankFacts | None:
    n = _norm(name)
    for b in BANKS:
        if country_code and b.country_code != country_code.upper():
            continue
        if n == _norm(b.name) or n in b.aliases:
            return b
    return None
