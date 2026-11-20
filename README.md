# Meridian — Global Banking Intelligence & Automation Platform

**Set up once → switch automation on → bank data is collected, statements are extracted, balances are
monitored, accounts are reconciled and reports are produced every day.**

Meridian is a **read-only** banking intelligence and reconciliation platform. It never initiates payments,
transfers funds or changes bank accounts.

| Layer | Technology |
|---|---|
| Backend | Python 3.12 · FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL 17 · APScheduler · PyMuPDF · pandas · openpyxl |
| Automation | Durable PostgreSQL job queue (`FOR UPDATE SKIP LOCKED`), multi-threaded worker, persisted schedule |
| Frontend | React 18 · TypeScript (strict) · Vite 6 · Tailwind · TanStack Query · Zustand · Recharts · Framer Motion |
| Desktop | Tauri 2 shell that starts the local backend and loads the same web app |

## Quick start (Windows)

For local bank website sessions without bank API keys, use `scripts\start-browser.cmd` after building
the frontend. Bank-specific retrieval requires reviewed permission and selectors; the included HDFC
profile supports optional encrypted saved-login submission through **Save bank login → Authenticate**;
its statement retrieval is still unverified. OTP/CAPTCHA remain user steps. See [Dynamic browser banking](docs/BROWSER_BANKING.md)
for capabilities, setup, migration and account preservation details.

```powershell
cd C:\xampp\htdocs\global-banking-platform
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1     # venv, DB role/databases, .env, migrations, frontend build
scripts\start.cmd                                               # API + worker + scheduler + web app
```

Open **http://127.0.0.1:8000**. The first visit shows the one-time setup screen (organisation + owner
account). Then either add real accounts or use **Load demo environment** to explore with clearly
labelled synthetic data.

Development with hot reload: `scripts\dev.cmd` (web on http://127.0.0.1:5173, API docs at
http://127.0.0.1:8000/api/docs). All quality gates: `scripts\test.cmd`.

## What is implemented

- **Auth & security** — first-run setup, Argon2id passwords, 15-minute JWT access tokens held in memory,
  rotating httpOnly refresh tokens with reuse detection, CSRF double-submit, login lockout, rate limiting,
  RBAC (owner/admin/analyst/viewer), AES-256-GCM tenant-bound credential encryption, append-only audit log
  (enforced by a database trigger), log redaction, strict CSP and security headers.
- **Multi-tenant data model** — 31 tables with foreign keys, indexes, unique constraints, soft deletion;
  original bank transaction values are immutable at the database level.
- **Bank integration architecture** — common read-only adapter contract; providers: statement upload
  (any bank, verified), Demo Bank Simulator (demo), Plaid and GoCardless Bank Account Data (implemented
  against public APIs, **unverified** until tested with your credentials), India Account Aggregator and
  configurable bank website automation (**implemented against synthetic pages; real bank retrieval
  unverified**). Visible browser sessions use reviewed profiles and direct user authentication.
- **Automation engine** — master on/off, pause/resume, daily or cron schedules per time zone, per-account
  switches, idempotent jobs, per-account advisory locks, exponential back-off, per-provider throttling,
  timeouts, stale-job recovery after crashes/DB restarts, manual-intervention queue for OTP/re-auth,
  full per-job execution logs. One bank failing never stops the others.
- **Statement engine** — content-based format detection (PDF/XLSX/XLS/CSV), scanned-PDF detection (routed
  to review, never guessed), multilingual header recognition, date-format inference, opening/closing
  balance identification, running-balance and arithmetic validation, confidence scoring, SHA-256 duplicate
  detection, bank-specific layout profiles, original files preserved.
- **Reconciliation** — deterministic 4-pass matching with configurable tolerances; uncertain matches are
  *suggested*, never auto-confirmed; balance verification, duplicate/count/running-balance checks;
  immutable sessions; auditable manual confirm/reject/match/resolve decisions.
- **Balances & FX** — available/ledger/current/statement balances stored and shown separately with
  timestamps and sources; consolidation only via dated exchange rates, with missing rates surfaced.
- **Reports** — 12 report types in Excel, CSV and PDF; demo data is watermarked.
- **UI** — 13 sections, light/dark mode, responsive down to 320 px (tables become cards), accessible
  dialogs, skeletons, empty and error states, British English.

## Verification status (see `docs/IMPLEMENTATION_STATUS.md`)

- Backend: **67 tests passing** against PostgreSQL (unit, API, security, automation, end-to-end).
- Frontend: **26 tests passing**, strict type-check and ESLint clean, production build succeeds.
- Live browser check: setup → demo → automated run (51 jobs) → all 36 API endpoints 200, desktop and
  375 px mobile layouts, dark mode.
- **Not verified:** real bank connectivity through Plaid/GoCardless (no credentials), Tauri installer
  (Rust toolchain not installed on the build machine).

## Documentation

[Architecture](docs/ARCHITECTURE.md) · [Database schema](docs/DATABASE_SCHEMA.md) ·
[API](docs/API_DOCUMENTATION.md) · [Bank integrations](docs/BANK_INTEGRATIONS.md) ·
[Automation engine](docs/AUTOMATION_ENGINE.md) · [Security](docs/SECURITY.md) ·
[Deployment](docs/DEPLOYMENT.md) · [User guide](docs/USER_GUIDE.md) · [Testing](docs/TESTING.md) ·
[Implementation status](docs/IMPLEMENTATION_STATUS.md)

Browser implementation audit, changed files, verified capabilities and Windows commands:
[Dynamic browser banking](docs/BROWSER_BANKING.md).
## Finzola integration

Open **Finzola** in the sidebar or `/integrations/finzola` for gateway import, account mapping, reviewed bank comparison, organization destinations and acknowledged delivery tracking. The integration uses this project's existing PostgreSQL queue and reconciliation engine. External keys are configured through the UI; live provider compatibility remains unverified until configured and tested. See [the implementation and setup guide](docs/FINZOLA.md).
