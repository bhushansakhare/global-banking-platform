# Finzola in Meridian

The supplied Finzola audit is adapted to this project's FastAPI, SQLAlchemy/PostgreSQL, existing durable job queue, scheduler, React UI and reconciliation engine. It is an integration with the external Finzola service, not a replacement Finzola server.

Open **Finzola** in the sidebar (`/integrations/finzola`). The workspace has four steps:

1. **Start here**: current setup and actual operation status.
2. **Import expenses**: encrypted gateway key, selected domains, manual fetch, optional 5/15/60 minute polling, per-domain freshness/error status, cached record browser and CSV/JSON exports.
3. **Match bank payments**: external organization/account routing, explicit financial-field mapping, original statement evidence review, expected-payment state, source-change decisions and comparison reports.
4. **Send to Finzola**: organization webhook destinations, no-expense-write verification, optional automatic comparison-result delivery, manual reviewed debit preview/send, acknowledgement history and transient retry.

## Configuration and contracts

Gateway and organization webhook keys are separate, write-only credentials stored with tenant-bound AES-GCM encryption. They are never returned in API data or stored in frontend localStorage. The browser never calls Finzola with these keys. Organization key fingerprints cannot be reused across destinations/tenants. Replacing a destination key increments its revision, clears verification and disables automatic delivery. A gateway key replacement clears cached snapshots, even when the same key is explicitly supplied again. Scope/key changes hold financial processing until a fresh fetch.

The provider-specific endpoints from the supplied contract are fixed in `backend/app/integrations/finzola/transport.py`:

- `GET https://ajlzldkyskdedjbeiazs.supabase.co/functions/v1/api-gateway/{domain}`, using Bearer authentication.
- `POST https://ajlzldkyskdedjbeiazs.supabase.co/functions/v1/external-expense-webhook`, using the organization `X-API-Key`.

They cannot be changed through browser input. The HTTPS client validates public DNS addresses, pins the selected IP while preserving TLS hostname verification, rejects redirects, applies a 15-second socket timeout and bounds each response to 4 MiB. Gateway snapshots are limited to 10,000 records per domain, with validated envelope/domain/count/nonempty unique IDs. Remote pagination and delta fetching are not implemented. Local page controls browse the cached snapshot only.

Supported import domains are organizations, users, vendors, expenses, approvals, finance and logs. Data is stored as row-per-record staging in `finzola_records`. A successful domain replaces its snapshot atomically, including an empty result; a failed domain retains its previous rows and last success with an explicit error. A comparison requires all selected domains from the same successful fetch generation, with no domain errors and within a freshness window of `max(60 minutes, twice the configured interval)`. Gateway-authorizer permissions are rechecked. Unused domains need not be selected.

Automatic fetch follows the existing **Automation Center master switch**, its pause state and `SCHEDULER_ENABLED`. The backend and computer must remain running. Fetch and comparison share a PostgreSQL advisory lock so their financial snapshots cannot overlap. Queue operations coalesce active work. Config/mapping edits are serialized against active comparison. No Redis service is required for this project's PostgreSQL-backed queue/locks.

## Financial mapping and bank evidence

External `org_id` never becomes a local tenant ID. Mapping always separately selects a genuine local account. A default organization route or exact vendor/bank-account route is supported; multiple matching specific routes require attention.

The mapping explicitly chooses `net_amount`/`amount`, major/minor units, due/expected/payment date, invoice/finance transaction reference and debit/credit direction. Eligible source expenses must be approved or paid, have an exact matching account currency, an explicit valid calendar date, a required reference and an exact positive amount. No FX conversion, invoice-date fallback, finance allocation, tax/vendor/category inference or paid bank transaction is invented. Multiple finance allocations are rejected where date/reference needs finance data. Supported currency exponents are declared in `contracts.py`; an unsupported currency requires review rather than assuming precision. Due-date decisions use the local organization's business time zone.

`finzola_items` hold expectation state and financial identity; the existing `BookEntry` table provides internal matching operands tagged `source=finzola`. These staged operands are excluded from normal cash-book listings, totals and ordinary reconciliation; only the explicit Finzola comparison selects them. They are never inserted into `transactions`. This reuses the native matching engine without treating an expense as an actual ledger or bank movement.

Bank proof requires a non-demo transaction linked to a genuine, extracted statement, valid arithmetic, matching account/currency, original file integrity and explicit review against the original by a currently authorized statement editor. Review binds the file, figures and transaction identities. Reprocessing the statement invalidates its prior review until checked again. Bank website screenshots/observations alone do not prove a transaction payment.

Expected payments remain awaiting date/evidence until eligible. Comparisons group eligible expectations by local account, use only reviewed bank transactions, retain immutable reconciliation sessions and distinguish confirmed matches from exceptions. Source changes or withdrawal hold the expectation and exclude it from new comparison. Financial amounts and prior match links are not silently overwritten. Authorized source review requires a reason; retaining/cancelling preserves local decisions, and replacement is allowed only for an expectation with no comparison links. The previous operand remains archived with its financial figures unchanged. Existing matched/manual links prevent replacement.

This project has no monthly financial close/period-lock workflow; a new monthly close subsystem has not been invented. Existing reconciliation/manual match decisions remain preserved. External approval/paid statuses are never used as bank payment proof.

## Delivery behavior

Save each organization's webhook key, then explicitly run **Test destination**. The documented test sends `{"requestId":"EXP-TEST","eventType":"test"}`. Verification requires HTTP 200 and exactly `{"ok":true,"received":"EXP-TEST"}`. Saving a key or a successful TLS connection is not verification.

Automatic result delivery is off by default and requires a verified current destination revision. Enabling it queues eligible current comparison processing. Manual bank-debit publishing is separate and does not require automatic delivery or a bridge mapping; it requires a reviewed genuine debit, a verified destination, source preview and explicit send action. The server resolves the source from its database, never from a client-supplied amount/payload.

Outgoing fields follow the supplied contract: stable request ID, event type, internal source ID, purpose, exact numeric amount/currency, source date and explanation. Comparison results use `reconciliation.updated`, comparison status and display-only payment text. They do not approve expenses, initiate bank payments or invent GST/TDS/invoice enrichment.

`finzola_deliveries` persist immutable payload/hash, key revision, source identity, authorizer, attempts and acknowledgement. At execution the worker revalidates source, evidence, permissions, current destination revision and payload hash. Invalid/changed work becomes superseded. Authentication rejection disables destination verification/automatic delivery. Two global delivery slots and a destination-specific PostgreSQL advisory lock bound concurrency and serialize sends to one organization. Transient errors use at most four attempts, with the existing queue's exponential/jitter backoff starting at three seconds. Deferral for a busy delivery slot does not consume an attempt. Queued/running/retry states are distinct from delivered; only the exact acknowledged request ID is delivered. Worker crash/cancellation status is reflected in delivery views, and durable queue recovery revisits interrupted work. Removing a destination cancels pending work; an already in-flight HTTP request cannot be recalled.

Exactly-once remote writes cannot be claimed. A timeout may happen after the receiver persisted a record. The receiver must atomically upsert by stable request ID and replay the acknowledgement. The external deployed receiver's implementation has not been inspected or verified here.

## API and permissions

All paths below use `/api/v1/integrations/finzola`:

| Endpoint | Use |
| --- | --- |
| `GET/PUT /custom-api` | Gateway settings; key is write-only |
| `POST /custom-api/fetch` | Queue domain fetch |
| `GET /custom-api/data/{domain}` and `/export` | Cached records and CSV/JSON export |
| `GET /custom-api/pipeline` and `/export` | Mapping, expectations, jobs and reports |
| `PUT /custom-api/pipeline/mapping` | Save organization/account route |
| `POST /custom-api/pipeline/process` | Queue comparison |
| `GET /custom-api/pipeline/items/{id}/review-preview` | Stored/current source comparison |
| `POST /custom-api/pipeline/items/{id}/review` | Reasoned retain/cancel/unmatched replacement |
| `POST /evidence/{statement_id}/review` | Review original bank evidence |
| `GET/PUT /outbound` | Destination configuration and delivery status |
| `POST /outbound/{external_org_id}/test` | Explicit no-expense-write verification |
| `DELETE /outbound/{external_org_id}` | Remove destination/cancel pending work |
| `POST /outbound/transactions/preview` and `/send` | Resolve and publish one reviewed bank debit |
| `POST /outbound/deliveries/{id}/retry` | Expedite a transient pending retry |

Uses the project's tenant ownership and RBAC: integration reads need settings access; mapping/source review needs settings management and reconciliation write; bank evidence review needs statement write; manual publishing also needs transaction read. Authorizer permissions are rechecked in workers. Provider organization IDs are never trusted as local account/tenant scope. Keys, raw HTTP error bodies and bank login secrets are excluded from delivery/audit responses. The project does not currently have a separate account-level ACL system; this integration follows its existing tenant/RBAC model.

Apply the additive migration with `.venv/Scripts/alembic.exe upgrade head` from the backend directory, then restart the backend. New tables: `finzola_records`, `finzola_mappings`, `finzola_items`, `finzola_destinations`, `finzola_deliveries`. Existing bank accounts, passwords, statements, evidence and email settings are preserved.

The source project's legacy live stub, TLS-only connectivity probe, obsolete preparation send path, Mongo/Redis stack, history-erasure UI and separate Finzola-provider server are not copied into this project. Existing immutable audit/history behavior is retained.

## Validation and live status

Gateway, mapping, evidence, changed/withdrawn source, currency precision, tenant isolation, authorization revocation, bounded retry, key rotation, exact acknowledgement, TLS/IP pinning and shared concurrency are tested with synthetic records and mocked HTTP. Existing automation/reconciliation/end-to-end checks and UI tests also pass. No live provider key, live fetch, connection test, expense publish or result delivery was performed for implementation. Configure approved gateway and organization keys in the new UI before evaluating live compatibility.
