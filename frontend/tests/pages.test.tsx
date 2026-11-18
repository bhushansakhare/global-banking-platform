import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { AppLayout } from '@/layouts/AppLayout';
import Dashboard from '@/pages/Dashboard';
import Login from '@/pages/Login';
import { useAuth } from '@/stores/auth';
import type { Summary } from '@/types/api';
import { makeUser, mockFetch, renderWithProviders, signIn } from './helpers';

const summary: Summary = {
  reporting_currency: 'GBP', demo_accounts: 2, live_accounts: 0, connected_banks: 1, total_accounts: 2,
  balances: {
    available: { accounts: 2, by_currency: { GBP: 1000 }, total_reporting: 1000, unconverted_currencies: [], oldest_as_of: null },
    ledger: { accounts: 2, by_currency: { GBP: 1200 }, total_reporting: 1200, unconverted_currencies: [], oldest_as_of: null },
    current: { accounts: 0, by_currency: {}, total_reporting: null, unconverted_currencies: [], oldest_as_of: null },
  },
  reported_balance: { total_reporting: 123456.78, accounts: 2, unconverted_currencies: [] },
  credits_30d: 5000, debits_30d: 2500, flows_unconverted_currencies: [], transactions_30d: 42, transactions_today: 3,
  last_activity_date: null, last_activity_count: 0,
  reconciliation: { by_status: { matched: 30, exception: 4 }, unmatched: 4, suggested: 2, sessions_balanced: 1, sessions_with_exceptions: 1 },
  automation: { master_enabled: true, paused: false, next_run_at: '2026-11-17T06:00:00Z', last_run_at: null, jobs_24h: { succeeded: 5 } },
  failed_connections: [], pending_statements: 1, fx_rates_used: [], fx_missing: ['INR'],
};

describe('Login page', () => {
  beforeEach(() => useAuth.getState().clear());

  it('validates fields before calling the API', async () => {
    const f = mockFetch({});
    renderWithProviders(<Login />);
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }));
    expect(await screen.findByText('Enter a valid email address')).toBeInTheDocument();
    expect(screen.getByText('Enter your password')).toBeInTheDocument();
    expect(f).not.toHaveBeenCalled();
  });

  it('signs in and stores the session in memory', async () => {
    mockFetch({ '/auth/login': { access_token: 'tok', csrf_token: 'c', expires_in: 900, user: makeUser() } });
    renderWithProviders(<Routes><Route path="/login" element={<Login />} /><Route path="/" element={<p>Home</p>} /></Routes>, { route: '/login' });
    await userEvent.type(screen.getByLabelText('Email address'), 'owner@example.com');
    await userEvent.type(screen.getByLabelText('Password'), 'Secret12345678');
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }));
    expect(await screen.findByText('Home')).toBeInTheDocument();
    expect(useAuth.getState().accessToken).toBe('tok');
    expect(localStorage.getItem('tok')).toBeNull();
  });

  it('shows server errors', async () => {
    mockFetch({ '/auth/login': () => new Response(JSON.stringify({ error: { code: 'invalid_credentials', message: 'Invalid email or password', details: null, request_id: null } }), { status: 401 }) });
    renderWithProviders(<Login />);
    await userEvent.type(screen.getByLabelText('Email address'), 'a@example.com');
    await userEvent.type(screen.getByLabelText('Password'), 'x');
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }));
    expect(await screen.findByText('Invalid email or password')).toBeInTheDocument();
  });
});

describe('Navigation', () => {
  it('shows all 13 sections to an owner', async () => {
    signIn();
    mockFetch({ '/notifications/unread-count': { unread: 3 } });
    renderWithProviders(<Routes><Route element={<AppLayout />}><Route index element={<p>x</p>} /></Route></Routes>);
    const nav = screen.getAllByRole('navigation', { name: 'Main navigation' })[0];
    for (const label of ['Dashboard', 'Bank Accounts', 'Institutions', 'Automation Center', 'Statements', 'Transactions', 'Balances',
      'Reconciliation', 'Analytics', 'Reports', 'Notifications', 'Audit Logs', 'Settings']) {
      expect(within(nav).getByRole('link', { name: new RegExp(label) })).toBeInTheDocument();
    }
    expect(await within(nav).findByText('3')).toBeInTheDocument();
  });

  it('hides sections a viewer cannot access', () => {
    signIn(['dashboard:read', 'accounts:read', 'transactions:read', 'notifications:read'], 'viewer');
    mockFetch({ '/notifications/unread-count': { unread: 0 } });
    renderWithProviders(<Routes><Route element={<AppLayout />}><Route index element={<p>x</p>} /></Route></Routes>);
    const nav = screen.getAllByRole('navigation', { name: 'Main navigation' })[0];
    expect(within(nav).queryByRole('link', { name: /Audit Logs/ })).toBeNull();
    expect(within(nav).queryByRole('link', { name: /Settings/ })).toBeNull();
    expect(within(nav).getByRole('link', { name: /Transactions/ })).toBeInTheDocument();
  });
});

describe('Dashboard', () => {
  it('renders consolidated figures, demo labelling and missing-rate warning', async () => {
    signIn();
    mockFetch({
      '/analytics/summary': summary,
      '/analytics/balance-history': { series: [], reporting_currency: 'GBP' },
      '/analytics/cash-flow': { series: [], reporting_currency: 'GBP' },
      '/analytics/monthly': { series: [], reporting_currency: 'GBP' },
      '/analytics/distributions': { reporting_currency: 'GBP', banks: [], currencies: [], categories: [], fx_missing: [] },
      '/analytics/reconciliation-performance': { series: [] },
    });
    renderWithProviders(<Dashboard />);
    expect(await screen.findByText('£123,456.78')).toBeInTheDocument();
    expect(screen.getByText('Demo data present')).toBeInTheDocument();
    expect(screen.getByText(/No rate to GBP for INR/)).toBeInTheDocument();
    expect(screen.getByText('Unmatched transactions')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('On')).toBeInTheDocument());
  });

  it('shows onboarding when there are no accounts', async () => {
    signIn();
    mockFetch({ '/analytics': () => ({ ...summary, total_accounts: 0, demo_accounts: 0 }) });
    renderWithProviders(<Dashboard />);
    expect(await screen.findByText('No bank accounts yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Load demo environment/ })).toBeInTheDocument();
  });
});
