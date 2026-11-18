# User guide

## 1. First run
Open the app, enter your organisation, name, email, password, reporting currency, country and time zone.
You become the **Owner**. Invite colleagues under **Settings → Users** (Admin, Finance Analyst, Viewer).

## 2. Add banks and accounts
- **Institutions** lists every bank you can use and *how* its data is accessed. Any bank can be added as a
  statement-upload institution. The **Integration providers** tab shows which aggregators are verified,
  unverified or planned.
- **Bank Accounts → Add account**: choose country and institution, nickname, currency, optional account
  number (encrypted; only the last four digits are ever shown) and a low-balance alert threshold.
- Want to explore first? **Dashboard → Load demo environment** creates 10 demo accounts in 5 currencies.
  Demo data carries a pink **DEMO** badge everywhere and is watermarked in reports.

## 3. Switch automation on
**Automation Center** → set the daily time (or a cron schedule) and time zone → turn on **Master
automation**. Each run collects every enabled account, extracts new statements, reconciles and produces the
daily report. Use **Run now** any time. **Pause** holds queued work; switching **off** cancels scheduled
work that hasn't started.

If a bank asks for a one-time passcode, the job appears under **Manual intervention**. Open it, enter the
code and resume. Passcodes are never stored.

## 4. Statements
Automation downloads statements where the provider supports it; otherwise **Upload statement** (PDF,
XLSX, XLS, CSV). Each statement shows extraction status, confidence, the opening + credits − debits =
closing check, and warnings. Scanned PDFs are flagged *Needs OCR* rather than guessed. Duplicate files are
detected automatically. Preview or download the original at any time.

## 5. Transactions
Search, filter (account, dates, direction, status, category, amount), sort, select rows to categorise or
export (CSV/Excel). Click a row to see the original extracted values. *Possible duplicate* flags rows
that share date, amount and reference with another entry.

## 6. Balances
Available, ledger, provider-current and statement balances are shown in separate columns with time and
source. Record exchange rates here; consolidated totals only include currencies with a rate.

## 7. Reconciliation
Import your cash-book (**Import cash-book**: CSV/Excel with Date, Description, Amount or Debit/Credit,
Reference). Runs happen automatically after each statement, or **Run reconciliation** manually.
In a session: **Needs review** lists suggested matches to confirm or reject; **Exceptions** lists
unmatched items, balance differences and duplicates — resolve with a mandatory note or create a manual
match. Bank data is never altered; every decision is audited.

## 8. Reports, analytics, notifications, audit
**Reports**: 12 report types in Excel, CSV or PDF. **Analytics**: balance history, cash flow, monthly
activity, distributions, reconciliation performance. **Notifications**: sync results, authentication
requests, low balances, mismatches, duplicates, failed jobs — choose which you receive under Preferences.
**Audit Logs**: immutable record of every action.
