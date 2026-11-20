import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ArrowDownToLine, GitCompareArrows, KeyRound, Send } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Badge, Button, Card, CardHeader } from '@/components/ui/primitives';
import { Notice, PageHeader, StatCard, Tabs } from '@/components/ui/layout';
import { ErrorState, CardsSkeleton } from '@/components/ui/States';
import { GatewayPanel } from '@/features/finzola/GatewayPanel';
import { MatchPanel } from '@/features/finzola/MatchPanel';
import { OutboundPanel } from '@/features/finzola/OutboundPanel';
import { finzolaApi } from '@/features/finzola/api';
import { useAuth } from '@/stores/auth';

type Tab = 'start' | 'import' | 'match' | 'send';
export default function Finzola() {
  const can = useAuth(s => s.can);
  const read = can('settings:read') && can('accounts:read');
  const edit = can('settings:manage');
  const [tab, setTab] = useState<Tab>('start');
  const [itemPage, setItemPage] = useState(1);
  const [deliveryPage, setDeliveryPage] = useState(1);
  const config = useQuery({ queryKey: ['finzola', 'config'], queryFn: finzolaApi.config, enabled: read, refetchInterval: 5000 });
  const pipeline = useQuery({ queryKey: ['finzola', 'pipeline', itemPage], queryFn: () => finzolaApi.pipeline(itemPage), enabled: read, refetchInterval: 5000 });
  const outbound = useQuery({ queryKey: ['finzola', 'outbound', deliveryPage], queryFn: () => finzolaApi.outbound(deliveryPage), enabled: read, refetchInterval: 5000 });
  if (!read) return <Notice>You need integration settings and account read access to view Finzola.</Notice>;
  const error = config.error || pipeline.error || outbound.error;
  if (error) return <Card><ErrorState error={error} onRetry={() => { config.refetch(); pipeline.refetch(); outbound.refetch(); }} /></Card>;
  if (!config.data || !pipeline.data || !outbound.data) return <CardsSkeleton />;
  const cfg = config.data, pipe = pipeline.data, out = outbound.data;
  const steps = [
    { label: 'Connect the gateway', description: 'Save your gateway key and select the domains you are allowed to read.', icon: KeyRound, done: cfg.configured, tab: 'import' as Tab },
    { label: 'Import expenses', description: 'Fetch once or set 5, 15 or 60 minute polling. Review freshness and errors.', icon: ArrowDownToLine, done: !!cfg.state.expenses?.last_success && !cfg.state.expenses.error, tab: 'import' as Tab },
    { label: 'Match bank payments', description: 'Map external organizations to local accounts, then review genuine statement evidence.', icon: GitCompareArrows, done: pipe.mappings.some(m => m.enabled), tab: 'match' as Tab },
    { label: 'Send to Finzola', description: 'Verify each organization destination. Enable automatic results or preview and send a bank debit.', icon: Send, done: out.destinations.some(d => d.verified), tab: 'send' as Tab },
  ];
  return <>
    <PageHeader eyebrow="Integrations" title="Finzola workspace" subtitle="Bring approved expenses into your banking workflow, compare them with reviewed bank records, and track acknowledged delivery back to Finzola." />
    <div className="mb-5 grid grid-cols-2 gap-4 xl:grid-cols-4">
      <StatCard label="Gateway" value={cfg.configured ? 'Key saved' : 'Not configured'} tone={cfg.configured ? 'brand' : 'slate'} />
      <StatCard label="Enabled account routes" value={pipe.mappings.filter(m => m.enabled).length} tone="accent" />
      <StatCard label="Expected payments" value={pipe.total} tone="slate" />
      <StatCard label="Verified destinations" value={out.destinations.filter(d => d.verified).length} tone="olive" />
    </div>
    <div className="mb-5"><Tabs value={tab} onChange={setTab} tabs={[{ value: 'start', label: 'Start here' }, { value: 'import', label: 'Import expenses' }, { value: 'match', label: 'Match bank payments' }, { value: 'send', label: 'Send to Finzola' }]} /></div>
    {tab === 'start' && <div className="space-y-4"><Card><CardHeader title="Set up the complete flow" subtitle="Gateway access and webhook delivery use different keys. Nothing is sent externally until you configure and verify a destination and authorize a send or automatic results." /><div className="grid gap-4 md:grid-cols-2">{steps.map(step => <div key={step.label} className="rounded-xl border border-line p-5"><div className="mb-3 flex items-center gap-3"><step.icon className="h-5 w-5 text-brand-600" /><h3 className="font-medium">{step.label}</h3><Badge tone={step.done ? 'success' : 'neutral'}>{step.done ? 'Set up' : 'Next step'}</Badge></div><p className="mb-4 text-sm text-muted">{step.description}</p><Button variant="outline" onClick={() => setTab(step.tab)}>Open {step.label.toLowerCase()}</Button></div>)}</div></Card><Notice>Finzola approval or “paid” status is not proof of a bank payment. This workspace imports expectations and comparison results; it never moves money or approves expenses. <Link className="link" to="/statements">Manage bank statements</Link></Notice></div>}
    {tab === 'import' && <GatewayPanel config={cfg} editable={edit} />}
    {tab === 'match' && <MatchPanel pipeline={pipe} editable={edit && can('reconciliation:write')} page={itemPage} setPage={setItemPage} />}
    {tab === 'send' && <OutboundPanel outbound={out} editable={edit && can('transactions:read')} page={deliveryPage} setPage={setDeliveryPage} />}
    <Card className="mt-5"><CardHeader title="Recent integration activity" subtitle="Pending, failed and completed operations stay visible. A completed fetch with partial failures is shown in domain freshness above." /><div className="space-y-2">{pipe.jobs.map(job => <div key={job.id} className="flex flex-wrap items-start justify-between gap-2 rounded-lg border border-line p-3"><div><p className="text-sm font-medium">{job.type.replace('finzola_', '').replace(/_/g, ' ')}</p><p className="text-xs text-muted">{job.error || job.progress || (job.result.partial_failure ? 'Partial domain failure; inspect freshness' : '')}</p></div><Badge tone={job.status === 'failed' ? 'warning' : job.status === 'succeeded' ? 'info' : 'neutral'}>{job.status.replace(/_/g, ' ')}</Badge></div>)}{!pipe.jobs.length && <p className="text-sm text-muted">No Finzola jobs yet. Start by configuring the gateway.</p>}</div></Card>
  </>;
}
