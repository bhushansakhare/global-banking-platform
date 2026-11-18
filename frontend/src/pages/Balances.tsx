import { useQuery } from '@tanstack/react-query';
import { Building2, Plus } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { Button, Card, CardHeader, DemoBadge, Field, Input } from '@/components/ui/primitives';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { Modal } from '@/components/ui/Modal';
import { Notice, PageHeader } from '@/components/ui/layout';
import { EmptyState } from '@/components/ui/States';
import { Money } from '@/components/Money';
import { errorText, useAction } from '@/hooks/useMutationToast';
import { balancesApi, type AccountBalances } from '@/services/api';
import { useAuth } from '@/stores/auth';
import { date, dateTime, isoDate, relative } from '@/utils/format';
import { BALANCE_LABELS } from '@/utils/status';

const TYPES = ['available', 'ledger', 'current', 'statement_closing', 'statement_opening'] as const;

function Cell({ row, type }: { row: AccountBalances; type: string }) {
  const b = row.balances[type];
  if (!b) return <span className="text-muted text-[13px]">Not reported</span>;
  return (
    <div className="text-right">
      <Money value={b.amount} currency={b.currency} className="font-medium text-ink" />
      <div className="text-[11px] text-muted" title={dateTime(b.as_of)}>{type.startsWith('statement') ? date(b.as_of) : relative(b.as_of)} · {b.source}</div>
    </div>
  );
}

function AddRate({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [f, setF] = useState({ base_currency: 'USD', quote_currency: 'GBP', rate: '', rate_date: isoDate(new Date()), source: 'manual' });
  const add = useAction(() => balancesApi.addRate(f), { success: 'Exchange rate recorded', invalidate: [['rates'], ['analytics']], onSuccess: onClose });
  const submit = (e: FormEvent) => { e.preventDefault(); add.mutate(undefined); };
  return (
    <Modal open={open} onClose={onClose} title="Record exchange rate" description="1 unit of the base currency = rate × quote currency. Rates are dated and never applied silently."
      footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button type="submit" form="rate" loading={add.isPending}
        disabled={!(Number(f.rate) > 0)}>Save rate</Button></>}>
      <form id="rate" onSubmit={submit} className="grid grid-cols-1 xs:grid-cols-2 gap-4">
        {add.error && <div className="xs:col-span-2"><Notice tone="danger">{errorText(add.error)}</Notice></div>}
        <Field label="Base currency" htmlFor="r-b"><Input id="r-b" maxLength={3} value={f.base_currency} onChange={(e) => setF({ ...f, base_currency: e.target.value.toUpperCase() })} /></Field>
        <Field label="Quote currency" htmlFor="r-q"><Input id="r-q" maxLength={3} value={f.quote_currency} onChange={(e) => setF({ ...f, quote_currency: e.target.value.toUpperCase() })} /></Field>
        <Field label="Rate" htmlFor="r-r"><Input id="r-r" inputMode="decimal" value={f.rate} onChange={(e) => setF({ ...f, rate: e.target.value.replace(/[^\d.]/g, '') })} /></Field>
        <Field label="Rate date" htmlFor="r-d"><Input id="r-d" type="date" value={f.rate_date} onChange={(e) => setF({ ...f, rate_date: e.target.value })} /></Field>
        <div className="xs:col-span-2"><Field label="Source" htmlFor="r-s"><Input id="r-s" value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })} /></Field></div>
      </form>
    </Modal>
  );
}

export default function Balances() {
  const can = useAuth((s) => s.can);
  const [rateModal, setRateModal] = useState(false);
  const list = useQuery({ queryKey: ['balances'], queryFn: balancesApi.latest, refetchInterval: 60_000 });
  const rates = useQuery({ queryKey: ['rates'], queryFn: balancesApi.rates });

  const cols: Column<AccountBalances>[] = [
    { key: 'acc', header: 'Account', mobile: 'title', cell: (r) => <div className="min-w-0"><div className="flex items-center gap-2">
      <Link to={`/accounts/${r.account_id}`} className="font-medium text-ink hover:underline truncate">{r.account_name}</Link><DemoBadge show={r.is_demo} /></div>
      <div className="text-[12px] text-muted">{r.institution} · {r.currency}</div></div> },
    ...TYPES.map((t) => ({ key: t, header: BALANCE_LABELS[t], align: 'right' as const, cell: (r: AccountBalances) => <Cell row={r} type={t} /> })),
  ];

  return (
    <>
      <PageHeader eyebrow="Monitoring" title="Balance monitoring"
        subtitle="Available, ledger and provider-current balances are shown separately from statement balances. A statement closing balance is historical — never a live balance." />
      <Notice tone="info">Balances stay in their original currency here. Consolidated totals on the dashboard use the dated exchange rates listed below; currencies without a rate are excluded, not guessed.</Notice>
      <Card className="mt-4" padded={false}>
        <DataTable columns={cols} rows={list.data} rowKey={(r) => r.account_id} loading={list.isLoading} error={list.error}
          empty={<EmptyState icon={<Building2 className="h-6 w-6" />} title="No balances yet" body="Balances appear after a provider sync or statement import." />} />
      </Card>
      <Card className="mt-4" padded={false}>
        <div className="p-5 pb-2"><CardHeader title="Exchange rates" subtitle="Used for consolidated reporting only"
          action={can('settings:manage') && <Button size="sm" icon={<Plus className="h-3.5 w-3.5" />} onClick={() => setRateModal(true)}>Add rate</Button>} /></div>
        <DataTable rows={rates.data} rowKey={(r) => r.id} loading={rates.isLoading} empty={<EmptyState title="No exchange rates recorded" />} columns={[
          { key: 'pair', header: 'Pair', mobile: 'title', cell: (r) => <span className="font-medium">{r.base_currency} → {r.quote_currency}</span> },
          { key: 'rate', header: 'Rate', align: 'right', mobile: 'meta', cell: (r) => <span className="num">{Number(r.rate).toFixed(6)}</span> },
          { key: 'date', header: 'Rate date', cell: (r) => date(r.rate_date) },
          { key: 'src', header: 'Source', cell: (r) => <span className="text-[13px]">{r.source}</span> },
        ]} />
      </Card>
      <AddRate open={rateModal} onClose={() => setRateModal(false)} />
    </>
  );
}
