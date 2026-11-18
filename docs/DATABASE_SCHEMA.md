# Database schema

PostgreSQL 17, managed by Alembic (`backend/migrations`). All primary keys are UUIDs; all business tables
carry `tenant_id → organizations.id` (tenant isolation from day one) and `created_at/updated_at`.

## Migrations

| Revision | Content |
|---|---|
| `d0253a2ef9b5` initial schema | all tables, FKs, indexes, unique constraints |
| `a1b2c3d4e5f6` immutability | trigger blocking UPDATE/DELETE on `audit_logs`; trigger blocking changes to original values of `transactions` |

```powershell
cd backend
.\.venv\Scripts\alembic.exe upgrade head          # apply
.\.venv\Scripts\alembic.exe revision --autogenerate -m "describe change"   # after model changes
```

## Tables

| Group | Table | Notes |
|---|---|---|
| Identity | `organizations` | tenant; reporting currency, time zone, demo flag; soft delete |
| | `users` | unique email, Argon2id hash, lockout counters, role FK; soft delete |
| | `roles`, `permissions`, `role_permissions` | system roles (tenant NULL) + permission catalogue |
| | `sessions` | refresh-token sessions: SHA-256 token hash (unique), family id, rotation chain, revocation reason |
| Banking | `countries` | ISO code, default currency, open-banking framework |
| | `bank_providers` | data channels with capabilities, auth method, consent, refresh frequency, **integration_status** |
| | `institutions` | global (tenant NULL) or tenant-custom; unique (tenant, provider, external_id) |
| | `bank_connections` | status, encrypted credentials (AES-GCM, tenant-bound), failure counters |
| | `bank_accounts` | encrypted number + keyed hash (unique per tenant) + masked display; automation switch; thresholds |
| | `account_consents` | consent lifecycle per connection |
| | `account_balances` | immutable snapshots; CHECK on `balance_type`; unique (account, type, as_of, source) |
| | `exchange_rates` | dated rates; unique (tenant, base, quote, date, source) |
| Statements | `statement_files` | original file metadata; unique (tenant, sha256) |
| | `statements` | period, opening/closing, totals, arithmetic result, confidence, warnings, duplicate link |
| | `statement_parsing_jobs` | per-parse diagnostics |
| Transactions | `transaction_categories` | keyword rules per tenant |
| | `transactions` | bank-reported values (immutable), signed amount, fingerprint unique per account, `raw_data` |
| | `book_entries` | internal cash-book side of reconciliation |
| Reconciliation | `reconciliation_sessions` | rules + engine version snapshot, counts, balance verification |
| | `reconciliation_matches` | match type, score, reasons, `original_status`, manual decision fields |
| | `reconciliation_exceptions` | type/severity/status with mandatory resolution notes |
| Automation | `automation_settings` | master/pause, schedule, persisted `next_run_at` |
| | `automation_jobs` | durable jobs; partial index for claiming; unique (tenant, idempotency_key) |
| | `automation_job_logs` | execution log lines |
| System | `notifications`, `notification_preferences`, `audit_logs`, `system_settings`, `reports` | |

## Integrity rules

- Original bank data is never modified: a trigger rejects changes to amounts, dates, descriptions,
  references, running balances, raw data and fingerprints. Workflow columns (category,
  reconciliation status, duplicate flag, statement link) remain updatable.
- Audit logs are append-only (trigger).
- Ingestion uses `INSERT … ON CONFLICT DO NOTHING` on fingerprints, so retries and re-imports are idempotent.
