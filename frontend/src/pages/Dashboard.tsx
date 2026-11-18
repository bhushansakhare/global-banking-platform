import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, ArrowDownLeft, ArrowUpRight, Banknote, Building2, CheckCircle2, FileClock, GitCompareArrows,
  Landmark, Play, Sparkles, Wallet, Workflow } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { Badge, Button, Card, CardHeader, StatusBadge } from '@/components/ui/primitives';
import { Notice, PageHeader, StatCard } from '@/components/ui/layout';
import { CardsSkeleton, EmptyState, ErrorState } from '@/components/ui/States';
import { BalanceArea, Donut, FlowBars, HBars, RateLine } from '@/features/charts/Charts';
import { useAction } from '@/hooks/useMutationToast';
import { analyticsApi, automationApi, settingsApi } from '@/services/api';
import { useAuth } from '@/stores/auth';
import { dateTime, money, num, relative } from '@/utils/format';

export default function Dashboard() {
  const navigate = useNavigate();
  const can = useAuth((s) => s.can);
  const summary = useQuery({ queryKey: ['analytics', 'summary'], queryFn: analyticsApi.summary, refetchInterval: 60_000 });
  const hist = useQuery({ queryKey: ['analytics', 'hist', 90], queryFn: () => analyticsApi.balanceHistory(90) });
  const flow = useQuery({ queryKey: ['analytics', 'flow', 30], queryFn: () => analyticsApi.cashFlow(30) });
  const monthly = useQuery({ queryKey: ['analytics', 'monthly'], queryFn: () => analyticsApi.monthly(12) });
  const dist = useQuery({ queryKey: ['analytics', 'dist'], queryFn: analyticsApi.distributions });
  const perf = useQuery({ queryKey: ['analytics', 'perf'], queryFn: () => analyticsApi.reconPerformance(12) });
  const runNow = useAction(() => automationApi.runNow(), { success: (r) => `Collection started for ${r.jobs_created} account(s)`,
    invalidate: [['automation'], ['jobs']] });
  const seed = useAction(settingsApi.seedDemo, { success: (r) => `${r.accounts_created} demo accounts created`,
    invalidate: [['analytics'], ['accounts']] });

  if (summary.isError) return <Card><ErrorState error={summary.error} onRetry={() => summary.refetch()} /></Card>;
  const s = summary.data;
  const ccy = s?.reporting_currency ?? 'GBP';

  if (s && s.total_accounts === 0) {
    return (
      <>
        <PageHeader title="Welcome to Meridian" subtitle="Connect your bank accounts once, switch automation on, and let the platform handle collection, extraction and reconciliation every day." />
        <Card>
          <EmptyState icon={<Landmark className="h-6 w-6" />} title="No bank accounts yet"
            body="Add an account using a supported provider or statement upload. You can also explore with a clearly labelled demo environment — demo data is never shown as real bank data."
            action={<div className="flex flex-wrap justify-center gap-2">
              {can('accounts:write') && <Button onClick={() => navigate('/accounts?add=1')} icon={<Wallet className="h-4 w-4" />}>Add bank account</Button>}
              {can('settings:manage') && <Button variant="outline" loading={seed.isPending} onClick={() => seed.mutate(undefined)}
                icon={<Sparkles className="h-4 w-4" />}>Load demo environment</Button>}
            </div>} />
        </Card>
      </>
    );
  }

  const reported = s?.reported_balance.total_reporting;
  const unconv = s ? [...new Set([...s.reported_balance.unconverted_currencies, ...s.fx_missing])] : [];
  const jobs = s?.automation.jobs_24h ?? {};
  const recon = s?.reconciliation;
  const totalTx = Object.values(recon?.by_status ?? {}).reduce((a, b) => a + b, 0);
  const matchedTx = (recon?.by_status.matched ?? 0) + (recon?.by_status.manual ?? 0);

  return (
    <>
      <PageHeader eyebrow="Overview" title="Dashboard"
        subtitle={s ? `Consolidated view in ${ccy}. Balances are labelled by type and source; conversions use recorded exchange rates.` : undefined}
        actions={can('automation:manage') && <Button onClick={() => runNow.mutate(undefined)} loading={runNow.isPending}
          icon={<Play className="h-4 w-4" />}>Run collection now</Button>} />

      {s && s.demo_accounts > 0 && (
        <div className="mb-5"><Notice tone="demo" title="Demo data present">
          {s.demo_accounts} of {s.total_accounts} account(s) use the Demo Bank Simulator. Demo figures are synthetic and flagged throughout.
        </Notice></div>
      )}
      {unconv.length > 0 && (
        <div className="mb-5"><Notice tone="warning" title="Exchange rates missing">
          No rate to {ccy} for {unconv.join(', ')}. Those balances are excluded from consolidated totals — add rates in Balances.
        </Notice></div>
      )}

      {!s ? <CardsSkeleton n={4} /> : (
        <div className="grid grid-cols-1 xs:grid-cols-2 xl:grid-cols-4 gap-4">
          <StatCard label="Combined reported balance" value={money(reported, ccy)} icon={<Banknote className="h-4 w-4" />}
            sub={`${s.reported_balance.accounts} account(s) · best available per account`} />
          <StatCard label="Available balance" tone="olive" icon={<Wallet className="h-4 w-4" />}
            value={s.balances.available.total_reporting !== null ? money(s.balances.available.total_reporting, ccy) : '—'}
            sub={s.balances.available.accounts ? `${s.balances.available.accounts} account(s) report available` : 'Not reported by providers'} />
          <StatCard label="Ledger balance" tone="slate" icon={<Building2 className="h-4 w-4" />}
            value={s.balances.ledger.total_reporting !== null ? money(s.balances.ledger.total_reporting, ccy) : '—'}
            sub={s.balances.ledger.oldest_as_of ? `Oldest snapshot ${relative(s.balances.ledger.oldest_as_of)}` : 'No ledger balances yet'} />
          <StatCard label="Connected banks" tone="accent" icon={<Landmark className="h-4 w-4" />}
            value={`${s.connected_banks}`} sub={`${s.total_accounts} account(s) · ${s.live_accounts} live, ${s.demo_accounts} demo`} />
          <StatCard label="Credits · 30 days" tone="olive" icon={<ArrowDownLeft className="h-4 w-4" />} value={money(s.credits_30d, ccy)}
            sub={`${num(s.transactions_30d)} transactions`} />
          <StatCard label="Debits · 30 days" tone="brand" icon={<ArrowUpRight className="h-4 w-4" />} value={money(s.debits_30d, ccy)}
            sub={`Net ${money((s.credits_30d ?? 0) - (s.debits_30d ?? 0), ccy, { sign: true })}`} />
          <StatCard label="Unmatched transactions" tone={recon && recon.unmatched > 0 ? 'amber' : 'olive'}
            icon={<GitCompareArrows className="h-4 w-4" />} value={num(recon?.unmatched)}
            sub={`${num(recon?.suggested)} suggested · ${totalTx ? Math.round((matchedTx / totalTx) * 100) : 0}% matched overall`}
            footer={<Link to="/reconciliation" className="link">Review reconciliation →</Link>} />
          <StatCard label="Pending statements" tone={s.pending_statements ? 'amber' : 'slate'} icon={<FileClock className="h-4 w-4" />}
            value={num(s.pending_statements)} sub="Awaiting processing or review"
            footer={<Link to="/statements" className="link">Open statements →</Link>} />
        </div>
      )}

      <div className="mt-6 grid grid-cols-1 xl:grid-cols-3 gap-4">
        <Card className="xl:col-span-2">
          <CardHeader title="Balance history" subtitle={hist.data?.basis ?? 'End-of-day balances, last 90 days'} />
          {hist.data ? <BalanceArea data={hist.data.series} currency={ccy} /> : <div className="h-[260px]" />}
        </Card>
        <Card>
          <CardHeader title="Automation" icon={<Workflow className="h-4 w-4" />}
            action={s && <Badge tone={s.automation.master_enabled ? (s.automation.paused ? 'warning' : 'success') : 'neutral'} dot>
              {s.automation.master_enabled ? (s.automation.paused ? 'Paused' : 'On') : 'Off'}</Badge>} />
          {s && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div className="rounded-xl bg-surface-2 p-3"><p className="text-[12px] text-muted">Next run</p>
                  <p className="font-medium text-ink mt-0.5">{s.automation.next_run_at ? dateTime(s.automation.next_run_at) : '—'}</p></div>
                <div className="rounded-xl bg-surface-2 p-3"><p className="text-[12px] text-muted">Last run</p>
                  <p className="font-medium text-ink mt-0.5">{relative(s.automation.last_run_at)}</p></div>
              </div>
              <div>
                <p className="text-[12px] text-muted mb-2">Jobs in the last 24 hours</p>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(jobs).length ? Object.entries(jobs).map(([k, v]) => (
                    <span key={k} className="inline-flex items-center gap-1.5"><StatusBadge status={k} /><span className="text-sm num text-ink">{v}</span></span>
                  )) : <span className="text-sm text-muted">No jobs yet</span>}
                </div>
              </div>
              {s.failed_connections.length > 0 ? (
                <div className="space-y-2">
                  <p className="text-[12px] text-muted">Connections needing attention</p>
                  {s.failed_connections.slice(0, 4).map((c) => (
                    <div key={c.id} className="flex items-center gap-2 text-sm">
                      <AlertTriangle className="h-4 w-4 text-amber-500 shrink-0" /><span className="truncate text-ink">{c.institution}</span>
                      <span className="ml-auto"><StatusBadge status={c.status} /></span>
                    </div>
                  ))}
                </div>
              ) : (s.account_automation?.healthy ?? 0) > 0 && Object.keys(s.account_automation ?? {}).every((k) => ['healthy', 'scheduled', 'running'].includes(k))
                ? <p className="flex items-center gap-2 text-sm text-olive-700 dark:text-olive-400"><CheckCircle2 className="h-4 w-4" />All automated accounts syncing</p>
                : null}
              {s.account_automation && Object.keys(s.account_automation).length > 0 && (
                <div>
                  <p className="text-[12px] text-muted mb-2">Accounts by automation status</p>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(s.account_automation).map(([k, v]) => (
                      <span key={k} className="inline-flex items-center gap-1.5"><StatusBadge status={k} /><span className="text-sm num text-ink">{v}</span></span>
                    ))}
                  </div>
                </div>
              )}
              <Link to="/automation" className="link text-sm">Open Automation Center →</Link>
            </div>
          )}
        </Card>
      </div>

      <div className="mt-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <CardHeader title="Cash inflow and outflow" subtitle={`Daily, last 30 days · ${ccy}`} />
          {flow.data ? <FlowBars data={flow.data.series} currency={ccy} /> : <div className="h-[260px]" />}
        </Card>
        <Card>
          <CardHeader title="Monthly banking activity" subtitle={`Credits and debits, 12 months · ${ccy}`} />
          {monthly.data ? <FlowBars data={monthly.data.series} currency={ccy} xKey="month" inKey="credits" outKey="debits" />
            : <div className="h-[260px]" />}
        </Card>
      </div>

      <div className="mt-4 grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card>
          <CardHeader title="Balance by bank" subtitle={`Converted to ${ccy}`} />
          {dist.data && dist.data.banks.length ? <Donut data={dist.data.banks} currency={ccy} /> : <EmptyState title="No balances yet" />}
        </Card>
        <Card>
          <CardHeader title="Balance by currency" subtitle="Original currency amounts" />
          {dist.data && dist.data.currencies.length ? (
            <ul className="space-y-3">
              {dist.data.currencies.map((c) => (
                <li key={c.currency} className="flex items-center gap-3">
                  <span className="flex h-9 w-12 items-center justify-center rounded-lg bg-surface-2 text-[12px] font-semibold text-ink">{c.currency}</span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-ink num">{money(c.original, c.currency)}</p>
                    <p className="text-[12px] text-muted num">{c.converted !== null ? `≈ ${money(c.converted, ccy)}` : 'No exchange rate'}</p>
                  </div>
                </li>
              ))}
            </ul>
          ) : <EmptyState title="No balances yet" />}
        </Card>
        <Card>
          <CardHeader title="Spending by category" subtitle={`Debits, last 30 days · ${ccy}`} />
          {dist.data && dist.data.categories.length
            ? <HBars data={dist.data.categories.filter((c) => c.debits > 0).slice(0, 7).map((c) => ({ name: c.name, value: c.debits, colour: c.colour }))} currency={ccy} />
            : <EmptyState title="No categorised spending" />}
        </Card>
      </div>

      <div className="mt-4">
        <Card>
          <CardHeader title="Reconciliation performance" subtitle="Weekly confirmed-match rate and exceptions" />
          {perf.data && perf.data.series.length ? <RateLine data={perf.data.series} />
            : <EmptyState title="No reconciliation runs yet" body="Runs appear here once statements have been reconciled." />}
        </Card>
      </div>
    </>
  );
}
