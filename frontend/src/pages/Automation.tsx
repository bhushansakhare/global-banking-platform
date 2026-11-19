import { useQuery } from '@tanstack/react-query';
import clsx from 'clsx';
import { motion } from 'framer-motion';
import { CalendarClock, Hand, Pause, Play, Power, RotateCcw, Save } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Badge, Button, Card, CardHeader, DemoBadge, Field, Input, Select, StatusBadge, Toggle } from '@/components/ui/primitives';
import { DataTable, Pagination, type Column } from '@/components/ui/DataTable';
import { ConfirmDialog } from '@/components/ui/Modal';
import { Notice, PageHeader, StatCard, Tabs } from '@/components/ui/layout';
import { CardsSkeleton, EmptyState, ErrorState } from '@/components/ui/States';
import { JobDetailModal } from '@/features/automation/JobDetailModal';
import { useAction } from '@/hooks/useMutationToast';
import { accountsApi, automationApi } from '@/services/api';
import { useAuth } from '@/stores/auth';
import type { AutomationSettings, Job } from '@/types/api';
import { dateTime, relative } from '@/utils/format';
import { JOB_LABELS, METHOD_LABELS } from '@/utils/status';

type JobTab = 'all' | 'running' | 'failed' | 'intervention' | 'completed';
const TAB_STATUS: Record<JobTab, string | undefined> = { all: undefined, running: 'running,queued,retry_scheduled',
  failed: 'failed', intervention: 'awaiting_user', completed: 'succeeded' };

function ScheduleForm({ s, disabled }: { s: AutomationSettings; disabled: boolean }) {
  const [f, setF] = useState(s);
  useEffect(() => setF(s), [s]);
  const save = useAction(() => automationApi.updateSettings({ schedule_type: f.schedule_type, daily_time: f.daily_time,
    cron_expression: f.schedule_type === 'custom' ? f.cron_expression : undefined, timezone: f.timezone, lookback_days: f.lookback_days,
    auto_reconcile: f.auto_reconcile, auto_generate_daily_report: f.auto_generate_daily_report, max_attempts: f.max_attempts,
    retry_base_seconds: f.retry_base_seconds }), { success: 'Schedule saved', invalidate: [['automation']] });
  const dirty = JSON.stringify(f) !== JSON.stringify(s);
  return (
    <form onSubmit={(e) => { e.preventDefault(); save.mutate(undefined); }} className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Schedule" htmlFor="st"><Select id="st" disabled={disabled} value={f.schedule_type}
          onChange={(e) => setF({ ...f, schedule_type: e.target.value as AutomationSettings['schedule_type'] })}>
          <option value="daily">Daily at a set time</option><option value="custom">Custom (cron)</option></Select></Field>
        {f.schedule_type === 'daily'
          ? <Field label="Run time" htmlFor="dt"><Input id="dt" type="time" disabled={disabled} value={f.daily_time} onChange={(e) => setF({ ...f, daily_time: e.target.value })} /></Field>
          : <Field label="Cron expression" htmlFor="cron" hint="minute hour day month weekday — e.g. 0 6,14 * * 1-5"><Input id="cron" disabled={disabled}
            value={f.cron_expression ?? ''} onChange={(e) => setF({ ...f, cron_expression: e.target.value })} className="font-mono" /></Field>}
        <Field label="Time zone" htmlFor="tz"><Input id="tz" disabled={disabled} value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })} /></Field>
        <Field label="Transaction look-back (days)" htmlFor="lb"><Input id="lb" type="number" min={1} max={90} disabled={disabled} value={f.lookback_days}
          onChange={(e) => setF({ ...f, lookback_days: Number(e.target.value) })} /></Field>
        <Field label="Maximum attempts" htmlFor="ma"><Input id="ma" type="number" min={1} max={10} disabled={disabled} value={f.max_attempts}
          onChange={(e) => setF({ ...f, max_attempts: Number(e.target.value) })} /></Field>
        <Field label="Retry base delay (seconds)" htmlFor="rb" hint="Doubles on every attempt (exponential back-off)"><Input id="rb" type="number" min={5}
          disabled={disabled} value={f.retry_base_seconds} onChange={(e) => setF({ ...f, retry_base_seconds: Number(e.target.value) })} /></Field>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {([['auto_reconcile', 'Reconcile automatically', 'Run reconciliation after new statements are processed'],
          ['auto_generate_daily_report', 'Daily report', 'Generate the daily banking report after each run']] as const).map(([k, t, d]) => (
          <div key={k} className="flex items-center justify-between gap-3 rounded-xl border border-line p-3">
            <div><p className="text-sm font-medium text-ink">{t}</p><p className="text-[12px] text-muted">{d}</p></div>
            <Toggle label={t} disabled={disabled} checked={f[k]} onChange={(v) => setF({ ...f, [k]: v })} />
          </div>
        ))}
      </div>
      {!disabled && <div className="flex justify-end"><Button type="submit" disabled={!dirty} loading={save.isPending} icon={<Save className="h-4 w-4" />}>Save schedule</Button></div>}
    </form>
  );
}

export default function Automation() {
  const can = useAuth((s) => s.can);
  const manage = can('automation:manage');
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState<JobTab>((params.get('tab') as JobTab) || 'all');
  const [page, setPage] = useState(1);
  const [confirmOff, setConfirmOff] = useState(false);
  const jobId = params.get('job');
  const accountFilter = params.get('account_id') ?? undefined;
  const ov = useQuery({ queryKey: ['automation'], queryFn: automationApi.overview, refetchInterval: 5000 });
  const jobs = useQuery({ queryKey: ['jobs', tab, page, accountFilter], refetchInterval: 5000,
    queryFn: () => automationApi.jobs({ status: TAB_STATUS[tab], page, page_size: 25, account_id: accountFilter }) });
  const inv = [['automation'], ['jobs'], ['analytics']];
  const master = useAction((v: boolean) => automationApi.master(v), { invalidate: inv,
    success: (s) => (s.master_enabled ? 'Automation switched on' : 'Automation switched off'), onSuccess: () => setConfirmOff(false) });
  const pause = useAction(() => automationApi.pause(), { invalidate: inv, success: 'Automation paused' });
  const resume = useAction(() => automationApi.resume(), { invalidate: inv, success: 'Automation resumed' });
  const runNow = useAction((ids?: string[]) => automationApi.runNow(ids), { invalidate: inv,
    success: (r) => r.message ?? (r.jobs_created ? `${r.jobs_created} collection job(s) queued` : 'A bank run is already in progress') });
  const retryAll = useAction(() => automationApi.retryFailed(), { invalidate: inv, success: (r) => `${r.requeued} job(s) re-queued` });
  const accAction = useAction(({ id, a }: { id: string; a: 'enable' | 'pause' | 'resume' | 'disable' }) => accountsApi.automationAction(id, a),
    { invalidate: inv, success: 'Account automation updated' });
  const syncAcc = useAction((id: string) => accountsApi.syncNow(id), { invalidate: inv,
    success: (r) => (r.created ? 'Synchronisation queued' : 'Already in progress') });

  if (ov.isError) return <Card><ErrorState error={ov.error} onRetry={() => ov.refetch()} /></Card>;
  const o = ov.data;
  const s = o?.settings;
  const on = !!s?.master_enabled;

  const jobCols: Column<Job>[] = [
    { key: 'type', header: 'Job', mobile: 'title', cell: (j) => <div><div className="font-medium text-ink">{JOB_LABELS[j.job_type] ?? j.job_type}</div>
      <div className="text-[12px] text-muted">{j.account_name ?? 'All accounts'} · {j.trigger}</div></div> },
    { key: 'status', header: 'Status', mobile: 'meta', cell: (j) => <StatusBadge status={j.status} /> },
    { key: 'progress', header: 'Progress', cell: (j) => (
      <div className="w-32"><div className="h-1.5 rounded-full bg-line overflow-hidden">
        <motion.div className={clsx('h-full rounded-full', j.status === 'failed' ? 'bg-red-500' : j.status === 'succeeded' ? 'bg-olive-500' : 'bg-accent-500')}
          initial={{ width: 0 }} animate={{ width: `${j.status === 'succeeded' ? 100 : j.progress}%` }} /></div>
        <div className="mt-1 text-[11px] text-muted truncate">{j.progress_message ?? ''}</div></div>) },
    { key: 'attempts', header: 'Attempts', align: 'right', cell: (j) => <span className="num">{j.attempts}/{j.max_attempts}</span> },
    { key: 'created', header: 'Created', cell: (j) => <span className="text-[13px]">{relative(j.created_at)}</span> },
    { key: 'error', header: 'Error', cell: (j) => <span className="text-[12.5px] text-muted line-clamp-1 max-w-[260px]">{j.error_message ?? '—'}</span> },
  ];

  return (
    <>
      <PageHeader eyebrow="Automation" title="Automation Center"
        subtitle="Switch automation on once and the engine collects, extracts, reconciles and reports every day. One bank failing never stops the others."
        actions={manage && <>
          <Button variant="outline" icon={<RotateCcw className="h-4 w-4" />} loading={retryAll.isPending} onClick={() => retryAll.mutate(undefined)}>Retry failed</Button>
          <Button icon={<Play className="h-4 w-4" />} loading={runNow.isPending} onClick={() => runNow.mutate(undefined)}>Run now</Button>
        </>} />

      {!o || !s ? <CardsSkeleton /> : (
        <>
          <Card className={clsx('relative overflow-hidden', on && !s.paused && 'ring-1 ring-olive-300 dark:ring-olive-800')}>
            {on && !s.paused && <div className="absolute inset-y-0 left-0 w-1 bg-olive-500" />}
            <div className="flex flex-col lg:flex-row lg:items-center gap-5">
              <div className="flex items-center gap-4 flex-1 min-w-0">
                <div className={clsx('flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl',
                  on ? (s.paused ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300' : 'bg-olive-100 text-olive-700 dark:bg-olive-900/40 dark:text-olive-300')
                    : 'bg-slate-100 text-slate-500 dark:bg-slate-800')}>
                  <Power className="h-7 w-7" />
                </div>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2"><h2 className="text-lg font-semibold text-ink">Master automation</h2>
                    <Badge tone={on ? (s.paused ? 'warning' : 'success') : 'neutral'} dot>{on ? (s.paused ? 'Paused' : 'On') : 'Off'}</Badge></div>
                  <p className="text-[13px] text-muted mt-0.5">
                    {on ? (s.paused ? 'Scheduled jobs are paused. Saved bank logins can still be run manually.' : `Next run ${s.next_run_at ? dateTime(s.next_run_at) : '—'} (${s.timezone}). Last run ${relative(s.last_run_at)}.`)
                      : 'No scheduled collection will run. Manual runs are still available.'}
                  </p>
                </div>
              </div>
              {manage && (
                <div className="flex flex-wrap items-center gap-3">
                  {on && (s.paused
                    ? <Button variant="outline" icon={<Play className="h-4 w-4" />} loading={resume.isPending} onClick={() => resume.mutate(undefined)}>Resume</Button>
                    : <Button variant="outline" icon={<Pause className="h-4 w-4" />} loading={pause.isPending} onClick={() => pause.mutate(undefined)}>Pause</Button>)}
                  <Toggle size="lg" label="Master automation" checked={on} disabled={master.isPending}
                    onChange={(v) => (v ? master.mutate(true) : setConfirmOff(true))} />
                </div>
              )}
            </div>
          </Card>

          <div className="mt-4 grid grid-cols-2 xl:grid-cols-5 gap-4">
            <StatCard label="Running" tone="accent" value={(o.active.running ?? 0) + (o.active.queued ?? 0)} sub="Running or queued" />
            <StatCard label="Completed · 24 h" tone="olive" value={o.jobs_24h.succeeded ?? 0} />
            <StatCard label="Failed · 24 h" tone={(o.jobs_24h.failed ?? 0) > 0 ? 'red' : 'slate'} value={o.jobs_24h.failed ?? 0} />
            <StatCard label="Retry scheduled" tone="amber" value={o.active.retry_scheduled ?? 0} />
            <StatCard label="Awaiting you" tone={(o.active.awaiting_user ?? 0) > 0 ? 'amber' : 'slate'} value={o.active.awaiting_user ?? 0}
              sub="OTP / re-authentication" />
          </div>
          {(o.active.awaiting_user ?? 0) > 0 && (
            <div className="mt-4"><Notice tone="warning" title="Manual intervention required" action={
              <Button size="sm" variant="outline" icon={<Hand className="h-4 w-4" />} onClick={() => setTab('intervention')}>Open queue</Button>}>
              {o.active.awaiting_user} job(s) are paused for bank verification. Automation never bypasses MFA, OTP or CAPTCHA.
            </Notice></div>
          )}

          <div className="mt-4 grid grid-cols-1 xl:grid-cols-5 gap-4">
            <Card className="xl:col-span-5">
              <CardHeader title="Schedule" icon={<CalendarClock className="h-4 w-4" />} subtitle="Persisted in the database — survives restarts without duplicate runs" />
              <ScheduleForm s={s} disabled={!manage} />
            </Card>
            <Card className="xl:col-span-5" padded={false}>
              <div className="p-5 pb-0"><CardHeader title="Account automation" subtitle="Per-account status, schedule, last result and controls. Healthy requires a verified sync." /></div>
              <DataTable rows={o.accounts} rowKey={(a) => a.id} empty={<EmptyState title="No accounts" />} columns={[
                { key: 'n', header: 'Account', mobile: 'title', cell: (a) => <div className="min-w-0"><div className="flex items-center gap-2">
                  <Link to={`/accounts/${a.id}`} className="font-medium text-ink truncate hover:underline">{a.nickname}</Link><DemoBadge show={a.is_demo} />
                  {a.environment === 'sandbox' && <StatusBadge status="sandbox" />}</div>
                  <div className="text-[12px] text-muted truncate">{a.institution} · {a.account_number_masked} · {METHOD_LABELS[a.connection_method ?? ''] ?? a.provider}</div></div> },
                { key: 'st', header: 'Automation', mobile: 'meta', cell: (a) => <div className="max-w-[240px]"><StatusBadge status={a.automation?.status ?? 'not_configured'} />
                  <div className="text-[11px] text-muted mt-0.5 line-clamp-2">{a.automation?.reason}</div></div> },
                { key: 'h', header: 'Connection', cell: (a) => <div className="flex flex-col gap-1 items-start"><StatusBadge status={a.connection_status ?? 'disconnected'} />
                  <StatusBadge status={a.health} /></div> },
                { key: 'ls', header: 'Last success', cell: (a) => <span className="text-[13px]">{a.last_successful_sync_at ? relative(a.last_successful_sync_at) : 'Never'}</span> },
                { key: 'la', header: 'Last attempt', cell: (a) => a.latest_job ? <button className="text-left" onClick={() => setParams({ job: a.latest_job!.id })}>
                  <StatusBadge status={a.latest_job.status} /><div className="text-[11px] text-muted mt-0.5">{relative(a.last_attempted_sync_at ?? a.latest_job.created_at)}</div></button>
                  : <span className="text-[13px] text-muted">Never run</span> },
                { key: 'nx', header: 'Next sync', cell: (a) => <span className="text-[13px]">{a.automation?.next_sync_at ? relative(a.automation.next_sync_at) : '—'}</span> },
                { key: 'e', header: 'Last error', cell: (a) => <span className="text-[12px] text-muted line-clamp-2 max-w-[200px]">{a.last_sync_error ?? '—'}</span> },
                { key: 'r', header: '', align: 'right', cell: (a) => {
                  const st = a.automation?.status ?? 'not_configured';
                  const connected = !['not_configured', 'provider_setup_required', 'authorization_required'].includes(st);
                  if (!manage) return null;
                  if (!connected) return <Link to={`/accounts/${a.id}?connect=1`} className="link text-[13px] whitespace-nowrap">Connect bank</Link>;
                  return <div className="flex justify-end gap-1.5">
                    {st !== 'waiting_for_user' && st !== 'running' && <Button size="sm" variant="ghost" icon={<Play className="h-3.5 w-3.5" />}
                      onClick={() => syncAcc.mutate(a.id)}>Sync</Button>}
                    {!a.automation_enabled ? <Button size="sm" variant="outline" onClick={() => accAction.mutate({ id: a.id, a: 'enable' })}>Enable</Button>
                      : a.automation_paused ? <Button size="sm" variant="outline" onClick={() => accAction.mutate({ id: a.id, a: 'resume' })}>Resume</Button>
                        : <Button size="sm" variant="outline" icon={<Pause className="h-3.5 w-3.5" />} onClick={() => accAction.mutate({ id: a.id, a: 'pause' })}>Pause</Button>}
                  </div>;
                } },
              ]} />
            </Card>
          </div>
        </>
      )}

      <Card className="mt-4" padded={false}>
        <div className="px-5 pt-4"><Tabs value={tab} onChange={(t) => { setTab(t); setPage(1); }} tabs={[
          { value: 'all', label: 'All jobs' }, { value: 'running', label: 'Running' }, { value: 'completed', label: 'Completed' },
          { value: 'failed', label: 'Failed', count: o?.jobs_24h.failed }, { value: 'intervention', label: 'Manual intervention', count: o?.active.awaiting_user }]} /></div>
        <DataTable columns={jobCols} rows={jobs.data?.items} rowKey={(j) => j.id} loading={jobs.isLoading} error={jobs.error}
          onRowClick={(j) => setParams({ job: j.id })}
          empty={<EmptyState title={tab === 'intervention' ? 'Nothing needs your attention' : 'No jobs here yet'}
            body={tab === 'all' ? 'Switch automation on or use “Run now” to start a collection.' : undefined} />} />
        {jobs.data && <Pagination page={page} pages={jobs.data.pages} total={jobs.data.total} pageSize={25} onPage={setPage} />}
      </Card>

      <JobDetailModal jobId={jobId} onClose={() => { params.delete('job'); setParams(params); }} />
      <ConfirmDialog open={confirmOff} onClose={() => setConfirmOff(false)} loading={master.isPending} onConfirm={() => master.mutate(false)}
        title="Switch automation off?" confirmLabel="Switch off"
        body="Scheduled jobs that have not started will be cancelled. Jobs already running finish safely. Manual runs remain available." />
    </>
  );
}
