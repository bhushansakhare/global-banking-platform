import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, History, Pencil, PlugZap, RefreshCw, Settings2, ShieldCheck, Trash2, Unplug, Upload, Zap } from 'lucide-react';
import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Button, Card, CardHeader, DemoBadge, Field, Input, Select, StatusBadge } from '@/components/ui/primitives';
import { DataTable, Pagination } from '@/components/ui/DataTable';
import { AutomationActions, AutomationSummary } from '@/features/accounts/AutomationControls';
import { MonitoringControls } from '@/features/accounts/MonitoringControls';
import { ConnectionSetupModal } from '@/features/accounts/ConnectionSetupModal';
import { ConfirmDialog, Modal } from '@/components/ui/Modal';
import { KeyValue, Notice, PageHeader } from '@/components/ui/layout';
import { ErrorState, TableSkeleton } from '@/components/ui/States';
import { Money } from '@/components/Money';
import { UploadStatementModal } from '@/features/statements/UploadStatementModal';
import { useAction } from '@/hooks/useMutationToast';
import { accountsApi } from '@/services/api';
import { useAuth } from '@/stores/auth';
import type { Account, BalanceType } from '@/types/api';
import { date, dateTime, relative } from '@/utils/format';
import { BALANCE_LABELS, JOB_LABELS, METHOD_LABELS } from '@/utils/status';

const ORDER: BalanceType[] = ['available', 'ledger', 'current', 'statement_opening', 'statement_closing', 'reported'];

function EditModal({ a, open, onClose }: { a: Account; open: boolean; onClose: () => void }) {
  const [f, setF] = useState({ nickname: a.nickname, holder_name: a.holder_name ?? '', low_balance_threshold: a.low_balance_threshold ?? '',
    statement_period: a.statement_period, notes: a.notes ?? '', account_type: a.account_type, sync_schedule: a.sync_schedule ?? '',
    timezone: a.timezone ?? '' });
  const save = useAction(() => accountsApi.update(a.id, { ...f, low_balance_threshold: f.low_balance_threshold || null,
    sync_schedule: f.sync_schedule.trim() || null, timezone: f.timezone.trim() || undefined }),
    { success: 'Account updated', invalidate: [['account', a.id], ['accounts']], onSuccess: onClose });
  const submit = (e: FormEvent) => { e.preventDefault(); save.mutate(undefined); };
  return (
    <Modal open={open} onClose={onClose} title="Edit account details" footer={<><Button variant="outline" onClick={onClose}>Cancel</Button>
      <Button type="submit" form="edit-acc" loading={save.isPending}>Save changes</Button></>}>
      <form id="edit-acc" onSubmit={submit} className="space-y-4">
        <Field label="Nickname" htmlFor="e-nick"><Input id="e-nick" value={f.nickname} onChange={(e) => setF({ ...f, nickname: e.target.value })} required /></Field>
        <Field label="Account holder" htmlFor="e-holder"><Input id="e-holder" value={f.holder_name} onChange={(e) => setF({ ...f, holder_name: e.target.value })} /></Field>
        <div className="grid grid-cols-1 xs:grid-cols-2 gap-4">
          <Field label="Low-balance threshold" htmlFor="e-thr"><Input id="e-thr" inputMode="decimal" value={f.low_balance_threshold}
            onChange={(e) => setF({ ...f, low_balance_threshold: e.target.value.replace(/[^\d.]/g, '') })} /></Field>
          <Field label="Statement period" htmlFor="e-per"><Select id="e-per" value={f.statement_period} onChange={(e) => setF({ ...f, statement_period: e.target.value })}>
            {['daily', 'weekly', 'monthly', 'quarterly'].map((p) => <option key={p} value={p}>{p[0].toUpperCase() + p.slice(1)}</option>)}</Select></Field>
        </div>
        <div className="grid grid-cols-1 xs:grid-cols-3 gap-4">
          <Field label="Account type" htmlFor="e-type"><Select id="e-type" value={f.account_type} onChange={(e) => setF({ ...f, account_type: e.target.value })}>
            {['savings', 'current', 'deposit', 'credit_card', 'loan', 'escrow', 'other'].map((t) => <option key={t} value={t}>{t.replace('_', ' ')}</option>)}</Select></Field>
          <Field label="Own sync schedule (cron)" htmlFor="e-cron" hint="Empty = organisation schedule"><Input id="e-cron" className="font-mono" value={f.sync_schedule}
            onChange={(e) => setF({ ...f, sync_schedule: e.target.value })} placeholder="0 7 * * *" /></Field>
          <Field label="Schedule time zone" htmlFor="e-tz"><Input id="e-tz" value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })}
            placeholder="Asia/Kolkata" /></Field>
        </div>
        <Field label="Notes" htmlFor="e-notes"><Input id="e-notes" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
      </form>
    </Modal>
  );
}

export default function AccountDetail() {
  const { id = '' } = useParams();
  const navigate = useNavigate();
  const can = useAuth((s) => s.can);
  const [edit, setEdit] = useState(false);
  const [upload, setUpload] = useState(false);
  const [confirm, setConfirm] = useState<null | 'delete' | 'disconnect'>(null);
  const [params, setParams] = useSearchParams();
  const [connectOpen, setConnectOpen] = useState(params.get('connect') === '1');
  const [histPage, setHistPage] = useState(1);
  useEffect(() => { if (params.get('connect') === '1') { setConnectOpen(true); params.delete('connect'); setParams(params, { replace: true }); } },
    [params, setParams]);
  const history = useQuery({ queryKey: ['sync-history', id, histPage], refetchInterval: 15_000,
    queryFn: () => accountsApi.syncHistory(id, { page: histPage, page_size: 10 }) });
  const q = useQuery({ queryKey: ['account', id], queryFn: () => accountsApi.get(id), refetchInterval: 20_000 });
  const inv = [['account', id], ['accounts'], ['analytics']];
  const check = useAction((cid: string) => accountsApi.check(cid), { invalidate: inv, success: (c) => `Connection status: ${c.status.replace('_', ' ')}` });
  const disconnect = useAction((cid: string) => accountsApi.disconnect(cid), { invalidate: inv, success: 'Disconnected', onSuccess: () => setConfirm(null) });
  const reconnect = useAction((cid: string) => accountsApi.reconnect(cid), { invalidate: inv, success: 'Reconnected' });
  const scenario = useAction(({ cid, s }: { cid: string; s: string }) => accountsApi.demoScenario(cid, s), { invalidate: inv, success: 'Demo scenario updated' });
  const remove = useAction(() => accountsApi.remove(id), { invalidate: [['accounts']], success: 'Account removed',
    onSuccess: () => navigate('/accounts') });

  if (q.isError) return <Card><ErrorState error={q.error} onRetry={() => q.refetch()} /></Card>;
  if (!q.data) return <Card padded={false}><TableSkeleton /></Card>;
  const a = q.data;
  const c = a.connection;

  return (
    <>
      <Link to="/accounts" className="inline-flex items-center gap-1.5 text-[13px] text-muted hover:text-ink mb-3"><ArrowLeft className="h-4 w-4" />Bank accounts</Link>
      <PageHeader title={<span className="flex flex-wrap items-center gap-2">{a.nickname}<DemoBadge show={a.is_demo} /></span>}
        subtitle={`${a.institution.name} · ${a.account_number_masked} · ${a.currency}`}
        actions={<>
          {can('statements:write') && <Button variant="outline" icon={<Upload className="h-4 w-4" />} onClick={() => setUpload(true)}>Upload statement (fallback)</Button>}
          {can('connections:manage') && <Button variant="outline" icon={<Settings2 className="h-4 w-4" />} onClick={() => setConnectOpen(true)}>Connection setup</Button>}
        </>} />
      <Card className="mb-4">
        <CardHeader title="Automation" icon={<Zap className="h-4 w-4" />}
          subtitle="Healthy is shown only after a verified synchronisation with live data." action={<AutomationActions account={a} onConnect={() => setConnectOpen(true)} size="sm" />} />
        <AutomationSummary account={a} />
      </Card>
      <MonitoringControls account={a} editable={can('accounts:write')} />
      {a.is_demo && <div className="mb-5"><Notice tone="demo">This account is connected to the Demo Bank Simulator. All balances, transactions and statements are synthetic.</Notice></div>}

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <Card className="xl:col-span-2">
          <CardHeader title="Balances" subtitle="Each balance type is stored separately with its timestamp and source — a statement closing balance is never shown as a live balance." />
          <div className="grid grid-cols-1 xs:grid-cols-2 lg:grid-cols-3 gap-3">
            {ORDER.filter((t) => a.balances[t]).map((t) => {
              const b = a.balances[t]!;
              return (
                <div key={t} className="rounded-xl border border-line p-4">
                  <p className="text-[12px] font-medium text-muted">{BALANCE_LABELS[t]}</p>
                  <Money value={b.amount} currency={b.currency} className="block mt-1 text-lg font-semibold text-ink" />
                  <p className="mt-1 text-[11.5px] text-muted">As of {dateTime(b.as_of)}</p>
                  <p className="text-[11.5px] text-muted">Source: {b.source_ref ?? b.source}{b.is_demo ? ' (demo)' : ''} · {b.currency}</p>
                  {b.verification_status && <div className="mt-1"><StatusBadge status={b.verification_status} /></div>}
                </div>
              );
            })}
            {!ORDER.some((t) => a.balances[t]) && <p className="text-sm text-muted">No balances captured yet. Balances appear only from verified data (a successful sync or a processed statement).</p>}
          </div>
          {a.stats?.cash_flow_30d && (
            <div className="mt-4 grid grid-cols-1 xs:grid-cols-3 gap-3">
              {([['Total credits · 30 d', a.stats.cash_flow_30d.credits], ['Total debits · 30 d', a.stats.cash_flow_30d.debits],
                ['Net cash flow · 30 d', a.stats.cash_flow_30d.net]] as const).map(([l, v]) => (
                <div key={l} className="rounded-xl bg-surface-2 p-3"><p className="text-[12px] text-muted">{l}</p>
                  <Money value={v} currency={a.stats!.cash_flow_30d!.currency} className="font-semibold text-ink" />
                  <p className="text-[11px] text-muted">{a.stats!.cash_flow_30d!.count} transaction(s), {a.currency} only</p></div>
              ))}
            </div>
          )}
        </Card>
        <Card>
          <CardHeader title="Connection" icon={<PlugZap className="h-4 w-4" />} action={<StatusBadge status={c?.status ?? 'disconnected'} />} />
          {c ? (
            <div className="space-y-4">
              <KeyValue items={[{ label: 'Method', value: METHOD_LABELS[c.connection_method ?? ''] ?? '—' },
                { label: 'Provider', value: c.provider_name }, { label: 'Integration', value: <StatusBadge status={c.integration_status} /> },
                { label: 'Environment', value: <StatusBadge status={c.environment ?? 'unknown'} /> },
                { label: 'Authorisation', value: <StatusBadge status={c.status} /> },
                { label: 'Last check', value: relative(c.last_checked_at) }, { label: 'Last success', value: relative(c.last_success_at) }]} />
              {c.connection_method === 'manual_statement_import' && (
                <Notice tone="warning" title="Manual import only">Nothing is collected automatically for this account.
                  <button type="button" className="link ml-1" onClick={() => setConnectOpen(true)}>Connect the bank</button> to automate it.</Notice>
              )}
              {c.last_error && <Notice tone="warning" title="Last error">{c.last_error}</Notice>}
              {c.is_demo && can('connections:manage') && (
                <Field label="Demo scenario" htmlFor="scn" hint="Simulate provider behaviour">
                  <Select id="scn" defaultValue="" onChange={(e) => e.target.value && scenario.mutate({ cid: c.id, s: e.target.value })}>
                    <option value="" disabled>Choose a scenario…</option><option value="ok">Healthy</option><option value="otp_required">One-time passcode required</option>
                    <option value="provider_down">Provider downtime</option><option value="consent_expired">Consent expired</option><option value="auth_failed">Authentication failed</option>
                  </Select>
                </Field>
              )}
              {can('connections:manage') && (
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="outline" icon={<RefreshCw className="h-3.5 w-3.5" />} loading={check.isPending}
                    disabled={c.status === 'disconnected'} onClick={() => check.mutate(c.id)}>Check</Button>
                  {c.status === 'disconnected'
                    ? <Button size="sm" variant="outline" icon={<ShieldCheck className="h-3.5 w-3.5" />} onClick={() => reconnect.mutate(c.id)}>Reconnect</Button>
                    : <Button size="sm" variant="outline" icon={<Unplug className="h-3.5 w-3.5" />} onClick={() => setConfirm('disconnect')}>Disconnect</Button>}
                </div>
              )}
            </div>
          ) : <p className="text-sm text-muted">No connection.</p>}
        </Card>
      </div>

      <div className="mt-4 grid grid-cols-1 xl:grid-cols-3 gap-4">
        <Card className="xl:col-span-2">
          <CardHeader title="Account details" action={can('accounts:write') && <Button size="sm" variant="ghost" icon={<Pencil className="h-3.5 w-3.5" />} onClick={() => setEdit(true)}>Edit</Button>} />
          <KeyValue cols={3} items={[
            { label: 'Bank', value: a.institution.name }, { label: 'Country', value: a.country_code }, { label: 'Currency', value: a.currency },
            { label: 'Account type', value: a.account_type.replace('_', ' ') }, { label: 'Account number', value: a.account_number_masked },
            { label: 'Holder', value: a.holder_name ?? '—' }, { label: 'Statement period', value: a.statement_period },
            { label: 'Last statement', value: date(a.last_statement_date) }, { label: 'Last sync', value: dateTime(a.last_sync_at) },
            { label: 'Low-balance alert', value: a.low_balance_threshold ? <Money value={a.low_balance_threshold} currency={a.currency} /> : 'Off' },
            { label: 'Transactions', value: <Link className="link" to={`/transactions?account_id=${a.id}`}>{a.stats?.transactions ?? 0}</Link> },
            { label: 'Statements', value: <Link className="link" to={`/statements?account_id=${a.id}`}>{a.stats?.statements ?? 0}</Link> },
          ]} />
          {can('accounts:write') && <div className="mt-6 pt-4 border-t border-line"><Button variant="ghost" size="sm" className="text-red-600 hover:text-red-700"
            icon={<Trash2 className="h-3.5 w-3.5" />} onClick={() => setConfirm('delete')}>Remove account</Button></div>}
        </Card>
        <Card>
          <CardHeader title="Recent jobs" action={<Link to={`/automation?account_id=${a.id}`} className="link text-[13px]">All</Link>} />
          <ul className="space-y-3">
            {(a.recent_jobs ?? []).map((j) => (
              <li key={j.id} className="flex items-start gap-3">
                <div className="min-w-0 flex-1"><p className="text-sm text-ink">{JOB_LABELS[j.job_type] ?? j.job_type}</p>
                  <p className="text-[12px] text-muted">{relative(j.created_at)}{j.error_message ? ` · ${j.error_message}` : ''}</p></div>
                <StatusBadge status={j.status} />
              </li>
            ))}
            {!a.recent_jobs?.length && <p className="text-sm text-muted">No jobs yet.</p>}
          </ul>
        </Card>
      </div>

      <Card className="mt-4" padded={false}>
        <div id="history" className="p-5 pb-0"><CardHeader title="Synchronisation history" icon={<History className="h-4 w-4" />}
          subtitle="Every attempt with its real outcome — skipped or failed runs are never shown as successful." /></div>
        <DataTable rows={history.data?.items} rowKey={(j) => j.id} loading={history.isLoading} error={history.error}
          onRowClick={(j) => navigate(`/automation?job=${j.id}`)} empty={<p className="p-5 text-sm text-muted">No synchronisation has run yet.</p>}
          columns={[
            { key: 't', header: 'Job', mobile: 'title', cell: (j) => <div><div className="text-ink">{JOB_LABELS[j.job_type] ?? j.job_type}</div>
              <div className="text-[11px] text-muted">{j.trigger} · {dateTime(j.created_at)}</div></div> },
            { key: 's', header: 'Status', mobile: 'meta', cell: (j) => <StatusBadge status={j.status} /> },
            { key: 'r', header: 'Result', cell: (j) => <span className="text-[12.5px] text-muted">
              {j.error_message ? j.error_message : j.result && typeof j.result === 'object'
                ? (j.result as Record<string, unknown>).skipped ? `Skipped: ${String((j.result as Record<string, unknown>).skipped)}`
                  : Object.entries(j.result as Record<string, unknown>).filter(([, v]) => typeof v === 'number').map(([k, v]) => `${k.replace(/_/g, ' ')} ${v}`).join(' · ') || '—'
                : '—'}</span> },
            { key: 'a', header: 'Attempts', align: 'right', cell: (j) => <span className="num">{j.attempts}/{j.max_attempts}</span> },
          ]} />
        {history.data && history.data.pages > 1 && <Pagination page={histPage} pages={history.data.pages} total={history.data.total} pageSize={10} onPage={setHistPage} />}
      </Card>

      <ConnectionSetupModal account={a} open={connectOpen} onClose={() => setConnectOpen(false)} />
      <EditModal a={a} open={edit} onClose={() => setEdit(false)} key={a.id + String(edit)} />
      <UploadStatementModal open={upload} onClose={() => setUpload(false)} accountId={a.id} />
      <ConfirmDialog open={confirm === 'disconnect'} onClose={() => setConfirm(null)} loading={disconnect.isPending}
        onConfirm={() => c && disconnect.mutate(c.id)} title="Disconnect this bank?" confirmLabel="Disconnect"
        body="Stored provider credentials are wiped and consents revoked. Historical data is kept. You can reconnect later." />
      <ConfirmDialog open={confirm === 'delete'} onClose={() => setConfirm(null)} destructive loading={remove.isPending}
        onConfirm={() => remove.mutate(undefined)} title="Remove account?" confirmLabel="Remove account"
        body="The account is archived and disconnected. Original statements, transactions and audit history are preserved." />
    </>
  );
}
