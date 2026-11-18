import { useQuery } from '@tanstack/react-query';
import { BookOpen, GitCompareArrows, Play, Upload } from 'lucide-react';
import { useRef, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Card, CardHeader, Field, Input, Select, StatusBadge } from '@/components/ui/primitives';
import { DataTable, Pagination, type Column } from '@/components/ui/DataTable';
import { Modal } from '@/components/ui/Modal';
import { Notice, PageHeader, StatCard } from '@/components/ui/layout';
import { EmptyState } from '@/components/ui/States';
import { Money } from '@/components/Money';
import { errorText, useAction } from '@/hooks/useMutationToast';
import { accountsApi, reconApi, statementsApi, transactionsApi } from '@/services/api';
import { useAuth } from '@/stores/auth';
import type { ReconSession } from '@/types/api';
import { date, daysAgo, isoDate, pct, relative } from '@/utils/format';

function RunModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const [acc, setAcc] = useState('');
  const [mode, setMode] = useState<'statement' | 'period'>('statement');
  const [st, setSt] = useState('');
  const [from, setFrom] = useState(daysAgo(30));
  const [to, setTo] = useState(isoDate(new Date()));
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: () => accountsApi.list(), enabled: open });
  const stmts = useQuery({ queryKey: ['statements', 'for', acc], enabled: open && !!acc,
    queryFn: () => statementsApi.list({ account_id: acc, page_size: 50 }) });
  const run = useAction(() => reconApi.run(mode === 'statement' ? { account_id: acc, statement_id: st } : { account_id: acc, period_start: from, period_end: to }),
    { success: 'Reconciliation complete', invalidate: [['recon'], ['transactions'], ['analytics']], onSuccess: (s) => { onClose(); navigate(`/reconciliation/${s.id}`); } });
  const submit = (e: FormEvent) => { e.preventDefault(); run.mutate(undefined); };
  const ok = acc && (mode === 'statement' ? st : from && to);
  return (
    <Modal open={open} onClose={onClose} title="Run reconciliation" description="Deterministic matching of bank transactions against your cash-book, with opening/closing balance verification."
      footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button type="submit" form="run-rec" disabled={!ok} loading={run.isPending} icon={<Play className="h-4 w-4" />}>Run</Button></>}>
      <form id="run-rec" onSubmit={submit} className="space-y-4">
        {run.error && <Notice tone="danger">{errorText(run.error)}</Notice>}
        <Field label="Account" htmlFor="rr-acc"><Select id="rr-acc" value={acc} onChange={(e) => { setAcc(e.target.value); setSt(''); }}>
          <option value="">Select account…</option>{accounts.data?.map((a) => <option key={a.id} value={a.id}>{a.nickname}</option>)}</Select></Field>
        <div className="flex gap-2">
          <Button size="sm" variant={mode === 'statement' ? 'secondary' : 'ghost'} onClick={() => setMode('statement')}>By statement</Button>
          <Button size="sm" variant={mode === 'period' ? 'secondary' : 'ghost'} onClick={() => setMode('period')}>By period</Button>
        </div>
        {mode === 'statement' ? (
          <Field label="Statement" htmlFor="rr-st"><Select id="rr-st" value={st} disabled={!acc} onChange={(e) => setSt(e.target.value)}>
            <option value="">Select statement…</option>{stmts.data?.items.filter((s) => s.period_start).map((s) =>
              <option key={s.id} value={s.id}>{date(s.period_start)} – {date(s.period_end)} · {s.transaction_count} txns</option>)}</Select></Field>
        ) : (
          <div className="grid grid-cols-2 gap-4">
            <Field label="From" htmlFor="rr-f"><Input id="rr-f" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
            <Field label="To" htmlFor="rr-t"><Input id="rr-t" type="date" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
          </div>
        )}
      </form>
    </Modal>
  );
}

function ImportBook({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [acc, setAcc] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const ref = useRef<HTMLInputElement>(null);
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: () => accountsApi.list(), enabled: open });
  const imp = useAction(() => transactionsApi.importBook(acc, file!), { success: (r) => `${r.created} entries imported (${r.skipped_duplicates} duplicates skipped)`,
    invalidate: [['book']], onSuccess: onClose });
  return (
    <Modal open={open} onClose={onClose} title="Import cash-book entries" description="CSV or Excel with Date, Description, Amount (or Debit/Credit) and optional Reference columns. Re-imports are de-duplicated."
      footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button disabled={!acc || !file} loading={imp.isPending} onClick={() => imp.mutate(undefined)}
        icon={<Upload className="h-4 w-4" />}>Import</Button></>}>
      <div className="space-y-4">
        {imp.error && <Notice tone="danger">{errorText(imp.error)}</Notice>}
        <Field label="Account" htmlFor="ib-acc"><Select id="ib-acc" value={acc} onChange={(e) => setAcc(e.target.value)}>
          <option value="">Select account…</option>{accounts.data?.map((a) => <option key={a.id} value={a.id}>{a.nickname}</option>)}</Select></Field>
        <div><Button variant="outline" onClick={() => ref.current?.click()}>{file ? file.name : 'Choose file…'}</Button>
          <input ref={ref} type="file" accept=".csv,.xlsx,.xls" className="sr-only" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></div>
      </div>
    </Modal>
  );
}

export default function Reconciliation() {
  const navigate = useNavigate();
  const can = useAuth((s) => s.can);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState('');
  const [run, setRun] = useState(false);
  const [imp, setImp] = useState(false);
  const list = useQuery({ queryKey: ['recon', 'sessions', page, status], queryFn: () => reconApi.sessions({ page, page_size: 25, status }) });
  const latest = list.data?.items ?? [];
  const balanced = latest.filter((s) => s.status === 'balanced').length;

  const cols: Column<ReconSession>[] = [
    { key: 'acc', header: 'Account', mobile: 'title', cell: (s) => <div><div className="font-medium text-ink">{s.account_name}</div>
      <div className="text-[12px] text-muted">{date(s.period_start)} – {date(s.period_end)} · {s.trigger}</div></div> },
    { key: 'st', header: 'Status', mobile: 'meta', cell: (s) => <StatusBadge status={s.status} /> },
    { key: 'm', header: 'Matched', align: 'right', cell: (s) => <span className="num">{s.matched_count}/{s.bank_transaction_count}</span> },
    { key: 'sg', header: 'Suggested', align: 'right', cell: (s) => <span className="num">{s.suggested_count}</span> },
    { key: 'ex', header: 'Exceptions', align: 'right', cell: (s) => <span className="num">{s.exception_count}</span> },
    { key: 'rate', header: 'Match rate', align: 'right', cell: (s) => <span className="num">{pct(s.match_rate)}</span> },
    { key: 'diff', header: 'Balance difference', align: 'right', cell: (s) => s.balance_difference === null ? <span className="text-muted">—</span>
      : <Money value={s.balance_difference} className={Number(s.balance_difference) === 0 ? 'text-olive-700 dark:text-olive-400' : 'text-amber-700 dark:text-amber-400'} /> },
    { key: 'at', header: 'Run', cell: (s) => <span className="text-[13px]">{relative(s.created_at)}</span> },
  ];

  return (
    <>
      <PageHeader eyebrow="Control" title="Reconciliation"
        subtitle="Every run is immutable and reproducible. Uncertain matches are suggested for review — never silently confirmed."
        actions={can('reconciliation:write') && <>
          <Button variant="outline" icon={<BookOpen className="h-4 w-4" />} onClick={() => setImp(true)}>Import cash-book</Button>
          <Button icon={<GitCompareArrows className="h-4 w-4" />} onClick={() => setRun(true)}>Run reconciliation</Button></>} />
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4 mb-4">
        <StatCard label="Sessions" value={list.data?.total ?? '—'} tone="brand" />
        <StatCard label="Balanced (this page)" value={balanced} tone="olive" />
        <StatCard label="With exceptions (this page)" value={latest.length - balanced} tone="amber" />
        <StatCard label="Open suggestions (this page)" value={latest.reduce((a, s) => a + s.suggested_count, 0)} tone="accent" />
      </div>
      <Card padded={false}>
        <div className="p-4 border-b border-line flex flex-col sm:flex-row gap-3 sm:items-center sm:justify-between">
          <CardHeader title="Reconciliation history" />
          <Select aria-label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} className="sm:w-60">
            <option value="">All statuses</option><option value="balanced">Balanced</option><option value="completed_with_exceptions">With exceptions</option>
          </Select>
        </div>
        <DataTable columns={cols} rows={list.data?.items} rowKey={(s) => s.id} loading={list.isLoading} error={list.error}
          onRowClick={(s) => navigate(`/reconciliation/${s.id}`)}
          empty={<EmptyState icon={<GitCompareArrows className="h-6 w-6" />} title="No reconciliation runs yet"
            body="Automation reconciles every processed statement. You can also import a cash-book and run one manually." />} />
        {list.data && <Pagination page={page} pages={list.data.pages} total={list.data.total} pageSize={25} onPage={setPage} />}
      </Card>
      <RunModal open={run} onClose={() => setRun(false)} />
      <ImportBook open={imp} onClose={() => setImp(false)} />
    </>
  );
}
