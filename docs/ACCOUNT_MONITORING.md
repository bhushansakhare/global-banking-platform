# Account balance monitoring

On **Bank Accounts**, each row has a **Balance target** column and **Set balance target** button. The button opens that account's monitoring form without leaving the list. The same form remains available on the account detail page. Users without account-write access can view the form but cannot save changes.

Open an account and use **Balance target & alerts**. Set the minimum balance in the account currency, the verification wait (60–720 seconds), and that account's email recipient(s). Enabling email requires a target and at least one valid recipient. Saving the target and monitoring settings is atomic. Turn email off before clearing the target to disable monitoring. Changes to the verification wait apply to the next browser run; they cannot extend the bank's own OTP/session expiry.

Each account needs its own target and recipient before enabling balance emails. Every new eligible balance observation generates one account-specific email: below target sends a warning, while equal to or above target sends a normal balance report. Both include the observed balance, target, currency, balance type and date. Rechecking/reprocessing the same snapshot does not send it again. A later new snapshot may send another email on the same day. Saving settings can also report the latest eligible snapshot if it has not already been reported for that target.

The next successful collection downloads the original statement, queues extraction, records dated balances and transactions, and checks the target. A manual statement upload follows the same extraction and alert path. **Statements → Extract pending statements** queues up to 500 pending/unresolved documents per request, with no duplicate active extraction jobs. Review extraction status before retrying unresolved files.

Supported local parsers: text PDF, CSV, XLSX, XLS. Scanned PDFs attempt local Tesseract OCR through PyMuPDF using the included English language data in `backend/vendor/tessdata`. Unsupported layouts or missing language data remain `needs_ocr`. OCR results need review before statement-balance emails. Check every transaction and balance against the original and use **Confirm reviewed figures** when the arithmetic reconciles. Confirmation is audited; currency mismatches and unbalanced statements cannot be confirmed. No external AI service is configured, no documents are sent to one, and universal or exact extraction is not claimed.

Only processed, arithmetically valid statement balances can trigger automatic emails. The latest eligible dated balance is used, with available/current/ledger preferred over statement closing when timestamps tie. Statement closing balances keep their date and source and are never labelled live balances. Email jobs recheck the target, enabled status and selected balance immediately before sending, skipping superseded observations. Demo accounts never send email. One email job per account/snapshot/target is queued to avoid repeated messages from reprocessing. Legacy warning-only jobs remain warning-only. SMTP delivery is at least once: a process interruption after server acceptance can lead to a duplicate on retry; Message-ID stays stable.

Configure outgoing mail on this computer in `backend/.env`:

```dotenv
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_FROM=banking@example.com
SMTP_USERNAME=your-smtp-login
SMTP_PASSWORD=your-mail-provider-app-password
SMTP_SECURITY=starttls
```

Use `SMTP_SECURITY=ssl` with the provider's TLS port for implicit TLS. Restart the backend after changing environment settings. Never enter SMTP passwords in chat or commit them. The account card reports when SMTP configuration is missing; delivery failures appear as **Account balance email** jobs in automation history. No success is reported before the mail server accepts the message. Emails contain the masked account label, balance type/date, balance and target; original statements, screenshots, OTPs and bank passwords are not attached.

**Statement images:** every PDF page can be downloaded as PNG from its statement detail page. An optional bank screen image is saved alongside the original only after a supported reviewed workflow downloads a valid statement and confirms the account identity. Login and OTP forms are excluded. Screenshots are private tenant-scoped files served only through authenticated statement endpoints with `Cache-Control: no-store`.

HDFC login/OTP currently returns to login even in a manual browser, and its authenticated statement selectors remain unverified. Configuring monitoring does not make that bank workflow operational. Live HDFC collection and its screenshot remain unverified until manual access and the statement workflow can be reviewed. Uploaded official statements can exercise the downstream processing independently.

Validation: 108 backend cases passed in the first related-suite run; the bulk-lock failure in that run was fixed. The final extraction/monitoring suite passed 33 cases, including actual local OCR, review confirmation, mismatched-currency rejection, per-account browser wait/screenshot linkage, missing-SMTP failure reporting, and mocked delivery to the configured account recipient. Eight related frontend tests and TypeScript checking passed. All SMTP sends were mocked, browser collection used synthetic data, and no real bank login was submitted during this change.

The subsequent normal-report update passed 14 backend monitoring tests and two frontend monitoring tests, covering below/equal/above target, distinct targets and recipients for two accounts, duplicate suppression, and stale-observation skipping. All delivery checks still use mocked SMTP.
