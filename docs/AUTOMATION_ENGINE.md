# Automation engine

## Daily pipeline

```
scheduler tick (30 s, advisory-locked)
   └─ next_run_at reached and master ON and not paused
        ├─ collect_account  × every eligible account   (idempotency: collect:<account>:<slot>)
        │     check connection → balances → transactions (look-back window) → new statements
        │       └─ parse_statement  per new statement   (idempotency: parse:<statement>)
        │             extract → validate → store → balance snapshots
        │               └─ reconcile_account          (idempotency: recon:stmt:<statement>)
        │     (no new statement but cash-book exists → reconcile rolling window)
        └─ daily_report (defers itself until the run's other jobs finish, max 2 h)
```

Eligible account = not deleted, automation switch on, connection not disconnected.

## Job states

`queued → running → succeeded`
`running → retry_scheduled → queued…` (retryable error, attempts < max)
`running → failed` (permanent error or attempts exhausted → *Failed background job* notification)
`running → awaiting_user` (OTP/CAPTCHA/re-auth; not counted as an attempt) → user resumes → `queued`
`queued|retry_scheduled|awaiting_user → cancelled`; running jobs honour `cancel_requested` at the next checkpoint.

## Guarantees

| Requirement | Mechanism |
|---|---|
| Persistence | jobs, logs and schedule live in PostgreSQL |
| Locking / concurrency | `FOR UPDATE SKIP LOCKED` claim; session advisory lock per account |
| Idempotency | unique (tenant, idempotency_key); fingerprint/sha256/external-id dedupe in ingestion |
| Retries | exponential back-off `base × 2^(attempt-1)` + 0–25 % jitter; configurable base and max attempts |
| Per-account isolation | each account is its own job; exceptions are contained per job |
| Per-provider rate limits | `min_request_interval` throttle per provider |
| Timeouts | jobs exceeding `JOB_TIMEOUT_SECONDS` are failed/retried by recovery |
| Failure recovery | jobs whose heartbeat is older than `JOB_STALE_SECONDS` (crash, DB restart) are retried |
| Graceful shutdown | worker threads finish the current job before exiting |
| Audit | every control action is audited; every job has an execution log |

**Automation OFF** cancels queued scheduled/pipeline jobs and clears `next_run_at`; running jobs finish.
**Pause** keeps everything queued until resumed. Manual "Run now" works regardless of the master switch.

## Running

- Embedded (default): `python main.py` starts API + worker + scheduler.
- Separate processes (scale-out): run the API with `WORKER_ENABLED=false SCHEDULER_ENABLED=false`, and one
  or more `python -m app.cli worker` processes. The scheduler tick is advisory-locked, so several worker
  processes are safe.
- Operations: `python -m app.cli drain` (process runnable jobs now), `recover`, `scheduler-tick`.
