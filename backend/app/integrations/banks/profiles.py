"""Reviewed, tenant-scoped browser profiles. A directory entry is not a working adapter."""
from __future__ import annotations

import ipaddress
import socket
import hashlib
import json
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.models.system import SystemSetting


class NavigationStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["click", "select", "date_from", "date_to", "account"]
    selector: str = Field(min_length=1, max_length=500)
    value: str = Field(default="", max_length=100)


class BrowserProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z0-9_]{2,60}$")
    name: str = Field(min_length=1, max_length=200)
    country: str = Field(pattern=r"^[A-Z]{2}$")
    aliases: list[str] = Field(default_factory=list, max_length=30)
    currencies: list[str] = Field(default_factory=list, max_length=30)
    account_types: list[str] = Field(default_factory=lambda: ["savings", "current"])
    website: str
    login_url: str
    domains: list[str] = Field(min_length=1, max_length=20)
    resource_domains: list[str] = Field(default_factory=list, max_length=30)
    official_reference: str
    permission: Literal["unverified", "permitted", "not_permitted"] = "unverified"
    permission_reference: str | None = None
    reviewed: bool = False
    review_required: bool = False
    login_fields_reviewed: bool = False
    customer_id_selector: str | None = None
    password_selector: str | None = None
    submit_selector: str | None = None
    continue_selector: str | None = None
    authenticated_selector: str | None = None
    authenticated_path: str | None = None
    account_selector: str | None = None
    expired_selector: str | None = None
    failed_selector: str | None = None
    steps: list[NavigationStep] = Field(default_factory=list, max_length=30)
    download_selector: str | None = None
    logout_selector: str | None = None
    date_format: str = "%Y-%m-%d"
    formats: list[Literal["pdf", "csv", "xls", "xlsx"]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    credential_storage_notice: str | None = None
    credential_storage_reference: str | None = None

    @model_validator(mode="after")
    def validate_profile(self):
        for domain in [*self.domains, *self.resource_domains]:
            if domain != domain.lower() or "/" in domain or ":" in domain or "*" in domain:
                raise ValueError("Use exact lowercase domain names without ports or wildcards")
            if "." not in domain or domain.endswith((".local", ".localhost", ".internal")):
                raise ValueError("Public bank domains are required")
            try:
                ipaddress.ip_address(domain)
            except ValueError:
                pass
            else:
                raise ValueError("IP addresses are not bank domains")
        for url in (self.website, self.login_url):
            validate_url(url, self.domains)
        validate_url(self.official_reference, None)
        if self.permission_reference:
            validate_url(self.permission_reference, None)
        if self.credential_storage_reference:
            validate_url(self.credential_storage_reference, None)
        if self.permission == "permitted" and (not self.permission_reference or not self.reviewed):
            raise ValueError("Enabling automation requires a reviewed permission reference")
        if self.authenticated_path and not self.authenticated_path.startswith("/"):
            raise ValueError("Authenticated path must be an absolute path")
        if self.retrieval_supported and not self.account_selector:
            raise ValueError("Retrieval requires an account identity selector")
        return self

    @property
    def retrieval_supported(self) -> bool:
        return bool(self.permission == "permitted" and self.reviewed and not self.review_required and self.authenticated_selector
                    and self.authenticated_path and self.download_selector and self.formats)

    def public(self) -> dict:
        return {**self.model_dump(), "capabilities": {
            "browser_login": "review_required" if self.review_required else
                ("supported" if self.reviewed and self.permission == "permitted" else self.permission),
            "official_login": True, "automatic_statement_retrieval": self.retrieval_supported,
            "customer_id_autofill": self.login_fields_reviewed and not self.review_required and bool(self.customer_id_selector),
            "saved_login": self.login_fields_reviewed and not self.review_required and bool(self.customer_id_selector and self.password_selector
                                                               and self.submit_selector),
            "scheduled_automation": "user_action_required" if self.retrieval_supported else "unsupported",
            "live_balance": False,
        }}

    @property
    def credential_scope(self) -> str:
        # A saved secret must never follow a different bank/profile/domain selected later.
        identity = [self.key, self.country, self.login_url, sorted(self.domains), self.customer_id_selector,
                    self.password_selector, self.submit_selector]
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def validate_url(url: str, domains: list[str] | None, *, resolve: bool = False) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443) or (domains is not None and parsed.hostname not in domains)):
        raise ValueError("Only HTTPS URLs on reviewed bank domains are allowed")
    if any(k.lower() in {"state", "nonce", "code", "code_challenge", "access_token", "id_token"}
           for k in parse_qs(parsed.query)) and not resolve:
        raise ValueError("Temporary authentication parameters cannot be saved in profiles")
    if resolve:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError("Bank domains must resolve exclusively to public addresses")


HDFC = BrowserProfile(
    key="hdfc_bank", name="HDFC Bank", country="IN", aliases=["hdfc", "hdfc bank ltd", "hdfc bank limited"],
    currencies=["INR"], website="https://www.hdfc.bank.in/",
    login_url="https://now.hdfc.bank.in/retail-app/", domains=["www.hdfc.bank.in", "now.hdfc.bank.in"],
    resource_domains=["mbrww.hdfcbank.com", "nextgenhtml.hdfcbank.com", "api-now.hdfc.bank.in"],
    official_reference="https://www.hdfc.bank.in/online-services",
    login_fields_reviewed=True, customer_id_selector="#username", password_selector="#password", submit_selector="#kc-login",
    credential_storage_notice="HDFC's published IPIN terms instruct customers not to record the IPIN electronically. "
        "Saving a password here is optional; direct bank-page login remains available. Encryption does not change the bank's terms.",
    credential_storage_reference="https://www.hdfc.bank.in/resources/way-to-bank/online-banking/net-banking/terms-and-conditions",
    limitations=["Official entry point verified; bank permission and authenticated statement selectors unverified.",
                 "Customer ID field verified in a visible user-controlled session. Enter IPIN, OTP, app approval or CAPTCHA on the official bank page.",
                 "No automatic statement retrieval or unattended login is claimed for this profile."],
)


def profiles_for(db, tenant_id) -> list[BrowserProfile]:
    profiles = {HDFC.key: HDFC}
    for row in db.scalars(select(SystemSetting).where(SystemSetting.tenant_id == tenant_id,
                                                      SystemSetting.key.like("browser_profile:%"))):
        p = BrowserProfile.model_validate(row.value)
        profiles[p.key] = p
    return sorted(profiles.values(), key=lambda p: p.name)


def find_profile(db, tenant_id, key: str) -> BrowserProfile:
    return next(p for p in profiles_for(db, tenant_id) if p.key == key)
