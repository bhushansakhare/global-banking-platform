# Architecture

```
                ┌──────────────────────────── Windows host ────────────────────────────┐
 Browser ──────►│  FastAPI (uvicorn, 127.0.0.1:8000)                                   │
 Tauri window ─►│   ├─ /api/v1/*  REST API (auth, RBAC, validation)                    │
                │   ├─ /*         built React app (same origin → cookies + CSRF)       │
                │   ├─ Worker threads  ── claim jobs ──┐                               │
                │   └─ Scheduler tick (30 s) ── enqueue┤                               │
                │                                       ▼                              │
                │                     PostgreSQL 17 (jobs, data, audit)                │
                │   storage/statements  storage/reports   (tenant-segregated files)   │
                └───────────────────────────────┬──────────────────────────────────────┘
                                                │ HTTPS (read-only)
                          Plaid · GoCardless · (future: AA, bank APIs) · Demo simulator
```

## Backend layout (`backend/app`)

| Package | Responsibility |
|---|---|
| `core/` | settings, AES-GCM crypto, password/JWT security, JSON logging with redaction, errors, rate limiting, permission catalogue |
| `database/` | declarative base (UUID PKs, timestamps, soft delete, tenant mixin), engine/session |
| `models/` | identity, banking, statements, transactions, reconciliation, automation, system |
| `integrations/` | `base/adapter.py` contract + errors, `base/http.py` error mapping, `base/browser.py` permitted-automation framework, `providers/*`, `registry.py` catalogue, `countries/catalogue.py` |
| `statements/` | detection, normalisation, parsers (CSV/Excel/PDF + profiles), storage, pipeline |
| `transactions/` | fingerprinting, categoriser, idempotent ingestion + duplicate suspicion |
| `balances/` | snapshots, latest-per-type queries, explicit FX conversion, low-balance alerts |
| `reconciliation/` | pure deterministic `engine.py` + persistence/decisions `service.py` |
| `automation/` | durable `queue.py`, `handlers.py`, control `service.py` |
| `workers/` | multi-threaded worker with per-account advisory locks |
| `scheduler/` | persisted schedule, advisory-locked tick |
| `analytics/`, `reports/` | aggregations; report builders and XLSX/CSV/PDF renderers |
| `notifications/`, `audit/` | in-app fan-out with preferences; append-only audit |
| `api/` | dependencies (principal, RBAC), serializers, routes |

**Key decisions**

1. *PostgreSQL job queue instead of Redis/Celery* — no extra service on Windows; `SKIP LOCKED` gives safe
   concurrent claiming; the queue API (`enqueue/claim/complete/fail`) is small enough to swap later.
2. *Schedule persisted in `automation_settings.next_run_at`* — APScheduler only provides the heartbeat;
   restarts never lose or duplicate runs; an advisory lock makes multi-process deployment safe.
3. *Same-origin delivery* — the backend serves the built SPA so the httpOnly refresh cookie
   (`SameSite=Strict`) works identically for web and desktop.
4. *Pure reconciliation core* — no I/O in `engine.py`, which makes it deterministic and easy to test.
5. *Business logic outside React* — components call typed functions in `src/services/api.ts`; state is
   TanStack Query (server) + Zustand (session/UI).

## Frontend layout (`frontend/src`)

`app/` router + providers · `layouts/` shell and auth layout · `pages/` 18 lazily-loaded screens ·
`features/` domain components (charts, add-account, upload, job detail, validation) · `components/` UI
kit (DataTable, Modal, primitives, states) · `services/` HTTP client and endpoints · `stores/` auth/UI ·
`utils/` formatting and status mapping · `types/` API types.

## Desktop (`desktop/`)

Tauri 2 shell: shows a splash, starts `backend/main.py` bound to 127.0.0.1 (no console window), polls
`/api/v1/health`, then navigates to the local origin. The webview has no shell/filesystem permissions.
The backend is stopped when the window closes; interrupted jobs are recovered at the next start.
