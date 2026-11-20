# Statement passwords, previews and automatic reconciliation

Select a bank account and upload one or multiple statements. Files are processed sequentially and each upload retains its own result. The original PDF, CSV or Excel file remains content-hashed and unchanged. Duplicate uploads for the same account reuse the existing statement.

For a protected PDF, open the statement and enter its document password in **Unlock this statement once**. **Unlock & save for this account** verifies the password against that PDF before saving it. A password entered in the browser's built-in PDF viewer is not available to the application; use this form once instead. Never use a banking login password in the PDF password field.

The verified password is encrypted using the existing master key and bound to the tenant and account. Verified older statements retain their own encrypted password binding, so changing the account's default does not break older previews. A newly uploaded statement reads only its own account's saved password. Wrong passwords leave the previous password unchanged and the document needing attention. Passwords never appear in API responses, audit payloads, durable job payloads or filenames. Saved password controls can forget all PDF passwords for the selected account without deleting files or transactions.

Saving a valid password processes the selected statement and queues other pending or password-blocked statements for the same account (up to 500 existing statements per save). Future uploads and browser/provider downloads use the saved password through the shared processing pipeline. Non-password extraction issues still need their own review.

**Preview** uses an authenticated, integrity-checked, in-memory PDF copy with encryption removed for display. It is served with `Cache-Control: no-store`. The original-download endpoint continues serving the original encrypted bytes. PDF page screenshots use the same account-bound password. No decrypted PDF is written to statement storage.

## Validation and matching

Generic CSV/Excel headers, PDF tables and PDF text extraction select the appropriate existing parser. Known institution profiles remain supported. Layouts and currencies are validated from extracted data; an unsupported layout, insufficient OCR confidence, wrong currency or invalid arithmetic does not become successful extraction simply because a password worked. A password unlock does not certify transaction accuracy.

After valid extraction, the pipeline automatically queues reconciliation only when automatic reconciliation is enabled, the statement has transactions and a dated period, arithmetic is valid, the currency matches the account and the statement is not an overlapping-period duplicate. Upload, background parsing, reprocess and reviewed-figure confirmation all use this hand-off. The per-statement idempotency key prevents repeat processing from creating duplicate automatic runs.

Reconciliation always binds the selected statement to the selected account and tenant. Without cash-book entries it reports statement balance/integrity checks only. Importing or creating new cash-book entries automatically queues affected validated statements again, respecting configured date tolerance. Duplicate imports with no new entries do not trigger another check. Immutable previous sessions remain available. Uncertain matches and exceptions remain reviewable rather than being silently confirmed.

## User interface

- Statements supports multiple files, per-account filtering and explicit **PDF password needed** status.
- Statement detail shows file receipt, PDF access, extraction, arithmetic validation and reconciliation stages, with an actual result link when available.
- Reconciliation refreshes automatically, supports account filtering, and distinguishes **Statement integrity** from **Bank + cash-book** checks.
- Bank website OTP/QR authentication and PDF document passwords are separate flows. Saving a PDF password never logs in to a bank or bypasses bank approval.

The encrypted user-provided statement must still be unlocked through the new UI before its real layout can be validated. Synthetic password, preview, isolation, persistence and reconciliation tests cover the feature without requiring a customer's password in chat.

Password-save success is acknowledged only after the database commit. The UI then reads the statement back and requires both saved-password and unlocked-preview confirmation before showing success. A success-shaped response without persisted access is an explicit error. This guards against a misleading saved state; it does not substitute for the correct PDF password.

Validation: 52 existing parsing/reconciliation/monitoring/end-to-end checks and five encrypted-PDF workflow tests passed. The frontend suite passed 54 tests before the final read-back guard; the final five statement UI cases cover that guard, sequential multiple upload, account password submission and truthful processing stages. TypeScript and production builds passed. Synthetic desktop/mobile statement and reconciliation views rendered without browser errors or horizontal overflow. The real supplied statement remained password-blocked at the last recorded live check, so its extraction has not been claimed as verified.
