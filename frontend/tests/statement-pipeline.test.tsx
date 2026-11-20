import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { StatementAccess, StatementPipeline } from '@/features/statements/StatementAccess';
import { UploadStatementModal } from '@/features/statements/UploadStatementModal';
import type { Statement } from '@/types/api';
import { mockFetch, renderWithProviders, signIn } from './helpers';

const locked = { id: 's1', account_id: 'a1', password_required: true, file: { format: 'pdf' },
  transaction_count: null, processing_status: 'needs_review', arithmetic_valid: null,
  document_access: { password_saved: false, password_required: true, preview_ready: false, encrypted: true } } as Statement;

describe('Statement processing', () => {
  it('sends a PDF password only to this statement unlock endpoint and clears the field', async () => {
    signIn();
    let body: unknown;
    mockFetch({ '/statements/s1': { ...locked, document_access: { password_saved: true, preview_ready: true } },
      '/statements/s1/unlock': (_url, init) => { body = JSON.parse(String(init?.body)); return { statements_queued: 2 }; } });
    renderWithProviders(<StatementAccess statement={locked} editable />);
    await userEvent.type(screen.getByLabelText('Statement PDF password'), 'synthetic-secret');
    await userEvent.click(screen.getByRole('button', { name: 'Unlock & save for this account' }));
    await waitFor(() => expect(body).toEqual({ password: 'synthetic-secret' }));
    await waitFor(() => expect(screen.getByLabelText('Statement PDF password')).toHaveValue(''));
    expect(screen.getByText(/2 other waiting statement/)).toBeInTheDocument();
    expect(screen.queryByText('synthetic-secret')).toBeNull();
  });

  it('does not mark locked extraction or reconciliation complete', () => {
    signIn();
    renderWithProviders(<StatementPipeline statement={locked} />);
    expect(screen.getByText('Enter password below')).toBeInTheDocument();
    expect(screen.getByText('Starts after validation')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'View reconciliation result' })).toBeNull();
  });

  it('does not show success when a saved password cannot be read back', async () => {
    signIn();
    mockFetch({ '/statements/s1/unlock': { statements_queued: 0 }, '/statements/s1': locked });
    renderWithProviders(<StatementAccess statement={locked} editable />);
    await userEvent.type(screen.getByLabelText('Statement PDF password'), 'synthetic-secret');
    await userEvent.click(screen.getByRole('button', { name: 'Unlock & save for this account' }));
    expect(await screen.findByText(/Saved PDF access could not be confirmed/)).toBeInTheDocument();
    expect(screen.getByLabelText('Statement PDF password')).toHaveValue('synthetic-secret');
  });

  it('shows a real result while explaining that no cash-book matching has happened', () => {
    signIn();
    renderWithProviders(<StatementPipeline statement={{ ...locked, transaction_count: 4, arithmetic_valid: true,
      processing_status: 'processed', latest_reconciliation: { id: 'r1', status: 'balanced', exception_count: 0, has_book_side: false } }} />);
    expect(screen.getByRole('link', { name: 'View reconciliation result' })).toHaveAttribute('href', '/reconciliation/r1');
    expect(screen.getByText('Statement integrity checked')).toBeInTheDocument();
    expect(screen.queryByText('Bank and cash-book checked')).toBeNull();
  });

  it('processes multiple uploads independently and keeps a password-needed result visible', async () => {
    signIn();
    const names: string[] = [];
    mockFetch({ '/statements/upload': (_url, init) => {
      const form = init?.body as FormData;
      names.push((form.get('file') as File).name);
      expect(form.get('account_id')).toBe('a1');
      return { duplicate: false, statement: { ...locked, id: `s${names.length}` } };
    } });
    renderWithProviders(<UploadStatementModal open onClose={() => undefined} accountId="a1" />);
    await userEvent.upload(screen.getByLabelText('Statement files'), [new File(['%PDF-1'], 'one.pdf', { type: 'application/pdf' }), new File(['%PDF-2'], 'two.pdf', { type: 'application/pdf' })]);
    await userEvent.click(screen.getByRole('button', { name: /Upload & process 2/ }));
    expect(await screen.findByText('One password for this account')).toBeInTheDocument();
    expect(names).toEqual(['one.pdf', 'two.pdf']);
    expect(screen.getAllByRole('link', { name: 'Open statement' })).toHaveLength(2);
  });
});
