import clsx from 'clsx';
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight } from 'lucide-react';
import type { ReactNode } from 'react';
import { Button, Checkbox } from './primitives';
import { EmptyState, ErrorState, TableSkeleton } from './States';

export interface Column<T> {
  key: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  className?: string;
  align?: 'left' | 'right' | 'center';
  sortable?: boolean;
  /** shown on the mobile card; first column is used as the card title */
  mobile?: 'title' | 'meta' | 'hidden' | 'field';
}

interface Props<T> {
  columns: Column<T>[];
  rows: T[] | undefined;
  rowKey: (row: T) => string;
  loading?: boolean;
  error?: unknown;
  onRetry?: () => void;
  empty?: ReactNode;
  onRowClick?: (row: T) => void;
  sort?: { key: string; order: 'asc' | 'desc' };
  onSort?: (key: string) => void;
  selectable?: boolean;
  selected?: Set<string>;
  onSelect?: (next: Set<string>) => void;
  rowClassName?: (row: T) => string | undefined;
}

/**
 * Desktop: a dense, sticky-header table. Below md: each row becomes a card with labelled
 * fields so nothing is squeezed into unreadable columns.
 */
export function DataTable<T>({ columns, rows, rowKey, loading, error, onRetry, empty, onRowClick, sort, onSort,
  selectable, selected, onSelect, rowClassName }: Props<T>) {
  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  if (loading && !rows) return <TableSkeleton cols={Math.min(columns.length, 6)} />;
  if (!rows?.length) return <>{empty ?? <EmptyState title="Nothing to show yet" />}</>;

  const allSelected = !!selected && rows.every((r) => selected.has(rowKey(r)));
  const someSelected = !!selected && rows.some((r) => selected.has(rowKey(r)));
  const toggleAll = (v: boolean) => {
    const next = new Set(selected);
    rows.forEach((r) => (v ? next.add(rowKey(r)) : next.delete(rowKey(r))));
    onSelect?.(next);
  };
  const toggle = (id: string, v: boolean) => {
    const next = new Set(selected);
    if (v) next.add(id); else next.delete(id);
    onSelect?.(next);
  };
  const title = columns.find((c) => c.mobile === 'title') ?? columns[0];
  const meta = columns.filter((c) => c.mobile === 'meta');
  const fields = columns.filter((c) => c !== title && c.mobile !== 'meta' && c.mobile !== 'hidden');

  return (
    <>
      <div className={clsx('hidden md:block overflow-x-auto', loading && 'opacity-60 transition-opacity')}>
        <table className="w-full text-sm">
          <thead className="sticky top-0 z-[1] bg-surface-2/95 backdrop-blur">
            <tr className="border-b border-line">
              {selectable && (
                <th className="w-10 pl-4 py-2.5">
                  <Checkbox label="Select all rows" checked={allSelected} indeterminate={someSelected} onChange={toggleAll} />
                </th>
              )}
              {columns.map((c) => {
                const active = sort?.key === c.key;
                return (
                  <th key={c.key} scope="col" aria-sort={active ? (sort!.order === 'asc' ? 'ascending' : 'descending') : undefined}
                    className={clsx('px-4 py-2.5 text-[11.5px] font-semibold uppercase tracking-wide text-muted whitespace-nowrap',
                      c.align === 'right' ? 'text-right' : c.align === 'center' ? 'text-center' : 'text-left', c.className)}>
                    {c.sortable && onSort ? (
                      <button className="inline-flex items-center gap-1 hover:text-ink" onClick={() => onSort(c.key)}>
                        {c.header}
                        {active && (sort!.order === 'asc' ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
                      </button>
                    ) : c.header}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((r) => {
              const id = rowKey(r);
              return (
                <tr key={id} onClick={onRowClick ? () => onRowClick(r) : undefined}
                  className={clsx('transition-colors', onRowClick && 'cursor-pointer hover:bg-surface-2/70',
                    selected?.has(id) && 'bg-brand-50/60 dark:bg-brand-900/20', rowClassName?.(r))}>
                  {selectable && (
                    <td className="pl-4 py-2.5" onClick={(e) => e.stopPropagation()}>
                      <Checkbox label="Select row" checked={!!selected?.has(id)} onChange={(v) => toggle(id, v)} />
                    </td>
                  )}
                  {columns.map((c) => (
                    <td key={c.key} className={clsx('px-4 py-2.5 align-middle',
                      c.align === 'right' ? 'text-right' : c.align === 'center' ? 'text-center' : 'text-left', c.className)}>
                      {c.cell(r)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <ul className="md:hidden divide-y divide-line">
        {rows.map((r) => {
          const id = rowKey(r);
          return (
            <li key={id} className={clsx('px-4 py-3.5', onRowClick && 'active:bg-surface-2', rowClassName?.(r))}
              onClick={onRowClick ? () => onRowClick(r) : undefined}>
              <div className="flex items-start gap-3">
                {selectable && (
                  <div className="pt-0.5" onClick={(e) => e.stopPropagation()}>
                    <Checkbox label="Select row" checked={!!selected?.has(id)} onChange={(v) => toggle(id, v)} />
                  </div>
                )}
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 font-medium text-ink">{title.cell(r)}</div>
                    {meta.length > 0 && <div className="shrink-0 text-right">{meta.map((m) => <div key={m.key}>{m.cell(r)}</div>)}</div>}
                  </div>
                  {fields.length > 0 && (
                    <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1.5">
                      {fields.map((f) => (
                        <div key={f.key} className="min-w-0">
                          <dt className="text-[11px] uppercase tracking-wide text-muted">{f.header}</dt>
                          <dd className="text-[13px] text-ink truncate">{f.cell(r)}</dd>
                        </div>
                      ))}
                    </dl>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );
}

export function Pagination({ page, pages, total, onPage, pageSize }: { page: number; pages: number; total: number;
  onPage: (p: number) => void; pageSize: number }) {
  if (total === 0) return null;
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <div className="flex items-center justify-between gap-3 border-t border-line px-4 py-3 text-[13px] text-muted">
      <span className="num">{from.toLocaleString('en-GB')}–{to.toLocaleString('en-GB')} of {total.toLocaleString('en-GB')}</span>
      <div className="flex items-center gap-1">
        <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page"
          icon={<ChevronLeft className="h-4 w-4" />} />
        <span className="px-2 num">Page {page} / {Math.max(1, pages)}</span>
        <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)} aria-label="Next page"
          icon={<ChevronRight className="h-4 w-4" />} />
      </div>
    </div>
  );
}
