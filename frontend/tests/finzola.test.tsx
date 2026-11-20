import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import Finzola from '@/pages/Finzola';
import { mockFetch, renderWithProviders, signIn } from './helpers';

const root = '/integrations/finzola';
const config = { configured: false, domains: ['expenses', 'finance'], polling_enabled: false, interval_minutes: 15,
  next_due: null, last_fetch: null, state: {} };
const pipeline = { mappings: [], items: [], jobs: [], total: 0 };
const outbound = { destinations: [], deliveries: [], total: 0 };

describe('Finzola workspace', () => {
  it('starts unconfigured without claiming a live connection or delivery', async () => {
    signIn();
    mockFetch({ [root+'/custom-api/pipeline']: pipeline, [root+'/custom-api']: config, [root+'/outbound']: outbound });
    renderWithProviders(<Finzola />);
    expect(await screen.findByText('Set up the complete flow')).toBeInTheDocument();
    expect(screen.getByText('Not configured')).toBeInTheDocument();
    expect(screen.getByText(/it never moves money or approves expenses/i)).toBeInTheDocument();
    expect(screen.queryByText('Verified key')).toBeNull();
  });

  it('saves a gateway key through the server and clears its input', async () => {
    signIn();
    let body: Record<string, unknown> | undefined;
    let saved = false;
    mockFetch({ [root+'/custom-api/pipeline']: pipeline, [root+'/custom-api/data']: { items: [], total: 0, state: null },
      [root+'/custom-api']: (_url, init) => { if (init?.method === 'PUT') { body = JSON.parse(String(init.body)); saved = true; } return { ...config, configured: saved }; },
      [root+'/outbound']: outbound });
    renderWithProviders(<Finzola />);
    await userEvent.click(await screen.findByRole('tab', { name: 'Import expenses' }));
    await userEvent.type(screen.getByLabelText('Finzola gateway key'), 'private-gateway-key');
    await userEvent.click(screen.getByRole('button', { name: 'Save gateway settings' }));
    await waitFor(() => expect(body?.api_key).toBe('private-gateway-key'));
    await waitFor(() => expect(screen.getByLabelText('Finzola gateway key')).toHaveValue(''));
    expect(localStorage.getItem('private-gateway-key')).toBeNull();
  });

  it('does not offer automatic delivery before destination verification', async () => {
    signIn();
    mockFetch({ [root+'/custom-api/pipeline']: pipeline, [root+'/custom-api']: config, [root+'/outbound']: {
      ...outbound, destinations: [{ id: 'd1', external_org_id: 'org-one', has_key: true, revision: 1,
        verified: false, verified_at: null, auto_delivery: false, last_error: null }],
      deliveries: [{ id: 'l1', request_id: 'request-one', source_kind: 'debit', status: 'queued', attempts: 0, created_at: null, payload: {}, acknowledgement: null }],
    }, '/accounts': [] });
    renderWithProviders(<Finzola />);
    await userEvent.click(await screen.findByRole('tab', { name: 'Send to Finzola' }));
    expect(screen.getByRole('button', { name: 'Enable automatic results' })).toBeDisabled();
    expect(screen.getByText('Test required')).toBeInTheDocument();
    expect(screen.getAllByText('queued').length).toBeGreaterThan(0);
    expect(screen.queryByText('Exact request ID acknowledged')).toBeNull();
  });

  it('keeps write controls unavailable to a read-only user', async () => {
    signIn(['settings:read', 'accounts:read']);
    mockFetch({ [root+'/custom-api/pipeline']: pipeline, [root+'/custom-api/data']: { items: [], total: 0, state: null }, [root+'/custom-api']: config, [root+'/outbound']: outbound });
    renderWithProviders(<Finzola />);
    await userEvent.click(await screen.findByRole('tab', { name: 'Import expenses' }));
    expect(screen.getByLabelText('Finzola gateway key')).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Save gateway settings' })).toBeNull();
  });
});
