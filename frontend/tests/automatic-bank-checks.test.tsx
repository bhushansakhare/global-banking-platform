import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { AutomaticBankChecks } from '@/features/accounts/AutomaticBankChecks';
import type { Account } from '@/types/api';
import { mockFetch, renderWithProviders, signIn } from './helpers';

describe('Automatic bank checks', () => {
  it('saves any chosen account time and enables scheduling without immediately logging in', async () => {
    signIn();
    const calls: { path: string; body: unknown }[] = [];
    const record = (path: string) => (_url: string, init?: RequestInit) => {
      calls.push({ path, body: JSON.parse(String(init?.body)) });
      return {};
    };
    mockFetch({ '/automation/master': record('/automation/master'),
      '/automation': { settings: { master_enabled: false, paused: false } },
      '/accounts/a1/automation/action': record('/accounts/a1/automation/action'),
      '/accounts/a1': record('/accounts/a1') });
    renderWithProviders(<AutomaticBankChecks account={{ id: 'a1', timezone: 'Asia/Kolkata', sync_schedule: null,
      automation_enabled: false, automation_paused: false } as Account} />);
    const input = screen.getByLabelText('Daily bank check time');
    expect(input).toHaveValue('');
    await userEvent.type(input, '14:37');
    await userEvent.click(await screen.findByRole('button', { name: 'Save & enable automatic checks' }));
    await waitFor(() => expect(calls).toHaveLength(3));
    expect(calls).toEqual([
      { path: '/accounts/a1', body: { sync_schedule: '37 14 * * *', timezone: 'Asia/Kolkata' } },
      { path: '/automation/master', body: { enabled: true } },
      { path: '/accounts/a1/automation/action', body: { action: 'enable' } },
    ]);
    expect(screen.getByText(/does not start a bank login immediately/i)).toBeInTheDocument();
  });

  it('shows the saved daily time and next run for an enabled account', async () => {
    signIn();
    mockFetch({ '/automation': { settings: { master_enabled: true, paused: false } } });
    renderWithProviders(<AutomaticBankChecks account={{ id: 'a2', timezone: 'Asia/Kolkata', sync_schedule: '5 18 * * *',
      automation_enabled: true, automation_paused: false,
      automation: { next_sync_at: '2026-11-20T12:35:00Z' } } as Account} />);
    expect(await screen.findByText('Schedule on')).toBeInTheDocument();
    expect(screen.getByLabelText('Daily bank check time')).toHaveValue('18:05');
    expect(screen.getByRole('button', { name: 'Pause this account' })).toBeInTheDocument();
    expect(screen.getByText('2. Approve QR / OTP if requested')).toBeInTheDocument();
  });
});
