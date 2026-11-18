import { useQuery } from '@tanstack/react-query';
import { History, Plus, Search, Upload, Wallet } from 'lucide-react';
import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Button, Card, DemoBadge, Input, Select, StatusBadge } from '@/components/ui/primitives';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { Notice, PageHeader } from '@/components/ui/layout';
import { EmptyState } from '@/components/ui/States';
import { Money } from '@/components/Money';
import { AddAccountModal } from '@/features/accounts/AddAccountModal';
import { AutomationActions, AutomationStatusBadge } from '@/features/accounts/AutomationControls';
import { ConnectionSetupModal } from '@/features/accounts/ConnectionSetupModal';
import { UploadStatementModal } from '@/features/statements/UploadStatementModal';
import { useDebounce } from '@/hooks/useDebounce';
import { accountsApi } from '@/services/api';
import { useAuth } from '@/stores/auth';
import type { Account } from '@/types/api';
import { relative } from '@/utils/format';
import { BALANCE_LABELS, METHOD_LABELS } from '@/utils/status';

function primaryBalance(a: Account) {
  for (const t of ['available', 'current', 'ledger', 'statement_closing'] as const) {
    if (a.balances[t]) return a.balances[t]!;
  }
  return null;
}

export default function Accounts() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState('');
  const [health, setHealth] = useState('');
  const [upload, setUpload] = useState(false);
  const [connect, setConnect] = useState<Account | null>(null);
  const navigate = useNavigate();
  const can = useAuth((s) => s.can);
  const dq = useDebounce(q);
  const list = useQuery({ queryKey: ['accounts', dq, health], queryFn: () => accountsApi.list({ q: dq, health }),
    refetchInterval: 15_000 });
  const needsAction = (list.data ?? []).filter((a) => a.automation?.action_required && a.automation.action_required !== 'enable');

  const cols: Column<Account>[] = [
    { key: 'name', header: 'Bank and account', mobile: 'title', cell: (a) => (
      <div className="min-w-0">
        <div className="flex items-center gap-2"><span className="font-medium text-ink truncate">{a.nickname}</span><DemoBadge show={a.is_demo} />
          {a.connection?.environment === 'sandbox' && <StatusBadge status="sandbox" />}</div>
        <div className="text-[12px] text-muted truncate">{a.institution.name} · {a.account_number_masked} · {a.account_type.replace('_', ' ')}</div>
      </div>) },
    { key: 'balance', header: 'Last known balance', align: 'right', mobile: 'meta', cell: (a) => {
      const b = primaryBalance(a);
      return b ? <div><Money value={b.amount} currency={b.currency} className="font-medium" />
        <div className="text-[11px] text-muted">{BALANCE_LABELS[b.balance_type]} · {relative(b.as_of)}</div></div>
        : <span className="text-muted text-[13px]">No balance yet</span>;
    } },
    { key: 'country', header: 'Country · currency', cell: (a) => <span className="text-[13px]">{a.country_code} · {a.currency}</span> },
    { key: 'method', header: 'Connection method', cell: (a) => <div className="flex flex-col gap-1 items-start">
      <span className="text-[13px] text-ink">{METHOD_LABELS[a.connection?.connection_method ?? ''] ?? a.connection?.provider_name ?? '—'}</span>
      <span className="text-[11px] text-muted">{a.connection?.provider_name}</span></div> },
    { key: 'health', header: 'Connection health', cell: (a) => <StatusBadge status={a.connection_health} /> },
    { key: 'auto', header: 'Automation', cell: (a) => <div className="max-w-[220px]"><AutomationStatusBadge view={a.automation} />
      {a.automation?.reason && <div className="mt-0.5 text-[11px] text-muted line-clamp-2">{a.automation.reason}</div>}</div> },
    { key: 'sync', header: 'Last sync', cell: (a) => <span className="text-[13px]">{a.last_successful_sync_at ? relative(a.last_successful_sync_at) : 'Never'}</span> },
    { key: 'next', header: 'Next sync', cell: (a) => <span className="text-[13px]">{a.automation?.next_sync_at ? relative(a.automation.next_sync_at) : '—'}</span> },
    { key: 'actions', header: '', align: 'right', cell: (a) => (
      <div className="flex flex-wrap justify-end gap-2" onClick={(e) => e.stopPropagation()}>
        <AutomationActions account={a} onConnect={() => setConnect(a)} />
        <Button size="sm" variant="ghost" icon={<History className="h-3.5 w-3.5" />} onClick={() => navigate(`/accounts/${a.id}#history`)}>History</Button>
      </div>) },
  ];

  return (
    <>
      <PageHeader eyebrow="Banking" title="Bank accounts" subtitle="Each account's connection method, verified health and automation status at a glance. Full account numbers are never displayed."
        actions={<>
          {can('statements:write') && <Button variant="outline" icon={<Upload className="h-4 w-4" />} onClick={() => setUpload(true)}>Upload statement (fallback)</Button>}
          {can('accounts:write') && <Button icon={<Plus className="h-4 w-4" />} onClick={() => setParams({ add: '1' })}>Add account</Button>}
        </>} />
      {needsAction.length > 0 && (
        <div className="mb-4"><Notice tone="warning" title={`${needsAction.length} account(s) need your action before they can sync automatically`}>
          {needsAction.slice(0, 3).map((a) => `${a.nickname}: ${a.automation?.reason}`).join(' · ')}
        </Notice></div>
      )}
      <Card padded={false}>
        <div className="flex flex-col sm:flex-row gap-3 p-4 border-b border-line">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted" />
            <Input aria-label="Search accounts" placeholder="Search by name…" value={q} onChange={(e) => setQ(e.target.value)} className="pl-9" />
          </div>
          <Select aria-label="Filter by health" value={health} onChange={(e) => setHealth(e.target.value)} className="sm:w-56">
            <option value="">All health states</option><option value="healthy">Healthy</option><option value="degraded">Degraded</option>
            <option value="failing">Failing</option><option value="unknown">Not verified yet</option>
            <option value="not_applicable">No live connection (manual)</option>
          </Select>
        </div>
        <DataTable columns={cols} rows={list.data} rowKey={(a) => a.id} loading={list.isLoading} error={list.error}
          onRetry={() => list.refetch()} onRowClick={(a) => navigate(`/accounts/${a.id}`)}
          empty={<EmptyState icon={<Wallet className="h-6 w-6" />} title={dq || health ? 'No matching accounts' : 'No bank accounts yet'}
            body="Add an account, authorise its connection once and let automation collect balances and transactions."
            action={can('accounts:write') && <Button icon={<Plus className="h-4 w-4" />} onClick={() => setParams({ add: '1' })}>Add account</Button>} />} />
      </Card>
      <AddAccountModal open={params.get('add') === '1'} onClose={() => setParams({})}
        onCreated={(a) => navigate(`/accounts/${a.id}${a.automation?.action_required ? '?connect=1' : ''}`)} />
      <UploadStatementModal open={upload} onClose={() => setUpload(false)} />
      {connect && <ConnectionSetupModal account={connect} open={!!connect} onClose={() => setConnect(null)} />}
    </>
  );
}
