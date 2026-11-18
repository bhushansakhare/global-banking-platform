/** Typed endpoint functions. Components never call fetch directly. */
import { request } from '@/services/http';
import type {
  Account, AccountConnectionMethods, AuditEntry, ConnectionMethodOption, AutomationOverview, AutomationSettings, BookEntry, Category, Connection, Country,
  Distributions, Institution, Job, JobDetail, Notification, Page, Provider, ReconSession, ReconSessionDetail, Report,
  Series, Statement, Summary, TokenResponse, TransactionPage, Transaction, User, Balance,
} from '@/types/api';

type Q = Record<string, string | number | boolean | null | undefined>;

export interface BrowserProfile {
  key: string; name: string; country: string; currencies: string[]; website: string; login_url: string;
  permission: 'unverified' | 'permitted' | 'not_permitted'; reviewed: boolean; limitations: string[];
  credential_storage_notice?: string | null; credential_storage_reference?: string | null;
  capabilities: { browser_login: string; official_login: boolean; automatic_statement_retrieval: boolean;
    scheduled_automation: string; live_balance: boolean; customer_id_autofill?: boolean; saved_login?: boolean };
}
export interface BrowserStatus {
  enabled: boolean; profile_key: string | null; has_customer_id: boolean; has_password?: boolean; profiles: BrowserProfile[];
  job: { id: string; status: string; message: string | null; error: string | null; result: Record<string, unknown>;
    error_code?: string | null; requires_user_action?: string | null } | null;
}
export const browserBankingApi = {
  profiles: (q: Q = {}) => request<BrowserProfile[]>('/banks/browser-profiles', { query: q }),
  saveProfile: (key: string, body: unknown) => request<BrowserProfile>(`/banks/browser-profiles/${key}`, { method: 'PUT', body }),
  status: (id: string) => request<BrowserStatus>(`/accounts/${id}/browser`),
  saveLogin: (id: string, body: { profile_key: string; customer_id: string; password?: string }) =>
    request<{ saved: boolean; has_customer_id: boolean; has_password: boolean }>(`/accounts/${id}/browser/credentials`, { method: 'PUT', body }),
  forgetLogin: (id: string) => request<{ saved: boolean }>(`/accounts/${id}/browser/credentials`, { method: 'DELETE' }),
  start: (id: string, body: { profile_key: string; customer_id?: string; login_only: boolean; date_from?: string; date_to?: string; authenticate?: boolean }) =>
    request<{ job_id: string; created: boolean; status: string }>(`/accounts/${id}/browser/start`, { method: 'POST', body }),
};

export const authApi = {
  setupStatus: () => request<{ setup_required: boolean; demo_allowed: boolean; environment: string }>('/auth/setup-status'),
  setup: (b: { organisation: string; full_name: string; email: string; password: string; reporting_currency: string;
    timezone: string; country_code?: string }) => request<TokenResponse>('/auth/setup', { method: 'POST', body: b }),
  login: (email: string, password: string) =>
    request<TokenResponse>('/auth/login', { method: 'POST', body: { email, password } }),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
  me: () => request<{ user: User }>('/auth/me'),
  changePassword: (current_password: string, new_password: string) =>
    request<void>('/auth/change-password', { method: 'POST', body: { current_password, new_password } }),
};

export const usersApi = {
  list: () => request<User[]>('/users'),
  roles: () => request<{ code: string; name: string; permissions: string[] }[]>('/users/roles'),
  create: (b: { email: string; full_name: string; role: string; password: string }) =>
    request<User>('/users', { method: 'POST', body: b }),
  update: (id: string, b: { full_name?: string; role?: string; is_active?: boolean }) =>
    request<User>(`/users/${id}`, { method: 'PATCH', body: b }),
};

export const institutionsApi = {
  countries: () => request<Country[]>('/countries'),
  providers: () => request<Provider[]>('/banks/providers'),
  list: (q: Q = {}) => request<Page<Institution>>('/institutions', { query: q }),
  create: (b: { name: string; country_code: string; bic?: string; supported_currencies?: string[] }) =>
    request<Institution>('/institutions', { method: 'POST', body: b }),
  remove: (id: string) => request<void>(`/institutions/${id}`, { method: 'DELETE' }),
  connectionMethods: (q: { country: string; institution_id?: string; bank_name?: string }) =>
    request<ConnectionMethodOption[]>('/banks/connection-methods', { query: q }),
  sync: (provider: string, country: string) =>
    request<{ fetched: number; created: number }>('/institutions/sync', { method: 'POST', query: { provider, country } }),
};

export interface AccountInput {
  institution_id: string; nickname: string; currency: string; account_type?: string; account_number?: string;
  holder_name?: string; low_balance_threshold?: string; automation_enabled?: boolean; statement_period?: string;
  demo_scenario?: string; notes?: string; connection_provider?: string; sync_schedule?: string; timezone?: string;
  country_code?: string;
}

export interface AuthorizationStartResult {
  authorisation_url?: string; status: string; provider: string; environment: string; instructions?: string;
}

export const accountsApi = {
  list: (q: Q = {}) => request<Account[]>('/accounts', { query: q }),
  get: (id: string) => request<Account>(`/accounts/${id}`),
  create: (b: AccountInput) => request<Account>('/accounts', { method: 'POST', body: b }),
  update: (id: string, b: Omit<Partial<AccountInput>, 'sync_schedule'> & { timezone?: string; sync_schedule?: string | null }) =>
    request<Account>(`/accounts/${id}`, { method: 'PATCH', body: b }),
  remove: (id: string) => request<void>(`/accounts/${id}`, { method: 'DELETE' }),
  setAutomation: (id: string, enabled: boolean) =>
    request<Account>(`/accounts/${id}/automation`, { method: 'POST', body: { enabled } }),
  automationAction: (id: string, action: 'enable' | 'pause' | 'resume' | 'disable') =>
    request<Account>(`/accounts/${id}/automation/action`, { method: 'POST', body: { action } }),
  syncNow: (id: string) =>
    request<{ job_id: string; created: boolean; status: string }>(`/accounts/${id}/sync`, { method: 'POST' }),
  syncHistory: (id: string, q: Q = {}) => request<Page<Job>>(`/accounts/${id}/sync-history`, { query: q }),
  connectionMethods: (id: string) => request<AccountConnectionMethods>(`/accounts/${id}/connection-methods`),
  setConnectionMethod: (id: string, provider_code: string) =>
    request<Account>(`/accounts/${id}/connection-method`, { method: 'POST', body: { provider_code } }),
  authorize: (id: string, b: { mobile?: string; redirect_url?: string }) =>
    request<AuthorizationStartResult>(`/accounts/${id}/authorize`, { method: 'POST', body: b }),
  verifyAuthorization: (id: string) => request<Account>(`/accounts/${id}/authorize/verify`, { method: 'POST' }),
  connections: () => request<Connection[]>('/connections'),
  check: (id: string) => request<Connection>(`/connections/${id}/check`, { method: 'POST' }),
  disconnect: (id: string) => request<Connection>(`/connections/${id}/disconnect`, { method: 'POST' }),
  reconnect: (id: string) => request<Connection>(`/connections/${id}/reconnect`, { method: 'POST' }),
  demoScenario: (id: string, scenario: string) =>
    request<Connection>(`/connections/${id}/demo-scenario`, { method: 'POST', body: { scenario } }),
  gocardlessRequisition: (id: string, redirect_url: string) =>
    request<{ authorisation_url: string }>(`/connections/${id}/gocardless/requisition`,
      { method: 'POST', body: { redirect_url } }),
  plaidLinkToken: (id: string) => request<{ link_token: string }>(`/connections/${id}/plaid/link-token`, { method: 'POST' }),
};

export interface AccountBalances {
  account_id: string; account_name: string; currency: string; institution: string; is_demo: boolean;
  balances: Partial<Record<string, Balance>>;
}

export const balancesApi = {
  latest: () => request<AccountBalances[]>('/balances'),
  history: (id: string, q: Q = {}) => request<Balance[]>(`/balances/${id}/history`, { query: q }),
  rates: () => request<{ id: string; base_currency: string; quote_currency: string; rate: string; rate_date: string;
    source: string }[]>('/balances/exchange-rates'),
  addRate: (b: { base_currency: string; quote_currency: string; rate: string; rate_date: string; source?: string }) =>
    request<{ id: string }>('/balances/exchange-rates', { method: 'POST', body: b }),
};

export const statementsApi = {
  list: (q: Q = {}) => request<Page<Statement>>('/statements', { query: q }),
  get: (id: string) => request<Statement>(`/statements/${id}`),
  transactions: (id: string) => request<Transaction[]>(`/statements/${id}/transactions`),
  upload: (accountId: string, file: File) => {
    const f = new FormData();
    f.append('account_id', accountId);
    f.append('file', file);
    return request<{ duplicate: boolean; statement: Statement; message?: string }>('/statements/upload',
      { method: 'POST', form: f });
  },
  reprocess: (id: string) => request<Statement>(`/statements/${id}/reprocess`, { method: 'POST' }),
  archive: (id: string) => request<void>(`/statements/${id}`, { method: 'DELETE' }),
};

export const transactionsApi = {
  list: (q: Q = {}) => request<TransactionPage>('/transactions', { query: q }),
  get: (id: string) => request<Transaction & { raw_data: Record<string, string>; fingerprint: string;
    provider_transaction_id: string | null }>(`/transactions/${id}`),
  categories: () => request<Category[]>('/transactions/categories'),
  createCategory: (b: Partial<Category>) => request<{ id: string }>('/transactions/categories', { method: 'POST', body: b }),
  categorise: (ids: string[], category_id: string | null) =>
    request<{ updated: number }>('/transactions/categorise', { method: 'POST', body: { transaction_ids: ids, category_id } }),
  bookEntries: (q: Q = {}) => request<Page<BookEntry>>('/book-entries', { query: q }),
  importBook: (accountId: string, file: File) => {
    const f = new FormData();
    f.append('account_id', accountId);
    f.append('file', file);
    return request<{ created: number; skipped_duplicates: number }>('/book-entries/import', { method: 'POST', form: f });
  },
  createBookEntry: (b: { account_id: string; entry_date: string; description: string; amount: string; reference?: string }) =>
    request<BookEntry>('/book-entries', { method: 'POST', body: b }),
};

export const reconApi = {
  run: (b: { account_id: string; statement_id?: string; period_start?: string; period_end?: string }) =>
    request<ReconSession>('/reconciliation/run', { method: 'POST', body: b }),
  sessions: (q: Q = {}) => request<Page<ReconSession>>('/reconciliation/sessions', { query: q }),
  session: (id: string) => request<ReconSessionDetail>(`/reconciliation/sessions/${id}`),
  decide: (matchId: string, decision: 'confirm' | 'reject', note?: string) =>
    request<{ id: string; status: string }>(`/reconciliation/matches/${matchId}/decision`,
      { method: 'POST', body: { decision, note } }),
  manualMatch: (sessionId: string, transaction_id: string, book_entry_id: string, note?: string) =>
    request<{ id: string }>(`/reconciliation/sessions/${sessionId}/manual-match`,
      { method: 'POST', body: { transaction_id, book_entry_id, note } }),
  updateException: (id: string, status: string, note: string) =>
    request(`/reconciliation/exceptions/${id}`, { method: 'PATCH', body: { status, note } }),
  rules: () => request<Record<string, string | number | boolean>>('/reconciliation/rules'),
  saveRules: (b: Record<string, unknown>) => request('/reconciliation/rules', { method: 'PUT', body: b }),
};

export const automationApi = {
  overview: () => request<AutomationOverview>('/automation'),
  updateSettings: (b: Partial<AutomationSettings>) =>
    request<AutomationSettings>('/automation/settings', { method: 'PUT', body: b }),
  master: (enabled: boolean) => request<AutomationSettings>('/automation/master', { method: 'POST', body: { enabled } }),
  pause: () => request<AutomationSettings>('/automation/pause', { method: 'POST' }),
  resume: () => request<AutomationSettings>('/automation/resume', { method: 'POST' }),
  runNow: (account_ids?: string[]) =>
    request<{ run_id: string; jobs_created: number }>('/automation/run-now', { method: 'POST', body: { account_ids } }),
  retryFailed: () => request<{ requeued: number }>('/automation/retry-failed', { method: 'POST' }),
  jobs: (q: Q = {}) => request<Page<Job>>('/jobs', { query: q }),
  job: (id: string) => request<JobDetail>(`/jobs/${id}`),
  retry: (id: string) => request<Job>(`/jobs/${id}/retry`, { method: 'POST' }),
  cancel: (id: string) => request<Job>(`/jobs/${id}/cancel`, { method: 'POST' }),
  resumeJob: (id: string, otp?: string) => request<Job>(`/jobs/${id}/resume`, { method: 'POST', body: { otp } }),
};

export const analyticsApi = {
  summary: () => request<Summary>('/analytics/summary'),
  balanceHistory: (days = 90) => request<Series<{ date: string; balance: number }>>('/analytics/balance-history', { query: { days } }),
  cashFlow: (days = 30) =>
    request<Series<{ date: string; inflow: number; outflow: number; net: number }>>('/analytics/cash-flow', { query: { days } }),
  monthly: (months = 12) =>
    request<Series<{ month: string; credits: number; debits: number; count: number }>>('/analytics/monthly', { query: { months } }),
  distributions: () => request<Distributions>('/analytics/distributions'),
  reconPerformance: (weeks = 12) => request<Series<{ week: string; sessions: number; matched: number; bank: number;
    exceptions: number; match_rate: number | null }>>('/analytics/reconciliation-performance', { query: { weeks } }),
};

export const reportsApi = {
  types: () => request<{ code: string; name: string }[]>('/reports/types'),
  list: (q: Q = {}) => request<Page<Report>>('/reports', { query: q }),
  create: (b: Record<string, unknown>) => request<Report>('/reports', { method: 'POST', body: b }),
};

export const notificationsApi = {
  list: (q: Q = {}) => request<Page<Notification> & { unread: number }>('/notifications', { query: q }),
  unread: () => request<{ unread: number }>('/notifications/unread-count'),
  read: (id: string) => request(`/notifications/${id}/read`, { method: 'POST' }),
  readAll: () => request<{ updated: number }>('/notifications/read-all', { method: 'POST' }),
  prefs: () => request<Record<string, boolean>>('/notifications/preferences'),
  savePrefs: (preferences: Record<string, boolean>) =>
    request<Record<string, boolean>>('/notifications/preferences', { method: 'PUT', body: { preferences } }),
};

export const auditApi = {
  list: (q: Q = {}) => request<Page<AuditEntry>>('/audit-logs', { query: q }),
};

export interface SettingsPayload {
  organisation: { name: string; reporting_currency: string; timezone: string; country_code: string | null; demo_mode: boolean };
  environment: string;
  demo_allowed: boolean;
  [key: string]: unknown;
}

export const settingsApi = {
  get: () => request<SettingsPayload>('/settings'),
  saveOrg: (b: Record<string, unknown>) => request('/settings/organisation', { method: 'PUT', body: b }),
  save: (key: string, value: Record<string, unknown>) => request(`/settings/${key}`, { method: 'PUT', body: { value } }),
  seedDemo: () => request<{ accounts_created: number }>('/settings/demo/seed', { method: 'POST' }),
  clearDemo: () => request<{ accounts_removed: number }>('/settings/demo/clear', { method: 'POST' }),
};
