# Security

## Controls

| Area | Implementation |
|---|---|
| Passwords | Argon2id (t=3, m=64 MiB, p=2); ≥12 chars with upper, lower, digit; timing-equalised unknown-user path |
| Login protection | 5 failures → 15-minute lock; per-IP rate limit on auth endpoints (20/min); failures audited |
| Access tokens | HS256 JWT, 15 min, issuer/expiry/type checked, bound to a live session (revocation is immediate) |
| Refresh tokens | 384-bit opaque, SHA-256 stored, httpOnly `SameSite=Strict` cookie scoped to `/api/v1/auth`, rotated on every use; replay of a rotated token revokes the whole family; idle timeout 120 min |
| CSRF | double-submit token (`gbp_csrf` cookie + `X-CSRF-Token`) on cookie-authenticated endpoints |
| Browser storage | access token kept in memory only — never `localStorage` |
| RBAC | owner / admin / analyst / viewer with 19 permissions; enforced server-side on every route; UI hides what you cannot use |
| Tenant isolation | every query filters by the principal's tenant; cross-tenant IDs return 404 (tested) |
| Encryption at rest | AES-256-GCM with random nonce; tenant id as associated data (ciphertext cannot be moved between tenants); used for provider credentials and full account numbers |
| Account numbers | stored encrypted + keyed HMAC for duplicate detection; only `••••1234` is ever returned |
| Credentials | stored only for providers that need them, only after explicit user action (`credentials_authorised_by` recorded), wiped on disconnect; never sent to AI services |
| OTP | in-memory single-use 5-minute hand-off; never persisted or logged |
| Audit | append-only table enforced by a database trigger; before/after values redacted |
| Logs | structured JSON; keys like password/token/otp/secret/account_number redacted; long digit runs masked |
| Files | content-type detection, size limit, SHA-256 integrity check before processing, tenant-segregated paths with traversal protection, downloads only through authenticated endpoints with `nosniff` |
| HTTP | CSP (`script-src 'self'`), `X-Frame-Options: DENY`, `nosniff`, Referrer-Policy, Permissions-Policy, HSTS when `COOKIE_SECURE=true`, `Cache-Control: no-store` on API |
| SQL injection | SQLAlchemy parameter binding everywhere; no string-built SQL with user input |
| Input validation | Pydantic models with `extra="forbid"`, patterns and bounds |
| Read-only scope | no endpoint or adapter method can initiate payments or modify bank accounts |

## Key management

- `JWT_SECRET` and `MASTER_ENCRYPTION_KEY` live only in `backend\.env` (git-ignored). **Back up
  `MASTER_ENCRYPTION_KEY`** — without it encrypted credentials and account numbers cannot be decrypted.
- Rotating `JWT_SECRET` signs everyone out. Rotating the master key requires re-encryption (the `v1:`
  ciphertext prefix allows adding a `v2` key alongside).
- The application uses a dedicated least-privilege PostgreSQL role (`gbp_app`) that owns only its databases.

## Production checklist

`APP_ENV=production`, `COOKIE_SECURE=true` (enforced at start-up), HTTPS reverse proxy, `ALLOW_DEMO=false`,
restricted `CORS_ORIGINS`, Windows service account with access only to the project and storage folders,
regular database and storage backups, OS-level disk encryption.
