# Implementation status

_Last updated: 16 Nov 2026_

## Environment found

Windows 10 Pro · Python 3.12.4 · Node 24.14.0 · npm 11.9.0 · Git 2.52 · PostgreSQL 17.10 (service running) ·
**Rust/Cargo not installed** (needed only for the desktop installer).

## Phases

| Phase | State |
|---|---|
| 1 Foundation — structure, config, PostgreSQL role/databases, auth, models, migrations, layout | ✅ done |
| 2 Banking management — countries, institutions, providers, accounts, connections, encrypted credentials | ✅ done |
| 3 Automation engine — queue, worker, scheduler, retries, monitoring UI | ✅ done |
| 4 Statement processing — download/upload, PDF/Excel/CSV extraction, normalisation, duplicates, balances | ✅ done (OCR: see below) |
| 5 Reconciliation — engine, verification, exceptions, decisions, reports | ✅ done |
| 6 Dashboard/analytics/notifications/audit | ✅ done |
| 7 Web + desktop delivery | ✅ web · ⚠️ desktop source complete, installer not built (no Rust) |
| 8 QA | ✅ all suites passing, builds succeed |

## Test results (actually executed)

- Backend: `pytest` → **67 passed**; `ruff --select F,E9` → clean.
- Frontend: `vitest` → **26 passed**; `tsc --noEmit` (strict) → 0 errors; ESLint → clean; `vite build` → success.
- Migrations applied to `gbp` and (per test session) `gbp_test`.
- Live run: 51 automation jobs succeeded on the demo environment; all 36 GET endpoints 200; responsive and
  dark-mode screens inspected.

## Bugs found and fixed during verification

1. Reconciliation auto-confirmed a pairing that only became "unique" after an ambiguous rival was taken —
   ambiguity is now judged against the pre-pass candidate pool.
2. `75.25Cr` amounts weren't parsed (regex word boundary).
3. Failed-login counters/audit rows were rolled back with the 401 response — now committed first.
4. `FOR UPDATE` with eager outer joins (User.role, Statement.file) failed — inner join / narrow lock.
5. Non-IP client hosts crashed INET inserts — validated.
6. Frontend refresh promise could be reused stale; a retried 401 didn't clear the session.
7. `GET /automation` returned 500 (`dict()` on a Result) — found in the browser, fixed, regression test added.
8. Reconciliation reported cash-book entries from the ±tolerance padding (neighbouring periods) as
   unmatched — padding is now candidate-only; regression test added.
9. Balance history plotted £0 before data existed — now a gap.

## Not done / external dependencies

| Item | What is needed |
|---|---|
| Real bank connectivity | Plaid / GoCardless credentials and the verification checklist in BANK_INTEGRATIONS.md. Until then these are **implemented_unverified**. |
| India Account Aggregator | Contract with a licensed AA / TSP; adapter to be written against their API. |
| Bank-portal automation | A bank that explicitly permits it, plus verified selectors; framework is ready. |
| OCR for scanned PDFs | Optional: install Tesseract + `pytesseract`, add an OCR parser with confidence scoring; scanned files are currently routed to *Needs OCR*/manual review. |
| Desktop installer | Install Rust (MSVC) + VS Build Tools, generate icons (`npx tauri icon`), `npm run build` in `desktop/`. Optionally bundle the backend into resources. |
| Exchange-rate feed | Rates are recorded manually (demo seeds illustrative rates). A rates provider can be added as a scheduled job. |
| Browser E2E automation suite | E2E is covered at API level in pytest plus a manual Playwright pass; a scripted Playwright UI suite is a next step. |

## Next steps

1. Obtain sandbox credentials for one aggregator and complete its verification checklist.
2. Build the desktop installer once Rust is installed.
3. Add OCR parser and a scheduled FX-rate import.
4. Script the browser smoke test (setup → demo → run → dashboard) with Playwright for CI.
