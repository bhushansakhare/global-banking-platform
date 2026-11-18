import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Card, CardHeader, Select } from '@/components/ui/primitives';
import { Notice, PageHeader } from '@/components/ui/layout';
import { EmptyState } from '@/components/ui/States';
import { BalanceArea, Donut, FlowBars, HBars, RateLine } from '@/features/charts/Charts';
import { analyticsApi } from '@/services/api';
import { money } from '@/utils/format';

export default function Analytics() {
  const [days, setDays] = useState(90);
  const [flowDays, setFlowDays] = useState(30);
  const hist = useQuery({ queryKey: ['analytics', 'hist', days], queryFn: () => analyticsApi.balanceHistory(days) });
  const flow = useQuery({ queryKey: ['analytics', 'flow', flowDays], queryFn: () => analyticsApi.cashFlow(flowDays) });
  const monthly = useQuery({ queryKey: ['analytics', 'monthly', 24], queryFn: () => analyticsApi.monthly(24) });
  const dist = useQuery({ queryKey: ['analytics', 'dist'], queryFn: analyticsApi.distributions });
  const perf = useQuery({ queryKey: ['analytics', 'perf', 26], queryFn: () => analyticsApi.reconPerformance(26) });
  const ccy = dist.data?.reporting_currency ?? hist.data?.reporting_currency ?? 'GBP';
  const flows = flow.data?.series ?? [];
  const inflow = flows.reduce((a, d) => a + (d.inflow ?? 0), 0);
  const outflow = flows.reduce((a, d) => a + (d.outflow ?? 0), 0);
  const missing = [...new Set([...(dist.data?.fx_missing ?? []), ...(hist.data?.fx_missing ?? [])])];

  return (
    <>
      <PageHeader eyebrow="Insight" title="Cash-flow analytics" subtitle={`All figures consolidated in ${ccy} using recorded exchange rates.`} />
      {missing.length > 0 && <div className="mb-4"><Notice tone="warning">No exchange rate for {missing.join(', ')} — those amounts are excluded from consolidated charts.</Notice></div>}
      <Card>
        <CardHeader title="Consolidated balance" subtitle={hist.data?.basis}
          action={<Select aria-label="Range" value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-32 h-9">
            {[30, 90, 180, 365].map((d) => <option key={d} value={d}>{d} days</option>)}</Select>} />
        {hist.data ? <BalanceArea data={hist.data.series} currency={ccy} height={300} /> : <div className="h-[300px]" />}
      </Card>
      <div className="mt-4 grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2">
          <CardHeader title="Cash inflow vs outflow" subtitle={`Net ${money(inflow - outflow, ccy, { sign: true })} over ${flowDays} days`}
            action={<Select aria-label="Range" value={flowDays} onChange={(e) => setFlowDays(Number(e.target.value))} className="w-32 h-9">
              {[14, 30, 60, 90].map((d) => <option key={d} value={d}>{d} days</option>)}</Select>} />
          <FlowBars data={flows} currency={ccy} height={280} />
        </Card>
        <Card>
          <CardHeader title="Flow summary" />
          <dl className="space-y-4">
            {[['Money in', inflow, 'text-olive-700 dark:text-olive-400'], ['Money out', outflow, 'text-ink'], ['Net movement', inflow - outflow, 'text-ink']].map(([l, v, c]) => (
              <div key={l as string} className="rounded-xl bg-surface-2 p-4"><dt className="text-[12px] text-muted">{l}</dt>
                <dd className={`mt-1 text-xl font-semibold num ${c}`}>{money(v as number, ccy)}</dd></div>
            ))}
            <div className="rounded-xl bg-surface-2 p-4"><dt className="text-[12px] text-muted">Average daily outflow</dt>
              <dd className="mt-1 text-xl font-semibold num text-ink">{money(flows.length ? outflow / flows.length : 0, ccy)}</dd></div>
          </dl>
        </Card>
      </div>
      <Card className="mt-4">
        <CardHeader title="Monthly activity" subtitle="24 months of credits and debits" />
        {monthly.data?.series.length ? <FlowBars data={monthly.data.series} currency={ccy} xKey="month" inKey="credits" outKey="debits" height={300} />
          : <EmptyState title="No monthly activity yet" />}
      </Card>
      <div className="mt-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card><CardHeader title="Bank-wise distribution" />{dist.data?.banks.length ? <Donut data={dist.data.banks} currency={ccy} /> : <EmptyState title="No data" />}</Card>
        <Card><CardHeader title="Currency exposure" subtitle={`Converted to ${ccy}`} />
          {dist.data?.currencies.length ? <Donut data={dist.data.currencies.filter((c) => c.converted !== null).map((c) => ({ name: c.currency, value: c.converted! }))} currency={ccy} />
            : <EmptyState title="No data" />}</Card>
        <Card><CardHeader title="Debits by category" subtitle="Last 30 days" />
          {dist.data?.categories.length ? <HBars data={dist.data.categories.filter((c) => c.debits > 0).map((c) => ({ name: c.name, value: c.debits, colour: c.colour }))} currency={ccy} height={320} />
            : <EmptyState title="No data" />}</Card>
        <Card><CardHeader title="Credits by category" subtitle="Last 30 days" />
          {dist.data?.categories.length ? <HBars data={dist.data.categories.filter((c) => c.credits > 0).map((c) => ({ name: c.name, value: c.credits, colour: c.colour }))} currency={ccy} height={320} />
            : <EmptyState title="No data" />}</Card>
      </div>
      <Card className="mt-4"><CardHeader title="Reconciliation performance" subtitle="26 weeks" />
        {perf.data?.series.length ? <RateLine data={perf.data.series} height={260} /> : <EmptyState title="No runs yet" />}</Card>
    </>
  );
}
