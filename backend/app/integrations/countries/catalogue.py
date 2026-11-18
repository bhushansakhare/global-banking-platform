"""Country catalogue with the open-banking framework that governs data access."""
from __future__ import annotations

COUNTRIES: list[dict[str, str | None]] = [
    {"code": "IN", "name": "India", "default_currency": "INR", "region": "Asia",
     "open_banking_framework": "RBI Account Aggregator (NBFC-AA)"},
    {"code": "GB", "name": "United Kingdom", "default_currency": "GBP", "region": "Europe",
     "open_banking_framework": "UK Open Banking (CMA9 / FCA)"},
    {"code": "US", "name": "United States", "default_currency": "USD", "region": "Americas",
     "open_banking_framework": "CFPB §1033 / licensed aggregators"},
    {"code": "CA", "name": "Canada", "default_currency": "CAD", "region": "Americas",
     "open_banking_framework": "Consumer-driven banking (in progress)"},
    {"code": "AE", "name": "United Arab Emirates", "default_currency": "AED", "region": "Middle East",
     "open_banking_framework": "CBUAE Open Finance"},
    {"code": "SA", "name": "Saudi Arabia", "default_currency": "SAR", "region": "Middle East",
     "open_banking_framework": "SAMA Open Banking"},
    {"code": "SG", "name": "Singapore", "default_currency": "SGD", "region": "Asia",
     "open_banking_framework": "SGFinDex / bank APIs"},
    {"code": "AU", "name": "Australia", "default_currency": "AUD", "region": "Oceania",
     "open_banking_framework": "Consumer Data Right (CDR)"},
    {"code": "JP", "name": "Japan", "default_currency": "JPY", "region": "Asia",
     "open_banking_framework": "Electronic Payment Agent API"},
    {"code": "CH", "name": "Switzerland", "default_currency": "CHF", "region": "Europe",
     "open_banking_framework": "bLink / market-driven"},
    {"code": "ZA", "name": "South Africa", "default_currency": "ZAR", "region": "Africa",
     "open_banking_framework": None},
    {"code": "BR", "name": "Brazil", "default_currency": "BRL", "region": "Americas",
     "open_banking_framework": "Open Finance Brasil"},
]

_EU = {"AT": "Austria", "BE": "Belgium", "DE": "Germany", "DK": "Denmark", "ES": "Spain", "FI": "Finland",
       "FR": "France", "IE": "Ireland", "IT": "Italy", "NL": "Netherlands", "PL": "Poland", "PT": "Portugal",
       "SE": "Sweden"}
_EU_CCY = {"DK": "DKK", "PL": "PLN", "SE": "SEK"}
for _c, _n in _EU.items():
    COUNTRIES.append({"code": _c, "name": _n, "default_currency": _EU_CCY.get(_c, "EUR"), "region": "Europe",
                      "open_banking_framework": "PSD2 / Berlin Group NextGenPSD2"})

CURRENCIES = sorted({str(c["default_currency"]) for c in COUNTRIES})
