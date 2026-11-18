# API

Interactive OpenAPI docs: **http://127.0.0.1:8000/api/docs** (ReDoc at `/api/redoc`, schema at
`/api/openapi.json`). Base path `/api/v1`. 98 routes.

## Conventions

- **Auth**: `Authorization: Bearer <access token>` (15 min). Refresh with `POST /auth/refresh`
  (httpOnly `gbp_rt` cookie + `X-CSRF-Token` header matching the `gbp_csrf` cookie).
- **Errors**: `{"error": {"code", "message", "details", "request_id"}}` — 401 `unauthorized|token_expired|session_revoked`,
  403 `forbidden|csrf_failed`, 404 `not_found`, 409 `conflict`, 422 `validation_error|invalid_statement`,
  429 `rate_limited`, 500 `internal_error`.
- **Pagination**: `?page=1&page_size=50` → `{items, total, page, page_size, pages}`.
- **Filtering/sorting**: query parameters per endpoint (e.g. transactions: `account_id, date_from, date_to, q,
  direction, status, category_id, min_amount, max_amount, currency, duplicate_suspect, sort, order`).
- Every response carries `X-Request-ID`.

## Endpoints

| Area | Endpoints |
|---|---|
| auth | `GET /auth/setup-status`, `POST /auth/setup`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me`, `POST /auth/change-password` |
| users | `GET/POST /users`, `PATCH /users/{id}`, `GET /users/roles` |
| banks/institutions | `GET /countries`, `GET /banks/providers`, `GET/POST /institutions`, `DELETE /institutions/{id}`, `POST /institutions/sync?provider=&country=` |
| accounts | `GET/POST /accounts`, `GET/PATCH/DELETE /accounts/{id}`, `POST /accounts/{id}/automation` |
| connections | `GET /connections`, `POST /connections/{id}/check|disconnect|reconnect|demo-scenario`, `POST /connections/{id}/plaid/link-token|plaid/exchange`, `POST /connections/{id}/gocardless/requisition` |
| balances | `GET /balances`, `GET /balances/{account}/history`, `GET/POST /balances/exchange-rates` |
| statements | `GET /statements`, `POST /statements/upload` (multipart), `GET /statements/{id}`, `GET /statements/{id}/transactions`, `GET /statements/{id}/file`, `POST /statements/{id}/reprocess`, `DELETE /statements/{id}` |
| transactions | `GET /transactions`, `GET /transactions/export?fmt=csv|xlsx`, `GET /transactions/{id}`, `POST /transactions/categorise`, `GET/POST /transactions/categories`, `GET/POST /book-entries`, `POST /book-entries/import` |
| reconciliation | `POST /reconciliation/run`, `GET /reconciliation/sessions`, `GET /reconciliation/sessions/{id}`, `POST /reconciliation/matches/{id}/decision`, `POST /reconciliation/sessions/{id}/manual-match`, `PATCH /reconciliation/exceptions/{id}`, `GET/PUT /reconciliation/rules` |
| automation/jobs | `GET /automation`, `PUT /automation/settings`, `POST /automation/master|pause|resume|run-now|retry-failed`, `GET /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/retry|cancel|resume` |
| reports | `GET /reports/types`, `POST /reports`, `GET /reports`, `GET /reports/{id}/download` |
| analytics | `GET /analytics/summary|balance-history|cash-flow|monthly|distributions|reconciliation-performance` |
| notifications | `GET /notifications`, `GET /notifications/unread-count`, `POST /notifications/{id}/read`, `POST /notifications/read-all`, `GET/PUT /notifications/preferences` |
| audit | `GET /audit-logs` |
| settings | `GET /settings`, `PUT /settings/organisation`, `PUT /settings/{key}`, `POST /settings/demo/seed|clear` |
| system | `GET /health` |

## Example (PowerShell)

```powershell
$login = Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/auth/login `
  -ContentType application/json -Body '{"email":"you@company.com","password":"…"}'
$h = @{ Authorization = "Bearer $($login.access_token)" }
Invoke-RestMethod http://127.0.0.1:8000/api/v1/analytics/summary -Headers $h
```
