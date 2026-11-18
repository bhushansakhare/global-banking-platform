import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import { AutomationActions, AutomationSummary } from '@/features/accounts/AutomationControls';
import { ConnectionSetupModal } from '@/features/accounts/ConnectionSetupModal';
import { useAuth } from '@/stores/auth';
import type { Account, AccountConnectionMethods, AutomationView } from '@/types/api';
import { statusTone } from '@/utils/status';
import { mockFetch, renderWithProviders, signIn } from './helpers';

function account(view: Partial<AutomationView>, over: Partial<Account> = {}): Account {
  return {
    id: 'a1', nickname: 'bhushan sakhare', holder_name: null, account_type: 'savings', currency: 'INR', country_code: 'IN',
    account_number_masked: '••••0127', institution: { id: 'i1', name: 'hdfc bank', country_code: 'IN' },
    connection: { id: 'c1', status: 'pending', provider_code: 'india_account_aggregator', provider_name: 'India Account Aggregator',
      integration_status: 'implemented_unverified', is_demo: false, last_checked_at: null, last_success_at: null, last_error: null,
      consecutive_failures: 0, has_credentials: false, institution: 'hdfc bank', connection_method: 'authorised_data_provider',
      environment: 'sandbox', provider_type: 'account_aggregator' },
    automation_enabled: true, low_balance_threshold: null, statement_period: 'monthly', last_sync_at: null, last_statement_date: null,
    connection_health: 'unknown', is_demo: false, notes: null, created_at: '2026-11-16T00:00:00Z', balances: {},
    automation_paused: false, sync_schedule: null, timezone: null, last_attempted_sync_at: null, last_successful_sync_at: null,
    last_sync_error: null, last_sync_result: null,
    automation: { status: 'authorization_required', reason: 'Authorisation has not been completed.', next_sync_at: null, retry: null,
      action_required: 'authorize', ...view },
    ...over,
  };
}

const methods: AccountConnectionMethods = {
  current_provider: 'manual_upload', consent: null, methods: [
    { method: 'official_bank_api', label: 'Official Bank API', available: false, status: 'awaiting_bank_approval',
      reason: 'HDFC Bank publishes APIs only to approved partners.', provider_code: null, provider_name: null, integration_status: null,
      environment: null, automatic: false, capabilities: {}, setup_steps: [], reference: null, recommended: false },
    { method: 'authorised_data_provider', label: 'Authorised Banking Data Provider', available: false, status: 'provider_setup_required',
      reason: 'Implemented but no provider credentials yet.', provider_code: 'india_account_aggregator', provider_name: 'India Account Aggregator',
      integration_status: 'implemented_unverified', environment: 'sandbox', automatic: true,
      capabilities: { transactions: true, current_balance: true, statements: false, automatic_sync: true },
      setup_steps: ['Register on Setu Bridge'], reference: null, recommended: false },
    { method: 'permitted_browser_automation', label: 'Permitted Browser Automation', available: false, status: 'not_permitted',
      reason: 'No documented permission.', provider_code: null, provider_name: null, integration_status: null, environment: null,
      automatic: false, capabilities: {}, setup_steps: [], reference: null, recommended: false },
    { method: 'manual_statement_import', label: 'Manual Statement Import (fallback)', available: true, status: 'available',
      reason: 'Fallback.', provider_code: 'manual_upload', provider_name: 'Statement Upload', integration_status: 'production_verified',
      environment: 'local', automatic: false, capabilities: { statements: true }, setup_steps: [], reference: null, recommended: false },
  ],
};

describe('Automation controls', () => {
  beforeEach(() => { useAuth.getState().clear(); signIn(); });

  it('never offers sync or pause for an account that is not authorised', () => {
    renderWithProviders(<AutomationActions account={account({})} onConnect={() => undefined} />);
    expect(screen.getByRole('button', { name: /authorise/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /run sync now/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /pause/i })).toBeNull();
  });

  it('shows sync, pause and disable for a healthy connected account and calls the API', async () => {
    const f = mockFetch({ '/accounts/a1/automation/action': account({ status: 'paused' }, { automation_paused: true }),
      '/accounts/a1/sync': { job_id: 'j1', created: true, status: 'queued' } });
    const acc = account({ status: 'healthy', action_required: null, reason: 'ok' },
      { connection: { ...account({}).connection!, status: 'active' }, connection_health: 'healthy' });
    renderWithProviders(<AutomationActions account={acc} />);
    await userEvent.click(screen.getByRole('button', { name: /run sync now/i }));
    await userEvent.click(screen.getByRole('button', { name: /pause/i }));
    await waitFor(() => expect(f).toHaveBeenCalledTimes(2));
    const bodies = f.mock.calls.map(([u, i]) => [String(u).replace(/^.*\/api\/v1/, ''), (i as RequestInit).body]);
    expect(bodies).toContainEqual(['/accounts/a1/automation/action', JSON.stringify({ action: 'pause' })]);
    expect(screen.getByRole('button', { name: /disable/i })).toBeInTheDocument();
  });

  it('shows resume for a paused account', () => {
    const acc = account({ status: 'paused', action_required: null }, { automation_paused: true,
      connection: { ...account({}).connection!, status: 'active' } });
    renderWithProviders(<AutomationActions account={acc} />);
    expect(screen.getByRole('button', { name: /resume/i })).toBeInTheDocument();
  });

  it('summarises last attempted / successful sync, retry and error honestly', () => {
    renderWithProviders(<AutomationSummary account={account({ status: 'degraded', reason: 'Provider timeout',
      retry: { attempt: 2, max_attempts: 4, next_attempt_at: new Date(Date.now() + 120_000).toISOString(), error: 'timeout' } },
    { last_sync_error: 'Provider timeout' })} />);
    expect(screen.getByText('Degraded')).toBeInTheDocument();
    expect(screen.getAllByText('Never')).toHaveLength(2); // last successful + last attempted sync
    expect(screen.getByText(/Attempt 2\/4/)).toBeInTheDocument();
    expect(screen.getAllByText('Provider timeout').length).toBeGreaterThan(0);
  });

  it('labels every automation state, never showing unknown as healthy', () => {
    for (const s of ['not_configured', 'provider_setup_required', 'authorization_required', 'ready', 'scheduled', 'running',
      'waiting_for_user', 'paused', 'failed', 'degraded', 'healthy']) {
      expect(statusTone(s)[1]).not.toMatch(/_/);
    }
    expect(statusTone('unknown')[1]).toBe('Not verified yet');
    expect(statusTone('not_applicable')[1]).toBe('No live connection');
  });
});

describe('Connection setup', () => {
  beforeEach(() => { useAuth.getState().clear(); signIn(); });

  it('lists only genuinely available methods as selectable and explains the rest', async () => {
    const f = mockFetch({ '/accounts/a1/connection-methods': methods,
      '/accounts/a1/connection-method': account({ status: 'provider_setup_required', action_required: 'provider_setup' }) });
    renderWithProviders(<ConnectionSetupModal account={account({ status: 'not_configured' })} open onClose={() => undefined} />);
    expect(await screen.findByText('Awaiting bank approval')).toBeInTheDocument();
    expect(screen.getByText('Not permitted')).toBeInTheDocument();
    expect(screen.getByText('Provider setup required')).toBeInTheDocument();
    const radios = screen.getAllByRole('radio');
    expect(radios).toHaveLength(2); // AA (awaiting provider setup) and manual — not official API / browser
    await userEvent.click(radios[0]);
    await userEvent.click(screen.getByRole('button', { name: /use this method/i }));
    await waitFor(() => expect(f.mock.calls.some(([u]) => String(u).endsWith('/accounts/a1/connection-method'))).toBe(true));
    expect(screen.queryByLabelText(/password/i)).toBeNull();
  });
});
