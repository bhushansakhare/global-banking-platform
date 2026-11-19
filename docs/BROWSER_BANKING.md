# Dynamic bank browser connections

## Architecture audit and change

The existing application uses React/Vite, FastAPI/SQLAlchemy, PostgreSQL, a durable PostgreSQL
job queue, APScheduler, and a Tauri shell. Statement ingestion, PDF/CSV/XLS/XLSX parsers,
transaction deduplication, balance snapshots, reconciliation, account schedules and job history
already existed. Browser integration previously consisted of an abstract Playwright class:
there was no concrete bank browser implementation. The static Indian bank facts also treated
unverified permission as a prohibition, which has been corrected to `unverified`.

The new browser channel does not require bank API keys. Each organisation can register profiles
for any supported bank through a validated configuration endpoint and the account connection UI.
Profiles contain official domains, reviewed entry points, currencies/account types, authentication
markers, account identity selectors, statement navigation/date filters, download formats, logout
and expiry/failure markers. API providers remain optional alternative connection methods.

`profiles.py` validates and discovers profiles. Tenant overrides live in the existing
`system_settings` table under `browser_profile:<key>`. Accounts have an optional profile binding
and an AES-GCM tenant-bound encrypted login vault. At the user's explicit request, this can now store
a Customer ID and optional bank login password/IPIN. Vault records are bound to the reviewed bank
profile and navigation domains, and are never returned to the frontend. OTPs, CAPTCHA answers,
app approval codes and banking session cookies are not persisted. Browser profiles reject permanent
state, nonce, code and PKCE parameters. HDFC's published terms instruct customers not to record their
IPIN electronically; that bank-specific notice and source are shown beside the optional password
field. Encrypted storage does not imply bank approval or change those terms. Manual login remains available.

`browser_engine.py` creates a visible, ephemeral Edge/Chromium context on its owning worker
thread. Optional saved-login submission requires reviewed login fields and an explicit Authenticate
request. Without saved authentication, the customer completes login directly on the official bank
page. Statement retrieval separately requires a reviewed, permitted statement profile. Authentication
requires a positive marker and exact configured path; statement retrieval also checks the linked
account's masked suffix. Unrecognised selectors, account mismatches and browser failures produce
review errors. Such errors disable retrieval on that profile until an administrator reviews it.

TLS verification stays enabled. Exact domain allowlists and public DNS validation protect each
request. Chromium's native request interception validates every redirect hop before transmission;
authentication forms are not replayed through an HTTP proxy and redirect documents are not rewritten.
Native redirect interception is tested against synthetic Chromium/CDP responses, including denial
of a redirect to an unreviewed domain. There are no stealth plugins,
CAPTCHA solvers, MFA bypasses or private-bank API integrations. Banking payloads, screenshots,
traces and storage-state files are not logged or exported. A profile must describe read-only
navigation; financial action controls are rejected. Unknown popup pages and WebSockets are blocked.

During login-only setup, the job can retain bounded navigation-review evidence: read-only control
labels/IDs, a path without its query string, and candidate account selectors without account-number
values. It excludes password/OTP input values, HTML, hrefs, tokens and cookies. Long digit runs are
masked. Evidence remains explicitly unverified and does not mark authentication or synchronization
successful. It allows the actual statement navigation to be reviewed after the customer logs in.

`browser_jobs.py` connects downloaded files to the existing original-file storage, SHA-256
deduplication, parsing, transaction ingestion, balance snapshots and reconciliation jobs. Original
files use the existing storage service. XLS/XLSX integrity, PDF validity and CSV structure are
checked before import; HTML login pages cannot masquerade as statements. Statement balances keep
their source/timestamp and are not labelled live available or ledger balances. Retrieval success
and subsequent parsing/reconciliation results are separate jobs and remain visible in sync history.

The current configurable workflow retrieves one configured statement export per run, with optional
date filters. It does not enumerate arbitrary statement archives or infer unknown bank layouts.
Scanned/encrypted/ambiguous statements still follow the existing parser's review requirements.

## HDFC reference status

The included `hdfc_bank` profile points to `https://now.hdfc.bank.in/retail-app/`.
The official entry point was checked against [HDFC online services](https://www.hdfc.bank.in/online-services)
and [HDFC's new banking website information](https://www.hdfc.bank.in/now-hdfc-bank).
Temporary OpenID Connect URLs are not saved. The customer may open this official login in a
dedicated visible browser. Optional saved credentials can fill Customer ID and password/IPIN and
submit login after the user clicks Authenticate. OTP, app approval and CAPTCHA stay on the bank page.

**HDFC automation permission and authenticated statement selectors have not been verified.**
HDFC's `#username` Customer ID field was subsequently verified in a visible user-controlled browser,
and Customer ID auto-fill is implemented independently from statement permission. The same page
also contains `#password` and the `#kc-login` submit input. With an explicitly saved login and an
Authenticate request, the application now fills both fields and submits once. Without that request,
it leaves password entry and submission to the customer. A Customer ID must be supplied once in
the application before it can be auto-filled; the account number is not a Customer ID.
Automatic statement retrieval and unattended login remain disabled until their workflow is verified.
Opening its login does not mark the account connected or record a successful sync. Uploading an
official statement remains available. No automated test used a real HDFC/customer banking account.

Follow-up public-login inspection on 17 November 2026, with no identifiers or credentials entered:
the official entry point loaded a document titled **403 Forbidden**, containing **403 Forbidden**
and no login fields. A separate basic HTTPS connectivity request returned HTTP 200, so a successful
HTTP connectivity check is not evidence that the bank's login form is available to Playwright.
The headless attempt was blocked at public login; the user subsequently confirmed the login form
was available in the explicitly opened visible browser. Authentication has not yet been completed
in the inspected session, so account/statement selectors are still unverified. The engine now reports
`bank_access_blocked` immediately for HTTP 403/429 or explicit denial documents, without waiting
for authentication, retrying login, or claiming retrieval. The diagnostic helper
`backend/inspect_public_login.py` reads control metadata without entering identifiers or credentials.
Its optional `--visible` mode opens a fresh user-controlled browser and waits for direct bank login,
then reports visible control names for workflow mapping. It never reads input values, passwords,
OTPs, cookies or session tokens, and never logs temporary authentication query strings.

Migration `c8d9e0f1a2b3` only adds nullable account fields. It was applied locally, with before/after
fingerprints confirming that the one existing account, ending 0127, and its previous connection
were unchanged. No account was deleted or duplicated.

## Current capability matrix

| Profile/channel | Official login | Customer ID auto-fill | Retrieval | Scheduled operation | Authentication |
| --- | --- | --- | --- | --- | --- |
| Included HDFC profile | Configured, official source checked | Verified `#username` field; user supplies ID once | Unverified, disabled | Disabled | Customer completes bank page |
| New reviewed and permitted profiles | Configured by administrator | If mapped | Configured export; must be tested per bank | Requests user login, then waits | Customer completes bank page |
| Statement upload | Separate fallback | Not applicable | Customer supplies file | No remote retrieval | None |
| Existing API/AA providers | Existing independent flows | Not applicable | Provider-specific | Existing provider implementation | Provider-hosted consent |

No additional real bank browser profiles are preconfigured or claimed verified. Existing static
ICICI, SBI, Axis and Kotak facts are not runnable browser profiles. The Example Bank profile and
pages used by tests are synthetic and are not seeded into the user's database.

## Windows CMD startup

From CMD:

```cmd
cd /d C:\xampp\htdocs\global-banking-platform
npm --prefix frontend run build
scripts\start-browser.cmd
```

Open `http://127.0.0.1:8000`. The script enables local browser sessions, selects installed Microsoft
Edge, applies migrations and starts the existing API, worker and scheduler. It does not need Docker
or bank API credentials. Stop an older platform process before starting another instance.

Alternative Chromium installation, only if Edge is unavailable:

```cmd
cd /d C:\xampp\htdocs\global-banking-platform
backend\.venv\Scripts\python.exe -m playwright install chromium
set BROWSER_AUTOMATION_ENABLED=true
set BROWSER_CHANNEL=chromium
scripts\start.cmd
```

`BROWSER_LOGIN_TIMEOUT_SECONDS` defaults to 600 seconds. These options are documented in
`.env.example`. Browser mode runs on the Windows machine hosting the backend/worker; a remote web
client cannot see or operate a browser on another server. This implementation does not stream a
remote browser. The Tauri shell uses the same local backend; no Tauri installer build was performed.

## Add a bank and use automation

1. In Bank Accounts, add/select the country and bank. Website profiles also appear in the bank picker.
   Keep the existing account when connecting an account that already exists.
2. Open **Connect bank**. The bank website panel comes first; search and select the bank profile.
3. An administrator can expand **Configure another bank profile**, load the selected profile as a
   template and save valid JSON. Change its key/name/country/domains/official reference for another
   bank. Keep permission `unverified` until permission and the bank-specific selectors are reviewed.
4. A working retrieval profile requires `permission: "permitted"`, a permission reference,
   `reviewed: true`, `review_required: false`, authentication path/marker, account identity selector,
   supported formats, and a statement download selector. Navigation is limited to click/select,
   account selection and date filters. Never configure payment or transfer navigation.
5. Enter Customer ID/User ID and optionally Bank password/IPIN, then **Save bank login**. Both are
   encrypted at rest and cleared from the inputs after saving. Click **Authenticate** to open the
   reviewed login, fill both fields and submit once. A password is never echoed back by the API,
   written to job payloads, put into URLs, or saved in localStorage. **Forget saved login** removes it.
   Changing the Customer ID discards the old password unless a new one is provided. Changing the
   bank/profile/domains makes old saved passwords unavailable until credentials are saved again.
   Choose a date range. For a reviewed retrieval profile, Authenticate continues to statement download;
   HDFC currently performs login only because statement navigation is still unverified.
6. Complete OTP, CAPTCHA or app approval on the official bank page. After confirmed login
   and account identity, the browser downloads and validates the configured statement export.
7. View statements, transactions, balances, reconciliation and account sync history. Parsing may
   require review even when a download succeeds. Downloaded statement balance is not a live balance.
8. Use the existing account schedule/timezone controls and enable automation. A due browser job
   requests authentication once and stays `awaiting_user`; subsequent ticks cannot create another
   active collection job for that account. Resume through the connection panel or intervention queue.
   Manual **Run now**, both per-account and in Automation Center (selected accounts or all enabled
   accounts), now recognises a valid saved browser login even when the previous connection is still
   Statement Upload. It opens one headed authentication job instead of reporting a manual-upload
   collection as the requested browser run. Unverified statement profiles still perform login only.
9. Pause/resume/disable controls remain available. Close the bank session using the connection panel
   or cancel its job. A PostgreSQL advisory lock permits one bank browser per desktop/database at a
   time, while the existing account lock and queue prevent concurrent account collection.

Persistent jobs recover through the existing stale-job policy after a restart. Banking cookies are
discarded with the browser, so recovery requires fresh authentication; there is no unattended login.
Temporary job failures use existing retry/history mechanisms. Authentication waits never loop OTP
requests. Profile failures require review rather than automatic authentication retries.

## Endpoints

- `GET /api/v1/banks/browser-profiles` — country/name/code/website/currency/account-type discovery.
- `PUT /api/v1/banks/browser-profiles/{key}` — administrator-only, tenant-scoped profile registration.
- `GET /api/v1/accounts/{id}/browser` — preferred profile, capabilities and latest browser job.
- `POST /api/v1/accounts/{id}/browser/start` — encrypted identifier binding and persistent login/sync job.
- `PUT /api/v1/accounts/{id}/browser/credentials` — write-only, encrypted, bank-scoped login storage.
- `DELETE /api/v1/accounts/{id}/browser/credentials` — remove saved login credentials.
- Existing sync, job cancel/resume, schedule, statement, balance and reconciliation endpoints are reused.

## Source files changed

- `.env.example`, `README.md`, `docs/BROWSER_BANKING.md`, `scripts/start-browser.cmd`.
- `backend/app/core/config.py`, `backend/app/core/logging.py`, `backend/app/main.py`.
- `backend/app/models/banking.py`, `backend/migrations/versions/20261117_c8d9e0f1a2b3_browser_profiles.py`.
- `backend/app/api/routes/accounts.py`, `backend/app/api/routes/browser_banking.py`.
- `backend/app/integrations/banks/profiles.py`, `backend/app/integrations/banks/browser_engine.py`,
  `backend/app/integrations/banks/directory.py`, `backend/app/integrations/base/browser.py`,
  `backend/app/integrations/providers/browser.py`, `backend/app/integrations/registry.py`,
  `backend/app/integrations/methods.py`.
- `backend/app/automation/browser_jobs.py`, `backend/app/automation/handlers.py`,
  `backend/app/automation/service.py`, `backend/app/scheduler/scheduler.py`, `backend/app/workers/worker.py`.
- `backend/tests/test_browser_banking.py`, `backend/tests/test_automation_first.py`.
- `backend/inspect_public_login.py` (follow-up public-login diagnostics; no credential entry).
- `frontend/src/services/api.ts`, `frontend/src/utils/status.ts`,
  `frontend/src/features/accounts/BrowserBankConnection.tsx`,
  `frontend/src/features/accounts/ConnectionSetupModal.tsx`,
  `frontend/src/features/accounts/AddAccountModal.tsx`,
  `frontend/src/features/accounts/AutomationControls.tsx`, `frontend/tests/browser-banking.test.tsx`.

The production frontend bundle in `frontend/dist` is regenerated by the build.

## Verification

Automated coverage includes dynamic tenant-scoped profile registration, HTTPS/domain/redirect checks,
private DNS rejection, permission review, authentication markers, wrong-account rejection, encrypted
identifier handling, preservation of the existing connection, durable jobs, duplicate statement
imports, corrupt files, scheduler wait/resume, invalidation after page changes, and the frontend
connection flow/RBAC. A real Playwright browser runs against in-process synthetic bank pages to
exercise Customer ID/continue, authentication detection, account/date selection and CSV download.
Existing statement tests cover PDF/CSV/Excel extraction, normalization and snapshots; existing
automation tests cover queue/retry/recovery/isolation, and reconciliation tests cover matching.

Real bank retrieval and a Tauri installer are unverified. See the final run results recorded below.

Latest verification on 17 November 2026, after saved-password authentication changes:

- `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` from `backend`: **128 passed**.
- `npm --prefix frontend run test`: **37 passed**.
- `npm --prefix frontend run build`: passed (TypeScript and production Vite bundle).
- Backend Ruff checks for `F,E9`: passed.
- A temporary smoke-test app on port 8012 returned HTTP 200 for `/` and `status: ok`,
  `database: true` for health. OpenAPI includes the credentials endpoint and marks its password
  schema `writeOnly: true`. The temporary server was stopped after verification.
- Coverage includes encrypted saved credentials, bank/domain/form binding, clearing credentials,
  password exclusion from responses/job payloads, explicit Authenticate requests, password-field
  validation, safe navigation-review evidence and a real Playwright test of saved-login submission
  through statement download on fully synthetic bank pages. No automated test logged into a real account.
- The previously running project backend on port 8000 was identified, verified against its project
  parent process and restarted with visible browser mode enabled. The updated live app returned
  `status: ok`, `database: true`, and its OpenAPI schema includes saved credentials and Authenticate.
  Logs are in `storage/browser-backend.stdout.log` and
  `storage/browser-backend.stderr.log`. Existing accounts and database contents were retained.
- Tests emit existing dependency deprecation warnings; no test failures remain in these runs.

Headed flow follow-up:

- Fixed Automation Run now routing for saved logins, including per-account, selected-account and
  global runs, account status, duplicate jobs and explicit resume after a user-action wait.
- Added a visible-browser badge. Production browser launch is explicitly `headless=False` and
  the browser test asserts this before applying its optional CI-only headless override.
- Corrected the live login timing bug: the Customer ID field now waits up to 45 seconds for the
  fresh SPA/SSO login page to become visible before validating or filling it. A delayed-render
  synthetic login case verifies this regression.
- Related browser/automation run: 75 passed, with one startup error caused by running a frontend
  rebuild while the backend fixture checked the temporarily removed assets directory. Once the
  build completed, that case plus all new Run now cases passed in a sequential 5-test run.
- Frontend automation/browser tests: 11 passed; build and backend Ruff checks passed. Three
  login-field regression tests and the delayed-render saved-login browser test also passed.
- A visible synthetic-bank smoke check successfully completed saved-ID/password submission,
  account/date selection and CSV download. The installed older Playwright driver emitted a
  `TargetClosedError` during synthetic-browser teardown; functional assertions passed. Cleanup
  explicitly closes context and browser. This warning is not evidence of real-bank retrieval.
- An explicitly user-requested live HDFC session reproduced and then verified the field-readiness
  fix. Saved login was submitted once in a visible browser. The customer confirmed that OTP,
  CAPTCHA or app approval was pending. No second login submission is automatic; completed
  authentication and statement retrieval still need confirmation.
- That live session subsequently stopped with a sanitized browser error during the verification
  phase. A navigation race is a plausible cause, but the original error was deliberately not
  logged with raw browser details. The poll now tolerates document-context replacement during
  authentication redirects, with a passing regression test. Browser closure is surfaced as a
  user-action wait rather than a bank-profile change. These changes are loaded into the backend;
  no further saved-credential submission was performed. HDFC authentication success and statement
  retrieval remain unconfirmed; the next Authenticate/Run now is an explicit customer action.

ACCESS_DENIED follow-up:

- The customer reported the official callback `/retail-app/select-context?error=ACCESS_DENIED`,
  and confirmed that a fresh manual login in a normal browser also returns ACCESS_DENIED.
  The cause is not established; this does not prove an incorrect password, an account block or
  an automation-only restriction. Successful bank access must be restored before retrieval can
  be verified. The [official HDFC NetBanking guide](https://www.hdfc.bank.in/ways-to-bank/digital-banking/online-banking/netbanking)
  and [official support page](https://www.hdfc.bank.in/contact-us) are the appropriate recovery sources.
- The browser engine now detects authorization-denial callback parameters, including HTTP 200
  pages titled normally. It records `bank_authorization_denied`, pauses the job for user action,
  retains the attempt count and never marks a sync successful. It does not log authentication
  URLs or untrusted error descriptions. No further real-bank login was attempted for this change.
- The connection panel displays the denial and recovery instructions. Open bank login can resume
  a waiting job into a fresh official session without automatically resubmitting the stored password.
  This is separate from Authenticate. OTP/MFA/CAPTCHA and bank access decisions are not bypassed.
- Validation for the denial update: 53 browser-banking backend tests and 6 frontend browser-flow
  tests passed; backend Ruff and the production frontend build passed. The local backend was
  restarted with the new handler without creating any new bank login job.

19 November 2026 startup/OTP-window correction:

- The active backend was running with browser automation disabled. The previous enabled state
  had only been supplied to a process launch and did not survive a different startup command.
- Set the three non-secret browser options in `backend/.env`: `BROWSER_AUTOMATION_ENABLED=true`,
  `BROWSER_CHANNEL=msedge`, `BROWSER_LOGIN_TIMEOUT_SECONDS=600`. All other environment values,
  existing accounts and encrypted saved logins were retained. `scripts/dev.cmd` now supplies the
  same development defaults when they are not explicitly set.
- The timeout setting now has a minimum of 60 seconds. The headed bank window normally waits
  up to 600 seconds for verification. Customer cancellation, browser closure or an explicit bank
  rejection can stop it earlier; a bank's own OTP expiry is not extended or bypassed.
- Browser status and health expose the non-secret enabled/mode/wait values; the account panel
  displays the configured verification duration. An old configuration-only failure no longer
  keeps a saved-login account failed after the browser setting is corrected. No sync is fabricated.
- A visible synthetic OTP page stayed open for **62.5 seconds** using the real browser engine.
  No real bank account, saved password or OTP was used in that check. Frontend browser tests (7),
  build and Ruff passed. Backend browser tests had 55 passing cases and one test-fixture setup
  failure; after correcting the fixture to use the normal job enqueue path, all three new cases
  passed in a focused run.
- Restarted the verified backend. Live health returned `bank_browser.enabled: true`,
  `bank_browser.mode: headed`, `bank_browser.verification_wait_seconds: 600`, with database OK.
  The Vite frontend on port 5173 returned HTTP 200. No real-bank login was submitted during this fix.

Manual Run now / zero-job correction:

- The customer's saved-login account had `automation_paused=true`. Global Run now used the
  schedule eligibility filter and silently skipped it. Manual Run now now includes valid saved
  browser logins regardless of the account's scheduling on/off or paused flags. It does not
  change those flags or resume scheduled collection.
- An explicitly user-requested browser job can execute even when organisation scheduling is
  paused. This exception is limited to jobs tagged `user_requested_browser=true`; scheduled and
  other background jobs still obey the existing pause rules.
- Run now reports new, resumed and already-running jobs separately. The frontend displays the
  returned message rather than treating `jobs_created=0` as a successful zero-job run. With no
  runnable account, the API returns `no_eligible_accounts` rather than a misleading success toast.
- Related backend suite: 90 tests passed and one resume-count test failed because the session
  disables autoflush. Added an explicit flush before run-summary queries; the failed case and
  all other new pause/count scenarios then passed in a focused six-case run. Frontend automation
  and browser tests (13), production build and Ruff passed.
- Loaded the update and performed one explicitly user-requested Run now for the saved account.
  The result was `jobs_started: 1`, `jobs_resumed: 0`, message `1 bank browser login(s) queued`;
  the account's scheduling pause remained true. The worker reached `running` / opening official
  bank login with no error. Successful bank authentication and retrieval are not implied by this.

Post-OTP lifecycle investigation:

- The customer reported being ejected after entering an OTP. The latest job recorded `job_timeout`,
  with its first `started_at` substantially earlier than the current `locked_at`, and continued
  receiving worker heartbeats after being marked failed. This establishes a software watchdog
  issue; it does not establish that the bank rejected the OTP or that authentication succeeded.
- The watchdog now measures a running attempt from `locked_at` (falling back to `started_at`
  for legacy data), retaining the original first-start timestamp for history. Resuming an old job
  no longer consumes its new verification window using time spent in an earlier session.
- Heartbeats only update running jobs. A terminal/recovered job tells its worker to stop rather
  than keeping an orphaned bank browser active. A worker cannot complete a terminal job as a
  successful run, and stopping that worker preserves the previous failure reason.
- The normal verification wait remains 600 seconds. Real attempt timeouts, explicit cancellation
  and bank authorization denials remain enforced. These changes do not bypass MFA or extend a
  bank-issued OTP's validity. No additional real-bank authentication was submitted for this fix.
# QR / bank app login

Validation: 66 backend browser-banking tests and 8 frontend browser-connection tests passed. QR cases cover sessions with/without saved secrets, rejecting combined QR/password submission, switching active login modes, and continuing a synthetic supported statement workflow. No real bank login or QR approval was submitted during validation.

In **Connection setup → Connect through your bank website**, select the bank profile and press **QR / bank app login**. The software opens a fresh official bank page without loading or submitting saved Customer ID/password. Choose the bank's own QR login option there, scan with its official mobile app and approve on that app. QR images, mPINs, approvals and tokens are not captured or stored by the software.

HDFC documents QR login via the HDFC Bank App, swipe approval and mPIN here: https://www.hdfc.bank.in/resources/way-to-bank/online-banking/net-banking/two-step-verification . Availability/eligibility and session acceptance remain bank-controlled. This option does not bypass a rejected bank session.

For a permitted, reviewed statement workflow, QR authentication uses the same authenticated/account-identity checks and statement pipeline as password login. If the bank profile is unverified (currently HDFC), the button opens a login-only session and never marks it as a successful sync. An existing active password session must be closed before switching to QR. Login-only sessions use the account's configured verification wait; scanning a QR alone is not proof of successful authentication.

