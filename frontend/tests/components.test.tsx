import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { DataTable, Pagination, type Column } from '@/components/ui/DataTable';
import { ConfirmDialog, Modal } from '@/components/ui/Modal';
import { DemoBadge, StatusBadge, Toggle } from '@/components/ui/primitives';
import { Money } from '@/components/Money';

type Row = { id: string; name: string; amount: string };
const rows: Row[] = [{ id: '1', name: 'Alpha', amount: '10' }, { id: '2', name: 'Beta', amount: '-5' }];
const cols: Column<Row>[] = [
  { key: 'name', header: 'Name', cell: (r) => r.name, sortable: true },
  { key: 'amount', header: 'Amount', cell: (r) => <Money value={r.amount} currency="GBP" signed />, mobile: 'meta' },
];

describe('DataTable', () => {
  it('renders a desktop table and a mobile card list (responsive)', () => {
    const { container } = render(<DataTable columns={cols} rows={rows} rowKey={(r) => r.id} />);
    const table = container.querySelector('table')!;
    expect(table.parentElement!.className).toContain('hidden md:block');
    const list = container.querySelector('ul')!;
    expect(list.className).toContain('md:hidden');
    expect(screen.getAllByText('Alpha')).toHaveLength(2);
  });

  it('shows the empty state and error state', () => {
    const { rerender } = render(<DataTable columns={cols} rows={[]} rowKey={(r) => r.id} empty={<p>Nothing here</p>} />);
    expect(screen.getByText('Nothing here')).toBeInTheDocument();
    rerender(<DataTable columns={cols} rows={undefined} rowKey={(r) => r.id} error={new Error('x')} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Unable to load');
  });

  it('supports sorting and selection', async () => {
    const onSort = vi.fn();
    const onSelect = vi.fn();
    render(<DataTable columns={cols} rows={rows} rowKey={(r) => r.id} onSort={onSort} sort={{ key: 'name', order: 'asc' }}
      selectable selected={new Set()} onSelect={onSelect} />);
    await userEvent.click(screen.getByRole('button', { name: /name/i }));
    expect(onSort).toHaveBeenCalledWith('name');
    await userEvent.click(screen.getByLabelText('Select all rows'));
    expect([...onSelect.mock.calls[0][0]]).toEqual(['1', '2']);
  });

  it('paginates', async () => {
    const onPage = vi.fn();
    render(<Pagination page={1} pages={3} total={120} pageSize={50} onPage={onPage} />);
    expect(screen.getByText('1–50 of 120')).toBeInTheDocument();
    expect(screen.getByLabelText('Previous page')).toBeDisabled();
    await userEvent.click(screen.getByLabelText('Next page'));
    expect(onPage).toHaveBeenCalledWith(2);
  });
});

describe('Modal & dialogs', () => {
  it('closes on Escape and is labelled', () => {
    const onClose = vi.fn();
    render(<Modal open onClose={onClose} title="Edit"><input aria-label="field" /></Modal>);
    expect(screen.getByRole('dialog', { name: 'Edit' })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('confirm dialog calls onConfirm', async () => {
    const onConfirm = vi.fn();
    render(<ConfirmDialog open onClose={() => {}} onConfirm={onConfirm} title="Remove?" body="Sure" confirmLabel="Remove" destructive />);
    await userEvent.click(screen.getByRole('button', { name: 'Remove' }));
    expect(onConfirm).toHaveBeenCalled();
  });
});

describe('primitives', () => {
  it('toggle is an accessible switch', async () => {
    const onChange = vi.fn();
    render(<Toggle label="Automation" checked={false} onChange={onChange} />);
    const sw = screen.getByRole('switch', { name: 'Automation' });
    expect(sw).toHaveAttribute('aria-checked', 'false');
    await userEvent.click(sw);
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it('labels statuses and demo data', () => {
    render(<><StatusBadge status="retry_scheduled" /><DemoBadge /></>);
    expect(screen.getByText('Retry scheduled')).toBeInTheDocument();
    expect(screen.getByText('DEMO')).toBeInTheDocument();
  });

  it('formats signed money with credit styling', () => {
    render(<Money value="25" currency="GBP" signed />);
    expect(screen.getByText('+£25.00').className).toContain('text-olive-700');
  });
});
