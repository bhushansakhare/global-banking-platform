export type UUID = string;
export type Money = string | null;

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface User {
  id: UUID;
  email: string;
  full_name: string;
  role: 'owner' | 'admin' | 'analyst' | 'viewer';
  role_name: string;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
  permissions: string[];
}

export interface TokenResponse {
  access_token: string;
  expires_in: number;
  csrf_token: string;
  user: User;
}

export interface Provider {
  id: UUID;
  code: string;
  name: string;
  provider_type: string;
  countries: string[];
  currencies: string[];
  capabilities: string[];
  auth_method: string;
  consent_required: boolean;
  refresh_frequency: string;
  integration_status: 'production_verified' | 'implemented_unverified' | 'planned' | 'demo';
  is_demo: boolean;
  documentation_url: string | null;
  notes: string | null;
  configured: boolean;
}

export interface Country {
  code: string;
  name: string;
  default_currency: string;
  region: string;
  open_banking_framework: string | null;
}

export interface Institution {
  id: UUID;
  name: string;
  country_code: string;
  bic: string | null;
  logo_url: string | null;
  external_id: string;
  supported_currencies: string[];
  supports_balances: boolean;
  supports_transactions: boolean;
  supports_statements: boolean;
  is_demo: boolean;
  is_custom: boolean;
  account_count?: number;
  provider: { code: string; name: string; integration_status: Provider['integration_status'] };
}

export interface Connection {
  id: UUID;
  status: 'pending' | 'active' | 'requires_auth' | 'consent_expired' | 'error' | 'disconnected';
  provider_code: string;
  provider_name: string;
  integration_status: string;
  is_demo: boolean;
  last_checked_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  consecutive_failures: number;
  has_credentials: boolean;
  institution: string;
  connection_method: ConnectionMethodKey | null;
  environment: 'sandbox' | 'production' | 'demo' | 'local' | null;
  provider_type: string;
}

export type ConnectionMethodKey = 'official_bank_api' | 'authorised_data_provider' | 'permitted_browser_automation'
  | 'manual_statement_import' | 'demo';

export type AvailabilityStatus = 'available' | 'provider_setup_required' | 'awaiting_bank_approval' | 'not_permitted'
  | 'unsupported';

export interface CapabilityMatrix {
  account_information: boolean; available_balance: boolean; ledger_balance: boolean; current_balance: boolean;
  transactions: boolean; statements: boolean; historical_data: boolean; automatic_sync: boolean;
}

export interface ConnectionMethodOption {
  method: ConnectionMethodKey;
  label: string;
  available: boolean;
  status: AvailabilityStatus;
  reason: string;
  provider_code: string | null;
  provider_name: string | null;
  integration_status: string | null;
  environment: string | null;
  automatic: boolean;
  capabilities: Partial<CapabilityMatrix>;
  setup_steps: string[];
  reference: string | null;
  recommended: boolean;
}

export type AutomationStatus = 'not_configured' | 'provider_setup_required' | 'authorization_required' | 'ready'
  | 'scheduled' | 'running' | 'waiting_for_user' | 'paused' | 'failed' | 'degraded' | 'healthy';

export interface AutomationView {
  status: AutomationStatus;
  reason: string;
  next_sync_at: string | null;
  retry: { attempt: number; max_attempts: number; next_attempt_at: string; error: string | null } | null;
  action_required: 'connect' | 'authorize' | 'provider_setup' | 'resolve' | 'enable' | null;
}

export interface AccountConnectionMethods {
  current_provider: string | null;
  consent: { status: string; granted_at: string | null; expires_at: string | null; authorisation_url: string | null;
    created_at: string } | null;
  methods: ConnectionMethodOption[];
}

export type BalanceType = 'available' | 'ledger' | 'current' | 'statement_opening' | 'statement_closing' | 'reported';

export interface Balance {
  id: UUID;
  account_id: UUID;
  balance_type: BalanceType;
  amount: string;
  currency: string;
  as_of: string;
  source: 'provider' | 'statement' | 'manual' | 'demo';
  source_ref: string | null;
  is_demo: boolean;
  captured_at: string;
  verification_status?: 'provider_reported' | 'statement_derived' | 'user_entered' | 'demo' | 'unverified';
}

export interface Account {
  id: UUID;
  nickname: string;
  holder_name: string | null;
  account_type: string;
  currency: string;
  country_code: string;
  account_number_masked: string;
  institution: { id: UUID; name: string; country_code: string };
  connection: Connection | null;
  automation_enabled: boolean;
  low_balance_threshold: Money;
  statement_period: string;
  last_sync_at: string | null;
  last_statement_date: string | null;
  connection_health: 'healthy' | 'degraded' | 'failing' | 'unknown' | 'not_applicable';
  is_demo: boolean;
  notes: string | null;
  created_at: string;
  balances: Partial<Record<BalanceType, Balance>>;
  automation_paused: boolean;
  sync_schedule: string | null;
  timezone: string | null;
  last_attempted_sync_at: string | null;
  last_successful_sync_at: string | null;
  last_sync_error: string | null;
  last_sync_result: Record<string, string | number | boolean> | null;
  automation: AutomationView | null;
  stats?: { transactions: number; statements: number; unreconciled: number;
    cash_flow_30d?: { credits: string; debits: string; net: string; count: number; currency: string } };
  recent_jobs?: Job[];
}

export interface StatementWarning {
  code: string;
  message: string;
  [k: string]: unknown;
}

export interface Statement {
  id: UUID;
  account_id: UUID;
  account_name: string | null;
  source: 'provider_download' | 'upload' | 'demo';
  period_start: string | null;
  period_end: string | null;
  opening_balance: Money;
  closing_balance: Money;
  total_credits: Money;
  total_debits: Money;
  transaction_count: number | null;
  currency: string;
  download_status: string;
  processing_status: string;
  arithmetic_valid: boolean | null;
  arithmetic_difference: Money;
  parser_name: string | null;
  parse_confidence: string | null;
  warnings: StatementWarning[];
  duplicate_of_id: UUID | null;
  downloaded_at: string | null;
  created_at: string;
  is_demo: boolean;
  file: { original_filename: string; format: string; size_bytes: number; sha256: string; scanned: boolean | null;
    page_count: number | null } | null;
  institution?: string;
  bank_screenshot_available?: boolean;
  parsing_jobs?: {
    id: UUID; status: string; parser_name: string | null; rows_extracted: number; rows_inserted: number;
    rows_duplicate: number; started_at: string | null; finished_at: string | null; error: string | null;
    diagnostics: Record<string, unknown>;
  }[];
}

export interface Transaction {
  id: UUID;
  account_id: UUID;
  account_name: string | null;
  bank_name: string | null;
  statement_id: UUID | null;
  source: string;
  booking_date: string;
  value_date: string | null;
  description: string;
  reference: string | null;
  debit: string;
  credit: string;
  amount: string;
  currency: string;
  running_balance: Money;
  category: { id: UUID; name: string; colour: string } | null;
  reconciliation_status: 'unreconciled' | 'matched' | 'suggested' | 'exception' | 'manual';
  duplicate_suspect: boolean;
  is_demo: boolean;
}

export interface TransactionPage extends Page<Transaction> {
  totals: { credits: string; debits: string; mixed_currencies: boolean };
}

export interface Category {
  id: UUID;
  name: string;
  colour: string;
  keywords: string[];
  direction: string;
  priority: number;
}

export interface BookEntry {
  id: UUID;
  account_id: UUID;
  entry_date: string;
  description: string;
  reference: string | null;
  amount: string;
  currency: string;
  source: string;
  reconciliation_status: string;
  is_demo: boolean;
  notes: string | null;
}

export interface ReconSession {
  id: UUID;
  account_id: UUID;
  account_name: string | null;
  statement_id: UUID | null;
  period_start: string;
  period_end: string;
  status: 'running' | 'balanced' | 'completed_with_exceptions' | 'failed';
  trigger: string;
  rules: Record<string, unknown>;
  engine_version: string;
  bank_transaction_count: number;
  book_entry_count: number;
  matched_count: number;
  suggested_count: number;
  unmatched_bank_count: number;
  unmatched_book_count: number;
  exception_count: number;
  opening_balance: Money;
  closing_balance: Money;
  expected_closing_balance: Money;
  balance_difference: Money;
  match_rate: string | null;
  created_at: string;
  completed_at: string | null;
  summary: { has_book_side?: boolean; has_statement?: boolean; critical_exceptions?: number; note?: string | null };
}

export interface ReconMatch {
  id: UUID;
  match_type: string;
  status: 'confirmed' | 'suggested' | 'rejected' | 'manual';
  original_status: string;
  score: string;
  amount_difference: string;
  date_difference_days: number;
  reasons: string[];
  decided_at: string | null;
  decision_note: string | null;
  transaction: Transaction | null;
  book_entry: BookEntry | null;
}

export interface ReconException {
  id: UUID;
  exception_type: string;
  severity: 'info' | 'warning' | 'critical';
  message: string;
  amount: Money;
  details: Record<string, unknown>;
  status: 'open' | 'resolved' | 'ignored';
  transaction_id: UUID | null;
  book_entry_id: UUID | null;
  resolved_at: string | null;
  resolution_note: string | null;
  transaction?: Transaction | null;
  book_entry?: BookEntry | null;
}

export interface ReconSessionDetail extends ReconSession {
  currency: string;
  is_demo: boolean;
  matches: ReconMatch[];
  exceptions: ReconException[];
}

export interface AutomationSettings {
  master_enabled: boolean;
  paused: boolean;
  schedule_type: 'daily' | 'custom';
  daily_time: string;
  cron_expression: string | null;
  timezone: string;
  lookback_days: number;
  auto_reconcile: boolean;
  auto_generate_daily_report: boolean;
  max_attempts: number;
  retry_base_seconds: number;
  next_run_at: string | null;
  last_run_at: string | null;
}

export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'retry_scheduled' | 'cancelled' | 'awaiting_user';

export interface Job {
  id: UUID;
  job_type: string;
  status: JobStatus;
  trigger: string;
  account_id: UUID | null;
  account_name: string | null;
  run_id: UUID | null;
  parent_job_id: UUID | null;
  attempts: number;
  max_attempts: number;
  run_after: string;
  started_at: string | null;
  finished_at: string | null;
  progress: number;
  progress_message: string | null;
  error_code: string | null;
  error_message: string | null;
  requires_user_action: string | null;
  result: Record<string, unknown>;
  created_at: string;
  duration_seconds: number | null;
}

export interface JobDetail extends Job {
  payload: Record<string, unknown>;
  logs: { id: UUID; ts: string; level: string; message: string; context: Record<string, unknown> }[];
  children: Job[];
}

export interface AutomationOverview {
  settings: AutomationSettings;
  jobs_24h: Partial<Record<JobStatus, number>>;
  active: Partial<Record<JobStatus, number>>;
  accounts: {
    id: UUID; nickname: string; institution: string; automation_enabled: boolean; connection_status: string | null;
    provider: string | null; health: string; last_sync_at: string | null; is_demo: boolean; latest_job: Job | null;
    automation_paused: boolean; provider_code: string | null; connection_method: ConnectionMethodKey | null;
    environment: string | null; last_attempted_sync_at: string | null; last_successful_sync_at: string | null;
    last_sync_error: string | null; account_number_masked: string; currency: string; automation: AutomationView | null;
  }[];
}

export interface Notification {
  id: UUID;
  type: string;
  severity: 'info' | 'success' | 'warning' | 'error';
  title: string;
  body: string;
  link: string | null;
  read_at: string | null;
  created_at: string;
}

export interface AuditEntry {
  id: UUID;
  ts: string;
  actor_user_id: UUID | null;
  actor_type: string;
  actor_label: string | null;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  outcome: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  meta: Record<string, unknown>;
  ip_address: string | null;
}

export interface Report {
  id: UUID;
  report_type: string;
  title: string;
  file_format: 'xlsx' | 'csv' | 'pdf';
  parameters: Record<string, unknown>;
  size_bytes: number | null;
  status: string;
  trigger: string;
  contains_demo_data: boolean;
  created_at: string;
}

export interface BalanceTotal {
  accounts: number;
  by_currency: Record<string, number>;
  total_reporting: number | null;
  unconverted_currencies: string[];
  oldest_as_of: string | null;
}

export interface Summary {
  reporting_currency: string;
  demo_accounts: number;
  live_accounts: number;
  connected_banks: number;
  total_accounts: number;
  balances: Record<'available' | 'ledger' | 'current', BalanceTotal>;
  reported_balance: { total_reporting: number | null; accounts: number; unconverted_currencies: string[] };
  credits_30d: number | null;
  debits_30d: number | null;
  flows_unconverted_currencies: string[];
  transactions_30d: number;
  transactions_today: number;
  last_activity_date: string | null;
  last_activity_count: number;
  reconciliation: { by_status: Record<string, number>; unmatched: number; suggested: number;
    sessions_balanced: number; sessions_with_exceptions: number };
  automation: { master_enabled: boolean; paused: boolean; next_run_at: string | null; last_run_at: string | null;
    jobs_24h: Partial<Record<JobStatus, number>> };
  failed_connections: { id: UUID; institution: string; status: string; last_error: string | null }[];
  account_automation?: Partial<Record<string, number>>;
  pending_statements: number;
  fx_rates_used: { base: string; quote: string; rate: string; rate_date: string; source: string }[];
  fx_missing: string[];
}

export interface Series<T> {
  reporting_currency?: string;
  series: T[];
  fx_missing?: string[];
  basis?: string;
}

export interface Distributions {
  reporting_currency: string;
  banks: { name: string; value: number }[];
  currencies: { currency: string; original: number; converted: number | null }[];
  categories: { name: string; colour: string; debits: number; credits: number }[];
  fx_missing: string[];
}

export interface ApiErrorBody {
  error: { code: string; message: string; details: unknown; request_id: string | null };
}
