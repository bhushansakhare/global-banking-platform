# Bank integrations

The platform does **not** claim universal bank support. Every channel declares exactly what it supports
and its verification status. Calling an undeclared capability raises `CapabilityNotSupported` — no data is
ever fabricated.

## Status matrix

| Provider | Countries | Data | Auth / consent | Status |
|---|---|---|---|---|
| Statement upload (`manual_upload`) | any | statements (PDF/XLSX/XLS/CSV) | none — no credentials stored | **production_verified** |
| Demo Bank Simulator (`demo`) | GB IN US DE AE | balances, transactions, statements | none | **demo** (synthetic, labelled everywhere) |
| GoCardless Bank Account Data (`gocardless_bad`) | GB + 13 EU | institutions, accounts, balances, transactions | PSD2 end-user agreement + requisition redirect, 90-day access | **implemented_unverified** |
| Plaid (`plaid`) | US CA GB | institutions, accounts, balances, transactions | Plaid Link `public_token` → `access_token` | **implemented_unverified** |
| India Account Aggregator via Setu (`india_account_aggregator`) | IN | account match, bank-reported *current* balance, transactions (no PDF statements) | RBI AA consent approved by the customer inside the AA (mobile-number handle; no bank password) | **implemented_unverified** — awaiting FIU onboarding / `SETU_AA_*` credentials |
| Bank portal automation (`browser_automation`) | per bank | statements | user-assisted MFA | **planned** (framework built) |

"Unverified" means the client was written against the provider's public API documentation but has not
been exercised with real credentials. Treat it as untested until you complete the verification checklist.
Check each provider's current onboarding availability and terms before relying on it.

## Connection methods (what users see)

For every bank the platform reports four methods, each with an explicit status — nothing unsupported is shown as
available (`GET /api/v1/banks/connection-methods`, `GET /api/v1/accounts/{id}/connection-methods`):

| Method | Status values | Source of truth |
|---|---|---|
| Official Bank API | `available` · `awaiting_bank_approval` · `unsupported` | `bank_api` providers with an adapter, else `integrations/banks/directory.py` |
| Authorised Banking Data Provider | `available` · `provider_setup_required` · `unsupported` | aggregator / AA providers for the country; aggregators only for banks in their institution list |
| Permitted Browser Automation | `not_permitted` · `unsupported` (no per-bank adapter exists) | `integrations/banks/directory.py` |
| Manual Statement Import | always `available` — **fallback only** | `manual_upload` |

Each option carries an explicit capability matrix (account information, available / ledger / current balance,
transactions, statements, historical data, automatic sync) — unsupported items are `false`, never omitted — plus
the environment (`sandbox`, `production`, `demo`, `local`).

## HDFC Bank (India) — findings

| Channel | Finding | Status in the platform |
|---|---|---|
| Official API | HDFC's developer portal (developer.hdfc.bank.in) lists API products only for registered, **approved** partners (mostly corporate/partner). No public self-service API for retail savings-account balances or statements was found. | `awaiting_bank_approval` (no adapter; endpoints are not invented) |
| Account Aggregator | HDFC Bank participates in the RBI AA network as a Financial Information Provider. Data reaches an FIU after the customer approves a consent in an AA. | `india_account_aggregator` adapter implemented against Setu AA Gateway docs — `provider_setup_required` until `SETU_AA_*` are set |
| Browser automation | No documented permission for automated access to HDFC retail NetBanking was found; it would require sharing the banking password and handling OTP/device binding. | `not_permitted` — not built |
| Statement upload | Works with HDFC PDF/CSV/XLS exports. | fallback |

## India Account Aggregator (Setu AA Gateway)

Flow implemented in `integrations/providers/india_aa.py` and `services/connections.py`:

1. `POST /accounts/{id}/connection-method {"provider_code":"india_account_aggregator"}` — switches an existing
   account (no delete/recreate). Connection is `pending`.
2. `POST /accounts/{id}/authorize {"mobile":"98xxxxxxxx"}` → Setu `POST {prefix}/consents`
   (`fetchType: PERIODIC`, `consentTypes: PROFILE/SUMMARY/TRANSACTIONS`, `fiTypes: DEPOSIT`, ReBIT purpose 102,
   `frequency` 6/day, `dataLife`/`consentDuration` 12 months). Returns the AA approval URL. The mobile number is the
   AA handle — stored encrypted (AES-256-GCM), never logged, audited or returned.
3. The customer approves inside the AA. `POST /accounts/{id}/authorize/verify` → `GET {prefix}/consents/{id}`;
   only `ACTIVE` makes the connection `active`; the first sync is queued automatically.
4. Each collection: `POST {prefix}/sessions` (`consentId`, `dataRange`, `format: json`) → `DataNotReady` →
   job deferred (attempt not consumed, session id persisted in the job payload) → `GET {prefix}/sessions/{id}` until
   `COMPLETED`/`PARTIAL`. The delivered account is matched by link reference or last four digits; if the consent does
   not include the account the job waits for the user (`consent` action) instead of guessing.
5. `summary.currentBalance` + `balanceDateTime` → balance type **current**, source `provider`,
   verification `provider_reported` (never relabelled "available"; no timestamp → not stored).
   Transactions: `type` CREDIT/DEBIT → signed amount, `txnId` → provider id, `narration`, `reference`, `valueDate`.
6. Notifications: `POST /api/v1/webhooks/aa/setu?token=<SETU_AA_WEBHOOK_TOKEN>` — disabled unless the token is set;
   the payload is only a hint, state is re-read from Setu.

Authentication differs between Setu product versions; by default `x-client-id`, `x-client-secret` and
`x-product-instance-id` headers are sent. If Setu issues you a token endpoint, set `SETU_AA_TOKEN_URL` and a bearer
token (`clientID`/`secret`/`grant_type=client_credentials`) is used instead. **Verify against your Setu Bridge
product documentation** — then complete the checklist below and change `integration_status` to
`production_verified` in `integrations/registry.py`.

## Country notes

- **India** — RBI Account Aggregator (above). Other licensed AAs/TSPs (e.g. Finvu, OneMoney, Anumati, CAMS
  Finserv) can be added as further adapters with the same contract. Statement upload remains the fallback.
- **UK / EU** — PSD2/Open Banking AIS through an authorised provider (GoCardless implemented).
- **US** — licensed aggregators (Plaid implemented).
- **Other** — statement upload works everywhere; add country-specific providers as adapters.

## Configuring a provider

1. Add credentials to `backend\.env` (`PLAID_CLIENT_ID`, `PLAID_SECRET`, `PLAID_ENV` or
   `GOCARDLESS_SECRET_ID`, `GOCARDLESS_SECRET_KEY`) and restart.
2. Import institutions: `POST /api/v1/institutions/sync?provider=gocardless_bad&country=GB`.
3. Add an account for that institution, then complete consent:
   GoCardless — `POST /connections/{id}/gocardless/requisition` → open `authorisation_url`;
   Plaid — `POST /connections/{id}/plaid/link-token`, run Plaid Link, then `POST /connections/{id}/plaid/exchange`.
4. `POST /connections/{id}/check` → status `active`.

### Verification checklist (per provider)

- [ ] check_connection returns active for a consented connection
- [ ] balances: types and currencies match the bank's app
- [ ] transactions: count and signs match for a known week; no pending items ingested
- [ ] consent expiry produces `consent_expired` and an *Authentication required* notification
- [ ] rate-limit response produces `retry_scheduled`, not failure
- [ ] update `integration_status` to `production_verified` in `integrations/registry.py`

## Adding a new bank or provider

1. Create `backend/app/integrations/providers/<name>.py`:

```python
from app.integrations.base.adapter import BankAdapter, Capability, ConnectionContext, ConnectionStatus
from app.integrations.registry import register

@register
class AcmeBankAdapter(BankAdapter):
    code = "acme_bank_api"
    name = "Acme Bank Business API"
    min_request_interval = 1.0            # per-provider throttle (seconds)
    capabilities = frozenset({Capability.BALANCES, Capability.TRANSACTIONS})

    def check_connection(self, ctx: ConnectionContext) -> ConnectionStatus: ...
    def get_balances(self, ctx): ...      # return list[BalanceData] — label types honestly
    def get_transactions(self, ctx, date_from, date_to): ...   # signed amounts, credit > 0
```

2. Map failures to `ProviderUnavailable`/`RateLimited` (retried), `AuthenticationRequired`/`ConsentExpired`
   (no retry, user notified) or `UserActionRequired(action="otp")` (job pauses for a human).
   `base/http.py:request_json` does most of this for HTTP APIs.
3. Import it in `registry._load()` and add a `ProviderSpec` to `PROVIDER_CATALOGUE` with
   `integration_status="implemented_unverified"`.
4. If the bank's statement layout is unusual, add a `StatementProfile` in
   `statements/parsers/profiles.py` built from a real (redacted) sample, with a fixture test.
5. Add unit tests with recorded/mocked responses — never real credentials.

## Browser automation (only where the bank permits it)

Subclass `integrations/base/browser.py:BrowserBankAdapter`, set `permitted_by_bank_terms` to the clause or
written permission that allows automated access, and implement `login()` with selectors verified against
that bank. The framework refuses to run without that reference. When a CAPTCHA or OTP appears it raises
`UserActionRequired`; the job moves to *Awaiting you*; the user enters the OTP in the Automation Center;
the OTP is passed to the worker through an in-memory, single-use, 5-minute vault and is never stored or
logged. CAPTCHA and MFA are never bypassed. Install browsers with
`.\.venv\Scripts\python.exe -m playwright install chromium` only when such an adapter exists.
