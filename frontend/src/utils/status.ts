/** Maps backend states to UI tones and British-English labels. Red is reserved for genuine errors. */
export type Tone = 'neutral' | 'brand' | 'success' | 'warning' | 'danger' | 'info' | 'demo';

const MAP: Record<string, [Tone, string]> = {
  // connections / health
  active: ['success', 'Active'], pending: ['neutral', 'Pending'], requires_auth: ['warning', 'Authentication needed'],
  consent_expired: ['warning', 'Consent expired'], error: ['danger', 'Error'], disconnected: ['neutral', 'Disconnected'],
  healthy: ['success', 'Healthy'], degraded: ['warning', 'Degraded'], failing: ['danger', 'Failing'],
  unknown: ['neutral', 'Not verified yet'], not_applicable: ['neutral', 'No live connection'],
  // per-account automation
  not_configured: ['neutral', 'Not configured'], provider_setup_required: ['warning', 'Provider setup required'],
  authorization_required: ['warning', 'Authorisation required'], ready: ['brand', 'Ready'],
  scheduled: ['info', 'Scheduled'], waiting_for_user: ['warning', 'Waiting for you'], paused: ['neutral', 'Paused'],
  // connection-method availability
  available: ['success', 'Available'], awaiting_bank_approval: ['warning', 'Awaiting bank approval'],
  not_permitted: ['neutral', 'Not permitted'], unsupported: ['neutral', 'Unsupported'],
  permitted: ['success', 'Permitted'], review_required: ['warning', 'Profile review required'],
  // environments / balance verification
  sandbox: ['warning', 'Sandbox'], production: ['success', 'Live'], local: ['neutral', 'Local'],
  provider_reported: ['success', 'Bank-reported'], statement_derived: ['info', 'From statement'],
  user_entered: ['neutral', 'User entered'], unverified: ['neutral', 'Unverified'],
  // jobs
  queued: ['neutral', 'Queued'], running: ['info', 'Running'], succeeded: ['success', 'Succeeded'],
  failed: ['danger', 'Failed'], retry_scheduled: ['warning', 'Retry scheduled'], cancelled: ['neutral', 'Cancelled'],
  awaiting_user: ['warning', 'Awaiting you'],
  // statements
  processing: ['info', 'Processing'], processed: ['success', 'Processed'],
  processed_with_warnings: ['warning', 'Processed · warnings'], needs_review: ['warning', 'Needs review'],
  needs_ocr: ['warning', 'Needs OCR'], duplicate: ['neutral', 'Duplicate'], received: ['neutral', 'Received'],
  downloaded: ['success', 'Downloaded'],
  // reconciliation
  balanced: ['success', 'Balanced'], completed_with_exceptions: ['warning', 'Exceptions'],
  matched: ['success', 'Matched'], suggested: ['info', 'Suggested'], exception: ['warning', 'Exception'],
  unreconciled: ['neutral', 'Unreconciled'], manual: ['brand', 'Manual'], confirmed: ['success', 'Confirmed'],
  rejected: ['neutral', 'Rejected'], open: ['warning', 'Open'], resolved: ['success', 'Resolved'],
  ignored: ['neutral', 'Ignored'],
  // integration status
  production_verified: ['success', 'Verified'], implemented_unverified: ['warning', 'Implemented · unverified'],
  planned: ['neutral', 'Planned'], demo: ['demo', 'Demo'],
  // severity
  info: ['info', 'Info'], warning: ['warning', 'Warning'], critical: ['danger', 'Critical'],
  success: ['success', 'Success'], failure: ['danger', 'Failure'],
};

export function statusTone(s: string | null | undefined): [Tone, string] {
  if (!s) return ['neutral', '—'];
  return MAP[s] ?? ['neutral', s.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())];
}

export const BALANCE_LABELS: Record<string, string> = {
  available: 'Available', ledger: 'Ledger', current: 'Current (provider)', statement_opening: 'Statement opening',
  statement_closing: 'Statement closing', reported: 'Reported',
};

export const METHOD_LABELS: Record<string, string> = {
  official_bank_api: 'Official Bank API', authorised_data_provider: 'Authorised Banking Data Provider',
  permitted_browser_automation: 'Permitted Browser Automation', manual_statement_import: 'Manual Statement Import',
  demo: 'Demo Bank Simulator',
};

export const JOB_LABELS: Record<string, string> = {
  collect_account: 'Bank collection', parse_statement: 'Statement extraction', reconcile_account: 'Reconciliation',
  daily_report: 'Daily report',
  open_bank_login: 'Official bank login',
  low_balance_email: 'Low-balance email',
  balance_email: 'Account balance email',
};
