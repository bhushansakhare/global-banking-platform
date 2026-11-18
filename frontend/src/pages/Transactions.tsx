import { useQuery } from '@tanstack/react-query';
import { ArrowLeftRight, Download, FileSpreadsheet, Filter, Search, Tags, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Badge, Button, Card, DemoBadge, Field, Input, Select, StatusBadge } from '@/components/ui/primitives';
import { DataTable, Pagination, type Column } from '@/components/ui/DataTable';
import { Modal } from '@/components/ui/Modal';
import { KeyValue, PageHeader } from '@/components/ui/layout';
import { EmptyState, TableSkeleton } from '@/components/ui/States';
import { DebitCredit, Money } from '@/components/Money';
import { useDebounce } from '@/hooks/useDebounce';
import { useAction } from '@/hooks/useMutationToast';
import { accountsApi, transactionsApi } from '@/services/api';
import { download } from '@/services/http';
import { useAuth } from '@/stores/auth';
import type { Transaction } from '@/types/api';
import { date } from '@/utils/format';

const FILTER_KEYS = ['account_id', 'date_from', 'date_to', 'direction', 'status', 'category_id', 'min_amount', 'max_amount', 'duplicate_suspect'];

function TxDetail({ id, onClose }: { id: string | null; onClose: () => void }) {
  const q = useQuery({ queryKey: ['tx', id], queryFn: () => transactionsApi.get(id!), enabled: !!id });
  const t = q.data;
  return (
    <Modal open={!!id} onClose={onClose} size="lg" title="Transaction" description="Bank-reported values are immutable; workflow fields (category, reconciliation) are tracked separately.">
      {!t ? <TableSkeleton rows={4} /> : (
        <div className="space-y-5">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0"><p className="text-base font-semibold text-ink break-words">{t.description}</p>
              <p className="text-[13px] text-muted">{t.bank_name} · {t.account_name}</p></div>
            <DebitCredit debit={t.debit} credit={t.credit} currency={t.currency} />
          </div>
          <KeyValue cols={3} items={[
            { label: 'Booking date', value: date(t.booking_date) }, { label: 'Value date', value: date(t.value_date) },
            { label: 'Reference', value: t.reference ?? '—' }, { label: 'Running balance', value: <Money value={t.running_balance} currency={t.currency} /> },
            { label: 'Category', value: t.category?.name ?? 'Uncategorised' }, { label: 'Reconciliation', value: <StatusBadge status={t.reconciliation_status} /> },
            { label: 'Source', value: t.source }, { label: 'Provider ID', value: t.provider_transaction_id ?? '—' },
            { label: 'Duplicate suspect', value: t.duplicate_suspect ? 'Yes — review' : 'No' },
          ]} />
          <div><p className="label">Original extracted values</p>
            <pre className="rounded-xl bg-surface-2 p-3 text-[12px] font-mono overflow-x-auto">{JSON.stringify(t.raw_data, null, 2)}</pre></div>
          <p className="text-[11px] text-muted font-mono break-all">Fingerprint {t.fingerprint}</p>
        </div>
      )}
    </Modal>
  );
}

export default function Transactions() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get('q') ?? '');
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<{ key: string; order: 'asc' | 'desc' }>({ key: 'date', order: 'desc' });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [showFilters, setShowFilters] = useState(false);
  const [catModal, setCatModal] = useState(false);
  const [catId, setCatId] = useState('');
  const [detail, setDetail] = useState<string | null>(null);
  const can = useAuth((s) => s.can);
  const dq = useDebounce(q);
  const filters = useMemo(() => Object.fromEntries(FILTER_KEYS.map((k) => [k, params.get(k) ?? undefined])), [params]);
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: () => accountsApi.list() });
  const cats = useQuery({ queryKey: ['categories'], queryFn: transactionsApi.categories });
  const list = useQuery({ queryKey: ['transactions', filters, dq, page, sort], placeholderData: (p) => p,
    queryFn: () => transactionsApi.list({ ...filters, q: dq, page, page_size: 50, sort: sort.key, order: sort.order }) });
  const categorise = useAction(() => transactionsApi.categorise([...selected], catId || null), {
    success: (r) => `${r.updated} transaction(s) categorised`, invalidate: [['transactions']],
    onSuccess: () => { setCatModal(false); setSelected(new Set()); } });

  const setF = (k: string, v: string) => { if (v) params.set(k, v); else params.delete(k); setParams(params); setPage(1); };
  const activeCount = FILTER_KEYS.filter((k) => params.get(k)).length;
  const exportQuery = (fmt: 'csv' | 'xlsx', onlySelected: boolean) => ({ fmt, ...filters, q: dq,
    ids: onlySelected ? [...selected].join(',') : undefined });

  const cols: Column<Transaction>[] = [
    { key: 'date', header: 'Date', sortable: true, cell: (t) => <div className="whitespace-nowrap text-[13px]">{date(t.booking_date)}
      {t.value_date && t.value_date !== t.booking_date && <div className="text-[11px] text-muted">Value {date(t.value_date, { day: '2-digit', month: 'short' })}</div>}</div> },
    { key: 'description', header: 'Description', sortable: true, mobile: 'title', cell: (t) => (
      <div className="min-w-0 max-w-[380px]"><div className="flex items-center gap-2"><span className="truncate text-ink">{t.description}</span>
        <DemoBadge show={t.is_demo} />{t.duplicate_suspect && <Badge tone="warning">Possible duplicate</Badge>}</div>
        <div className="text-[12px] text-muted truncate">{t.bank_name} · {t.account_name}{t.reference ? ` · ${t.reference}` : ''}</div></div>) },
    { key: 'amount', header: 'Amount', sortable: true, align: 'right', mobile: 'meta', cell: (t) => <DebitCredit debit={t.debit} credit={t.credit} currency={t.currency} /> },
    { key: 'balance', header: 'Balance', align: 'right', cell: (t) => <Money value={t.running_balance} currency={t.currency} className="text-muted" /> },
    { key: 'category', header: 'Category', cell: (t) => t.category ? <span className="inline-flex items-center gap-1.5 text-[13px]">
      <span className="h-2 w-2 rounded-full" style={{ background: t.category.colour }} />{t.category.name}</span> : <span className="text-muted text-[13px]">—</span> },
    { key: 'recon', header: 'Reconciliation', cell: (t) => <StatusBadge status={t.reconciliation_status} /> },
  ];

  const data = list.data;
  return (
    <>
      <PageHeader eyebrow="Data" title="Transactions" subtitle="All bank-reported transactions across accounts. Search, filter, categorise and export."
        actions={<>
          <Button variant="outline" icon={<Download className="h-4 w-4" />} onClick={() => download('/transactions/export', exportQuery('csv', false), 'transactions.csv')}>CSV</Button>
          <Button variant="outline" icon={<FileSpreadsheet className="h-4 w-4" />} onClick={() => download('/transactions/export', exportQuery('xlsx', false), 'transactions.xlsx')}>Excel</Button>
        </>} />
      <Card padded={false}>
        <div className="flex flex-col gap-3 p-4 border-b border-line">
          <div className="flex flex-col sm:flex-row gap-3">
            <div className="relative flex-1"><Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted" />
              <Input aria-label="Search transactions" placeholder="Search description or reference…" className="pl-9" value={q}
                onChange={(e) => { setQ(e.target.value); setPage(1); }} /></div>
            <Select aria-label="Account" className="sm:w-64" value={filters.account_id ?? ''} onChange={(e) => setF('account_id', e.target.value)}>
              <option value="">All accounts</option>{accounts.data?.map((a) => <option key={a.id} value={a.id}>{a.nickname}</option>)}</Select>
            <Button variant={activeCount ? 'secondary' : 'outline'} icon={<Filter className="h-4 w-4" />} onClick={() => setShowFilters((v) => !v)}>
              Filters{activeCount ? ` (${activeCount})` : ''}</Button>
          </div>
          {showFilters && (
            <div className="grid grid-cols-1 xs:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7 gap-3">
              <Field label="From" htmlFor="f-from"><Input id="f-from" type="date" value={filters.date_from ?? ''} onChange={(e) => setF('date_from', e.target.value)} /></Field>
              <Field label="To" htmlFor="f-to"><Input id="f-to" type="date" value={filters.date_to ?? ''} onChange={(e) => setF('date_to', e.target.value)} /></Field>
              <Field label="Direction" htmlFor="f-dir"><Select id="f-dir" value={filters.direction ?? ''} onChange={(e) => setF('direction', e.target.value)}>
                <option value="">Both</option><option value="credit">Credits</option><option value="debit">Debits</option></Select></Field>
              <Field label="Reconciliation" htmlFor="f-st"><Select id="f-st" value={filters.status ?? ''} onChange={(e) => setF('status', e.target.value)}>
                <option value="">Any</option>{['unreconciled', 'matched', 'suggested', 'exception', 'manual'].map((s) => <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>)}</Select></Field>
              <Field label="Category" htmlFor="f-cat"><Select id="f-cat" value={filters.category_id ?? ''} onChange={(e) => setF('category_id', e.target.value)}>
                <option value="">Any</option>{cats.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select></Field>
              <Field label="Min amount" htmlFor="f-min"><Input id="f-min" inputMode="decimal" value={filters.min_amount ?? ''} onChange={(e) => setF('min_amount', e.target.value.replace(/[^\d.]/g, ''))} /></Field>
              <Field label="Max amount" htmlFor="f-max"><Input id="f-max" inputMode="decimal" value={filters.max_amount ?? ''} onChange={(e) => setF('max_amount', e.target.value.replace(/[^\d.]/g, ''))} /></Field>
              {activeCount > 0 && <div className="flex items-end"><Button variant="ghost" icon={<X className="h-4 w-4" />}
                onClick={() => { FILTER_KEYS.forEach((k) => params.delete(k)); setParams(params); }}>Clear filters</Button></div>}
            </div>
          )}
          {data && (
            <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-[13px] text-muted">
              <span><span className="num text-ink font-medium">{data.total.toLocaleString('en-GB')}</span> transactions</span>
              {!data.totals.mixed_currencies && data.items[0] ? <>
                <span>Credits <Money value={data.totals.credits} currency={data.items[0].currency} className="text-ink" /></span>
                <span>Debits <Money value={data.totals.debits} currency={data.items[0].currency} className="text-ink" /></span>
              </> : data.totals.mixed_currencies && <span>Totals hidden — multiple currencies in view</span>}
            </div>
          )}
        </div>
        {selected.size > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-b border-line bg-brand-50/60 dark:bg-brand-900/20 px-4 py-2.5 text-sm">
            <span className="font-medium text-ink">{selected.size} selected</span>
            {can('transactions:write') && <Button size="sm" variant="outline" icon={<Tags className="h-3.5 w-3.5" />} onClick={() => setCatModal(true)}>Categorise</Button>}
            <Button size="sm" variant="outline" icon={<Download className="h-3.5 w-3.5" />} onClick={() => download('/transactions/export', exportQuery('csv', true), 'transactions.csv')}>Export selected</Button>
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>Clear</Button>
          </div>
        )}
        <DataTable columns={cols} rows={data?.items} rowKey={(t) => t.id} loading={list.isFetching} error={list.error}
          sort={sort} onSort={(k) => setSort((s) => ({ key: k, order: s.key === k && s.order === 'desc' ? 'asc' : 'desc' }))}
          selectable selected={selected} onSelect={setSelected} onRowClick={(t) => setDetail(t.id)}
          empty={<EmptyState icon={<ArrowLeftRight className="h-6 w-6" />} title="No transactions found"
            body={activeCount || dq ? 'Try removing some filters.' : 'Transactions appear after the first collection or statement upload.'} />} />
        {data && <Pagination page={page} pages={data.pages} total={data.total} pageSize={50} onPage={setPage} />}
      </Card>
      <Modal open={catModal} onClose={() => setCatModal(false)} size="sm" title={`Categorise ${selected.size} transaction(s)`}
        footer={<><Button variant="outline" onClick={() => setCatModal(false)}>Cancel</Button><Button loading={categorise.isPending} onClick={() => categorise.mutate(undefined)}>Apply</Button></>}>
        <Field label="Category" htmlFor="bulk-cat"><Select id="bulk-cat" value={catId} onChange={(e) => setCatId(e.target.value)}>
          <option value="">Uncategorised</option>{cats.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select></Field>
      </Modal>
      <TxDetail id={detail} onClose={() => setDetail(null)} />
    </>
  );
}
