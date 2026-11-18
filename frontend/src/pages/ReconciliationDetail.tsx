import { useQuery } from '@tanstack/react-query';
import clsx from 'clsx';
import { ArrowLeft, Check, Link2, X } from 'lucide-react';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Badge, Button, Card, CardHeader, DemoBadge, Field, Select, StatusBadge, Textarea } from '@/components/ui/primitives';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { Modal } from '@/components/ui/Modal';
import { KeyValue, Notice, PageHeader, StatCard, Tabs } from '@/components/ui/layout';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/States';
import { Money } from '@/components/Money';
import { useAction } from '@/hooks/useMutationToast';
import { reconApi } from '@/services/api';
import { useAuth } from '@/stores/auth';
import type { ReconException, ReconMatch } from '@/types/api';
import { date, dateTime, pct, titleCase } from '@/utils/format';

type Tab = 'review' | 'confirmed' | 'exceptions' | 'all';

export default function ReconciliationDetail() {
  const { id = '' } = useParams();
  const can = useAuth((s) => s.can('reconciliation:write'));
  const [tab, setTab] = useState<Tab>('review');
  const [resolve, setResolve] = useState<ReconException | null>(null);
  const [note, setNote] = useState('');
  const [resStatus, setResStatus] = useState<'resolved' | 'ignored'>('resolved');
  const [manual, setManual] = useState<ReconException | null>(null);
  const [bookPick, setBookPick] = useState('');
  const q = useQuery({ queryKey: ['recon', 'session', id], queryFn: () => reconApi.session(id) });
  const inv = [['recon'], ['transactions']];
  const decide = useAction(({ m, d }: { m: string; d: 'confirm' | 'reject' }) => reconApi.decide(m, d), {
    invalidate: inv, success: (r) => (r.status === 'manual' ? 'Match confirmed' : 'Match rejected') });
  const upd = useAction(() => reconApi.updateException(resolve!.id, resStatus, note), { invalidate: inv, success: 'Exception updated',
    onSuccess: () => { setResolve(null); setNote(''); } });
  const mm = useAction(() => reconApi.manualMatch(id, manual!.transaction_id!, bookPick, note || undefined), { invalidate: inv,
    success: 'Manual match recorded', onSuccess: () => { setManual(null); setBookPick(''); setNote(''); } });

  if (q.isError) return <Card><ErrorState error={q.error} onRetry={() => q.refetch()} /></Card>;
  if (!q.data) return <Card padded={false}><TableSkeleton /></Card>;
  const s = q.data;
  const ccy = s.currency;
  const review = s.matches.filter((m) => m.status === 'suggested');
  const confirmed = s.matches.filter((m) => m.status === 'confirmed' || m.status === 'manual');
  const openExc = s.exceptions.filter((e) => e.status === 'open');
  const unmatchedBook = s.exceptions.filter((e) => e.exception_type === 'unmatched_book' && e.status === 'open' && e.book_entry);

  const matchCols: Column<ReconMatch>[] = [
    { key: 'bank', header: 'Bank transaction', mobile: 'title', cell: (m) => m.transaction ? <div className="min-w-0 max-w-[300px]">
      <div className="truncate text-ink">{m.transaction.description}</div>
      <div className="text-[12px] text-muted">{date(m.transaction.booking_date)} · {m.transaction.reference ?? 'no ref'}</div></div> : '—' },
    { key: 'amt', header: 'Amount', align: 'right', mobile: 'meta', cell: (m) => <Money value={m.transaction?.amount} currency={ccy} signed /> },
    { key: 'book', header: 'Cash-book entry', cell: (m) => m.book_entry ? <div className="min-w-0 max-w-[300px]">
      <div className="truncate text-ink">{m.book_entry.description}</div>
      <div className="text-[12px] text-muted">{date(m.book_entry.entry_date)} · <Money value={m.book_entry.amount} currency={ccy} /></div></div> : '—' },
    { key: 'why', header: 'Basis', cell: (m) => <div className="max-w-[260px]"><Badge tone="brand">{titleCase(m.match_type)}</Badge>
      <div className="mt-1 text-[11.5px] text-muted line-clamp-2">{m.reasons.join(' · ')}</div></div> },
    { key: 'score', header: 'Score', align: 'right', cell: (m) => <span className={clsx('num font-medium', Number(m.score) >= 0.9 ? 'text-olive-700 dark:text-olive-400' : 'text-amber-700 dark:text-amber-400')}>{pct(m.score, 0)}</span> },
    { key: 'st', header: 'Status', cell: (m) => <StatusBadge status={m.status} /> },
    { key: 'act', header: '', align: 'right', cell: (m) => can && m.status === 'suggested' ? (
      <div className="flex justify-end gap-1.5" onClick={(e) => e.stopPropagation()}>
        <Button size="sm" variant="success" icon={<Check className="h-3.5 w-3.5" />} onClick={() => decide.mutate({ m: m.id, d: 'confirm' })}>Confirm</Button>
        <Button size="sm" variant="outline" icon={<X className="h-3.5 w-3.5" />} onClick={() => decide.mutate({ m: m.id, d: 'reject' })}>Reject</Button>
      </div>) : m.decided_at ? <span className="text-[11px] text-muted">Decided {dateTime(m.decided_at)}</span> : null },
  ];

  const excCols: Column<ReconException>[] = [
    { key: 't', header: 'Type', cell: (e) => <Badge tone={e.severity === 'critical' ? 'danger' : e.severity === 'warning' ? 'warning' : 'info'}>{titleCase(e.exception_type)}</Badge> },
    { key: 'm', header: 'Detail', mobile: 'title', cell: (e) => <div className="max-w-[460px]"><p className="text-ink text-[13px]">{e.message}</p>
      {e.resolution_note && <p className="text-[12px] text-muted mt-0.5">Note: {e.resolution_note}</p>}</div> },
    { key: 'a', header: 'Amount', align: 'right', cell: (e) => <Money value={e.amount} currency={ccy} /> },
    { key: 's', header: 'Status', mobile: 'meta', cell: (e) => <StatusBadge status={e.status} /> },
    { key: 'x', header: '', align: 'right', cell: (e) => can && e.status === 'open' ? (
      <div className="flex justify-end gap-1.5">
        {e.exception_type === 'unmatched_bank' && unmatchedBook.length > 0 && <Button size="sm" variant="outline" icon={<Link2 className="h-3.5 w-3.5" />} onClick={() => setManual(e)}>Match</Button>}
        <Button size="sm" variant="outline" onClick={() => setResolve(e)}>Resolve</Button></div>) : null },
  ];

  return (
    <>
      <Link to="/reconciliation" className="inline-flex items-center gap-1.5 text-[13px] text-muted hover:text-ink mb-3"><ArrowLeft className="h-4 w-4" />Reconciliation</Link>
      <PageHeader title={<span className="flex flex-wrap items-center gap-2">{s.account_name}<DemoBadge show={s.is_demo} /><StatusBadge status={s.status} /></span>}
        subtitle={`${date(s.period_start)} – ${date(s.period_end)} · engine v${s.engine_version} · ${s.trigger} run ${dateTime(s.created_at)}`} />
      {s.summary.note && <div className="mb-4"><Notice tone="info">{s.summary.note}</Notice></div>}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Confirmed matches" tone="olive" value={`${s.matched_count} / ${s.bank_transaction_count}`} sub={`Match rate ${pct(s.match_rate)}`} />
        <StatCard label="Needs review" tone="accent" value={review.length} sub="Suggested — decide manually" />
        <StatCard label="Open exceptions" tone={openExc.length ? 'amber' : 'olive'} value={openExc.length} sub={`${s.unmatched_bank_count} bank · ${s.unmatched_book_count} book unmatched`} />
        <StatCard label="Balance difference" tone={s.balance_difference && Number(s.balance_difference) !== 0 ? 'red' : 'olive'}
          value={s.balance_difference === null ? '—' : <Money value={s.balance_difference} currency={ccy} />} sub="Closing − (opening + movements)" />
      </div>
      <Card className="mt-4">
        <CardHeader title="Balance verification" />
        <KeyValue cols={4} items={[
          { label: 'Opening balance', value: <Money value={s.opening_balance} currency={ccy} /> },
          { label: 'Expected closing', value: <Money value={s.expected_closing_balance} currency={ccy} /> },
          { label: 'Statement closing', value: <Money value={s.closing_balance} currency={ccy} /> },
          { label: 'Tolerances', value: `±${s.rules.date_tolerance_days as number} days · amount ±${s.rules.amount_tolerance_abs as string}` },
        ]} />
      </Card>
      <Card className="mt-4" padded={false}>
        <div className="px-5 pt-4"><Tabs value={tab} onChange={setTab} tabs={[
          { value: 'review', label: 'Needs review', count: review.length }, { value: 'exceptions', label: 'Exceptions', count: openExc.length },
          { value: 'confirmed', label: 'Matched', count: confirmed.length }, { value: 'all', label: 'All matches', count: s.matches.length }]} /></div>
        {tab === 'exceptions'
          ? <DataTable columns={excCols} rows={s.exceptions} rowKey={(e) => e.id} empty={<EmptyState title="No exceptions" body="Everything reconciled." />} />
          : <DataTable columns={matchCols} rowKey={(m) => m.id}
            rows={tab === 'review' ? review : tab === 'confirmed' ? confirmed : s.matches}
            empty={<EmptyState title={tab === 'review' ? 'Nothing to review' : 'No matches'} />} />}
      </Card>

      <Modal open={!!resolve} onClose={() => setResolve(null)} title="Resolve exception" description={resolve?.message}
        footer={<><Button variant="outline" onClick={() => setResolve(null)}>Cancel</Button><Button disabled={note.trim().length < 3} loading={upd.isPending} onClick={() => upd.mutate(undefined)}>Save</Button></>}>
        <div className="space-y-4">
          <Field label="Outcome" htmlFor="res-st"><Select id="res-st" value={resStatus} onChange={(e) => setResStatus(e.target.value as 'resolved' | 'ignored')}>
            <option value="resolved">Resolved</option><option value="ignored">Ignored (not an issue)</option></Select></Field>
          <Field label="Note (required, audited)" htmlFor="res-note"><Textarea id="res-note" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
          <p className="text-[12px] text-muted">Original bank data is never changed — the decision is recorded alongside it in the audit trail.</p>
        </div>
      </Modal>
      <Modal open={!!manual} onClose={() => setManual(null)} title="Create manual match" description={manual?.message}
        footer={<><Button variant="outline" onClick={() => setManual(null)}>Cancel</Button><Button disabled={!bookPick} loading={mm.isPending} onClick={() => mm.mutate(undefined)}>Match</Button></>}>
        <div className="space-y-4">
          <Field label="Cash-book entry" htmlFor="mm-b"><Select id="mm-b" value={bookPick} onChange={(e) => setBookPick(e.target.value)}>
            <option value="">Select unmatched entry…</option>
            {unmatchedBook.map((e) => <option key={e.book_entry!.id} value={e.book_entry!.id}>{date(e.book_entry!.entry_date)} · {e.book_entry!.description} · {e.book_entry!.amount}</option>)}
          </Select></Field>
          <Field label="Note" htmlFor="mm-n"><Textarea id="mm-n" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        </div>
      </Modal>
    </>
  );
}
