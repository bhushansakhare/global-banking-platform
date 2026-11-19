import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import { BrowserBankConnection } from '@/features/accounts/BrowserBankConnection';
import { useAuth } from '@/stores/auth';
import type { Account } from '@/types/api';
import type { BrowserStatus } from '@/services/api';
import { mockFetch, renderWithProviders, signIn } from './helpers';

const account = { id: 'a1', country_code: 'IN', is_demo: false } as Account;
const status: BrowserStatus = {
  enabled: true, profile_key: 'hdfc_bank', has_customer_id: false, job: null,
  profiles: [{ key: 'hdfc_bank', name: 'HDFC Bank', country: 'IN', currencies: ['INR'],
    website: 'https://www.hdfc.bank.in/', login_url: 'https://now.hdfc.bank.in/retail-app/', permission: 'unverified',
    reviewed: false, limitations: ['Statement selectors and permission unverified.'], capabilities: {
      browser_login: 'unverified', official_login: true, automatic_statement_retrieval: false,
      scheduled_automation: 'unsupported', live_balance: false,
    } }],
};

describe('Bank browser connection', () => {
  beforeEach(() => { useAuth.getState().clear(); signIn(); });

  it('opens QR login without sending typed or saved credentials', async () => {
    const fetch = mockFetch({ '/accounts/a1/browser': { ...status, has_customer_id: true, has_password: true },
      '/accounts/a1/browser/start': { job_id: 'qr1', status: 'queued', created: true } });
    renderWithProviders(<BrowserBankConnection account={account} />);
    await screen.findByLabelText('Customer ID / User ID');
    await userEvent.type(screen.getByLabelText('Customer ID / User ID'), 'UNSAVED-CUSTOMER');
    await userEvent.click(screen.getByRole('button', { name: 'QR / bank app login' }));
    await waitFor(() => expect(fetch.mock.calls.some(([url, init]) => {
      if (!String(url).endsWith('/browser/start')) return false;
      const body = JSON.parse(String(init?.body));
      return body.qr_login === true && body.login_only === true && !body.authenticate && !body.customer_id && !body.password;
    })).toBe(true));
  });

  it('opens official login without claiming an unverified statement workflow works', async () => {
    const fetch = mockFetch({ '/accounts/a1/browser': status,
      '/accounts/a1/browser/start': { job_id: 'j1', status: 'queued', created: true } });
    renderWithProviders(<BrowserBankConnection account={account} />);
    await screen.findByLabelText('Customer ID / User ID');
    expect(screen.getByRole('button', { name: 'Connect and retrieve statements' })).toBeDisabled();
    expect(screen.getByRole('link', { name: 'Official bank login' })).toHaveAttribute('href', status.profiles[0].login_url);
    expect(screen.queryByLabelText(/^password$/i)).toBeNull();
    await userEvent.type(screen.getByLabelText('Customer ID / User ID'), 'CUSTOMER123');
    await userEvent.click(screen.getByRole('button', { name: 'Open bank login' }));
    await waitFor(() => expect(fetch.mock.calls.some(([u, init]) => String(u).endsWith('/browser/start')
      && JSON.parse(String(init?.body)).customer_id === 'CUSTOMER123'
      && JSON.parse(String(init?.body)).login_only === true)).toBe(true));
    await waitFor(() => expect(screen.getByLabelText('Customer ID / User ID')).toHaveValue(''));
  });

  it('queues statement retrieval only for a supported bank profile', async () => {
    const supported = structuredClone(status);
    supported.profiles[0].permission = 'permitted';
    supported.profiles[0].reviewed = true;
    supported.profiles[0].capabilities.automatic_statement_retrieval = true;
    const fetch = mockFetch({ '/accounts/a1/browser': supported,
      '/accounts/a1/browser/start': { job_id: 'j1', status: 'queued', created: true } });
    renderWithProviders(<BrowserBankConnection account={account} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Connect and retrieve statements' })).toBeEnabled());
    await userEvent.click(screen.getByRole('button', { name: 'Connect and retrieve statements' }));
    await waitFor(() => expect(fetch.mock.calls.some(([u, init]) => String(u).endsWith('/browser/start')
      && JSON.parse(String(init?.body)).login_only === false)).toBe(true));
  });

  it('keeps read-only users from opening sessions or changing profiles', async () => {
    signIn(['accounts:read'], 'viewer');
    mockFetch({ '/accounts/a1/browser': status });
    renderWithProviders(<BrowserBankConnection account={account} />);
    await screen.findByLabelText('Customer ID / User ID');
    expect(screen.getByRole('button', { name: 'Open bank login' })).toBeDisabled();
    expect(screen.queryByText('Configure another bank profile')).toBeNull();
  });

  it('saves credentials, clears password input, then authenticates without resending the secret', async () => {
    const current = structuredClone(status);
    current.profiles[0].capabilities.saved_login = true;
    current.has_password = false;
    const fetch = mockFetch({ '/accounts/a1/browser': () => current,
      '/accounts/a1/browser/credentials': () => {
        current.has_customer_id = true; current.has_password = true;
        return { saved: true, has_customer_id: true, has_password: true };
      }, '/accounts/a1/browser/start': { job_id: 'j1', status: 'queued', created: true } });
    renderWithProviders(<BrowserBankConnection account={account} />);
    await screen.findByLabelText('Bank password / IPIN');
    expect(screen.getByRole('button', { name: 'Authenticate' })).toBeDisabled();
    await userEvent.type(screen.getByLabelText('Customer ID / User ID'), 'SYNTHETIC-USER');
    await userEvent.type(screen.getByLabelText('Bank password / IPIN'), 'SYNTHETIC-PASSWORD');
    await userEvent.click(screen.getByRole('button', { name: 'Save bank login' }));
    await waitFor(() => expect(screen.getByLabelText('Bank password / IPIN')).toHaveValue(''));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Authenticate' })).toBeEnabled());
    await userEvent.click(screen.getByRole('button', { name: 'Authenticate' }));
    await waitFor(() => expect(fetch.mock.calls.some(([u, init]) => String(u).endsWith('/browser/start')
      && JSON.parse(String(init?.body)).authenticate === true)).toBe(true));
    const authenticate = fetch.mock.calls.find(([u]) => String(u).endsWith('/browser/start'));
    expect(authenticate?.[1]?.body).not.toContain('SYNTHETIC-PASSWORD');
    expect(JSON.stringify(window.localStorage)).not.toContain('SYNTHETIC-PASSWORD');
  });

  it('requires saving edited credentials before Authenticate becomes available', async () => {
    const current = structuredClone(status);
    current.profiles[0].capabilities.saved_login = true;
    current.has_password = true;
    mockFetch({ '/accounts/a1/browser': current });
    renderWithProviders(<BrowserBankConnection account={account} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Authenticate' })).toBeEnabled());
    await userEvent.type(screen.getByLabelText('Bank password / IPIN'), 'changed-password');
    expect(screen.getByRole('button', { name: 'Authenticate' })).toBeDisabled();
  });

  it('shows bank denial recovery without automatically retrying authentication', async () => {
    const current = structuredClone(status);
    current.job = { id: 'j1', status: 'awaiting_user', message: null, error: 'The bank declined this login session (ACCESS_DENIED).',
      error_code: 'bank_authorization_denied', requires_user_action: 'bank_access_denied', result: { data_retrieved: false } };
    const fetch = mockFetch({ '/accounts/a1/browser': current,
      '/accounts/a1/browser/start': { job_id: 'j1', status: 'queued', created: false } });
    renderWithProviders(<BrowserBankConnection account={account} />);
    await screen.findByText('Bank declined this session');
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
    await userEvent.click(screen.getByRole('button', { name: 'Open bank login' }));
    await waitFor(() => expect(fetch.mock.calls.some(([u, init]) => String(u).endsWith('/browser/start')
      && JSON.parse(String(init?.body)).login_only === true && !JSON.parse(String(init?.body)).authenticate)).toBe(true));
  });

  it('shows the configured visible verification wait without opening a bank automatically', async () => {
    const current = structuredClone(status);
    current.verification_wait_seconds = 60;
    const fetch = mockFetch({ '/accounts/a1/browser': current });
    renderWithProviders(<BrowserBankConnection account={account} />);
    await screen.findByText(/visible bank window waits up to 60 seconds/i);
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
  });
});
