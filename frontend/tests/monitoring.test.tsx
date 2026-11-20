import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { MonitoringControls } from '@/features/accounts/MonitoringControls';
import Accounts from '@/pages/Accounts';
import { BankObservation } from '@/features/accounts/BankObservation';
import { AccountMailSettings } from '@/features/accounts/AccountMailSettings';
import type { Account } from '@/types/api';
import { mockFetch, renderWithProviders, signIn } from './helpers';

const account = { id: 'a1', currency: 'INR', low_balance_threshold: '1000.00' } as Account;
const settings = { verification_wait_seconds: 600, email_enabled: false, recipients: [],
  capture_statement_screenshot: true, email_configured: false };

describe('Account monitoring', () => {
  it('shows confirmed email delivery without treating saved SMTP as a sent email', async () => {
    signIn();
    mockFetch({ '/accounts/a1/monitoring': { ...settings, email_configured: true,
      latest_email: { status: 'succeeded', sent: true, finished_at: '2026-11-20T07:00:00Z', error: null } } });
    renderWithProviders(<MonitoringControls account={account} editable={false} />);
    expect(await screen.findByText('Latest email sent')).toBeInTheDocument();
    expect(screen.getByText(/accepted by your mail server/i)).toBeInTheDocument();
  });
  it('preserves a saved SMTP password without exposing it in the form', async () => {
    signIn();
    let saved: Record<string, unknown> | undefined;
    mockFetch({ '/accounts/a1/mail-settings': (_url, init) => {
      if (init?.method === 'PUT') saved = JSON.parse(String(init.body));
      return { configured: true, host: 'smtp.example.com', port: 587, sender: 'sender@example.com',
        username: 'sender@example.com', security: 'starttls', password_saved: true };
    } });
    renderWithProviders(<AccountMailSettings accountId="a1" />);
    await userEvent.click(screen.getByText('Sender email / SMTP for this account'));
    expect(await screen.findByLabelText('SMTP app password')).toHaveValue('');
    await userEvent.click(screen.getByRole('button', { name: 'Save account sender' }));
    await waitFor(() => expect(saved?.host).toBe('smtp.example.com'));
    expect(saved).not.toHaveProperty('password');
    expect(saved?.sender).toBe('sender@example.com');
  });
  it('shows the observed bank balance below its account target', async () => {
    signIn();
    mockFetch({ '/accounts/a1/bank-observation': { available: true, amount: '1250.00', currency: 'INR',
      captured_at: '2026-11-20T06:20:00Z', target: '2000.00', below_target: true, screenshot_available: true } });
    renderWithProviders(<BankObservation accountId="a1" />);
    expect(await screen.findByText('Balance below target')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'View bank screenshot' })).toBeInTheDocument();
    expect(screen.queryByText('Target met')).toBeNull();
  });

  it('shows target met when the observed balance is high enough', async () => {
    signIn();
    mockFetch({ '/accounts/a1/bank-observation': { available: true, amount: '1250.00', currency: 'INR',
      captured_at: '2026-11-20T06:20:00Z', target: '1000.00', below_target: false, screenshot_available: false } });
    renderWithProviders(<BankObservation accountId="a1" />);
    expect(await screen.findByText('Target met')).toBeInTheDocument();
    expect(screen.queryByText('Balance below target')).toBeNull();
  });

  it('opens the target form directly from the accounts list', async () => {
    signIn();
    const listed = { ...account, nickname: 'HDFC savings', account_type: 'savings', country_code: 'IN',
      account_number_masked: '••••0127', institution: { id: 'i1', name: 'HDFC Bank', country_code: 'IN' },
      balances: {}, connection: null, automation: null, connection_health: 'unknown', last_successful_sync_at: null };
    mockFetch({ '/accounts/a1/mail-settings': { configured: false, host: '', port: 587, sender: '', username: '', security: 'starttls', password_saved: false }, '/accounts/a1/monitoring': settings, '/accounts': [listed] });
    renderWithProviders(<Accounts />, { route: '/accounts' });
    const buttons = await screen.findAllByRole('button', { name: 'Set balance target' });
    await userEvent.click(buttons[0]);
    expect(await screen.findByRole('dialog', { name: /balance target.*HDFC savings/i })).toBeInTheDocument();
    expect(await screen.findByLabelText(/minimum balance target/i)).toHaveValue(1000);
    expect(screen.getByRole('button', { name: /save monitoring settings/i })).toBeInTheDocument();
  });

  it('saves target, wait and account-specific recipients together', async () => {
    signIn();
    let saved: Record<string, unknown> | undefined;
    mockFetch({ '/accounts/a1/monitoring': (_url, init) => {
      if (init?.method === 'PUT') saved = JSON.parse(String(init.body));
      return settings;
    } });
    renderWithProviders(<MonitoringControls account={account} editable />);
    const target = await screen.findByLabelText(/minimum balance target/i);
    const user = userEvent.setup();
    await user.clear(target);
    await user.type(target, '5000');
    const wait = screen.getByLabelText(/verification wait/i);
    await user.clear(wait);
    await user.type(wait, '120');
    await user.type(screen.getByLabelText(/alert email recipients/i), 'first@example.com, second@example.com');
    await user.click(screen.getByRole('checkbox', { name: /email each new verified balance/i }));
    await user.click(screen.getByRole('button', { name: /save monitoring/i }));
    await waitFor(() => expect(saved).toMatchObject({ low_balance_threshold: '5000', verification_wait_seconds: 120,
      email_enabled: true, recipients: ['first@example.com', 'second@example.com'] }));
    expect(screen.getByText(/set up the sender email for this account/i)).toBeInTheDocument();
  });

  it('does not allow a read-only user to change settings', async () => {
    signIn(['accounts:read']);
    mockFetch({ '/accounts/a1/monitoring': settings });
    renderWithProviders(<MonitoringControls account={account} editable={false} />);
    expect(await screen.findByLabelText(/minimum balance target/i)).toBeDisabled();
    expect(screen.queryByRole('button', { name: /save monitoring/i })).toBeNull();
  });
});
