# Testing

Everything at once: `scripts\test.cmd` (lint → backend tests → typecheck → ESLint → frontend tests → build).

## Backend (pytest, real PostgreSQL)

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q          # 67 tests, ~2 min
.\.venv\Scripts\python.exe -m pytest tests\test_reconciliation_engine.py -q
```
Tests use `TEST_DATABASE_URL` (refuses to run if it equals `DATABASE_URL`); the schema is rebuilt from
migrations at session start and truncated after every test. Workers/scheduler are disabled and jobs are
drained synchronously with `run_pending_once()`. No real banking credentials are used — the demo adapter's
scenarios simulate failures.

| File | Coverage |
|---|---|
| `test_statement_parsing.py` | amount/date parsing (UK, EU, Indian, Dr/Cr), header detection, CSV, XLSX, text PDF, scanned PDF → OCR, invalid files, arithmetic & running-balance validation |
| `test_reconciliation_engine.py` | reference/amount/date passes, ambiguity never auto-confirmed, tolerances, balance verification, duplicates, count mismatch, determinism, tolerance padding, manual carry-forward |
| `test_security.py` | AES-GCM round-trip, tamper and tenant-binding, hashing/policy, JWT forgery/expiry/`alg=none`, redaction |
| `test_api_auth.py` | setup once, lockout + audit, refresh rotation + reuse detection, CSRF, logout revocation, unauthenticated access, RBAC, tenant isolation, account-number masking, append-only audit |
| `test_automation.py` | idempotency, concurrent claims (4 threads), back-off, stale recovery (crash/DB restart), scheduler eligibility & no double-run, master-off semantics, pause, provider downtime isolation, expired consent, missing OTP pause/resume, retry |
| `test_e2e_workflow.py` | full workflow (user → sign-in → institution → account → automation → collection → statements → transactions → balances → reconciliation → reports → audit), manual-upload bank, duplicate statement/invalid file, duplicate transactions, multi-currency demo environment |

## Frontend (Vitest + Testing Library)

```powershell
cd frontend
npm test            # 26 tests
npm run typecheck   # strict TypeScript
npm run lint
```
Covers formatting/status mapping, form validation, the HTTP client (auth header, single-flight refresh,
session clearing, error parsing), DataTable (responsive table/card rendering, sorting, selection,
pagination, empty/error states), accessible modal/confirm/toggle, login flow, RBAC-filtered navigation and
dashboard rendering with demo/FX warnings.

## Manual browser verification performed

Setup → demo environment → run now (51 jobs succeeded) → 36 API endpoints returned 200 → dashboard at
1440 px, transactions at 375 px (no horizontal overflow), reconciliation detail in dark mode. This pass found
and fixed two bugs the automated suites had missed (see IMPLEMENTATION_STATUS).
