import { useQuery } from '@tanstack/react-query';
import { ScrollText, Search } from 'lucide-react';
import { useState } from 'react';
import { Badge, Card, Input, Select, StatusBadge } from '@/components/ui/primitives';
import { DataTable, Pagination } from '@/components/ui/DataTable';
import { Modal } from '@/components/ui/Modal';
import { KeyValue, Notice, PageHeader } from '@/components/ui/layout';
import { EmptyState } from '@/components/ui/States';
import { useDebounce } from '@/hooks/useDebounce';
import { auditApi } from '@/services/api';
import type { AuditEntry } from '@/types/api';
import { dateTime } from '@/utils/format';

export default function AuditLogs() {
  const [page, setPage] = useState(1);
  const [action, setAction] = useState('');
  const [outcome, setOutcome] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [open, setOpen] = useState<AuditEntry | null>(null);
  const da = useDebounce(action);
  const list = useQuery({ queryKey: ['audit', page, da, outcome, from, to],
    queryFn: () => auditApi.list({ page, page_size: 50, action: da, outcome, date_from: from, date_to: to }) });

  return (
    <>
      <PageHeader eyebrow="Governance" title="Audit logs" subtitle="An append-only record of every sign-in, configuration change, automation action and manual decision." />
      <Notice tone="info">Audit entries are immutable — the database rejects any update or deletion. Secrets, passcodes and full account numbers are redacted before storage.</Notice>
      <Card className="mt-4" padded={false}>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 p-4 border-b border-line">
          <div className="relative"><Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted" />
            <Input aria-label="Action" placeholder="Action prefix e.g. auth." className="pl-9" value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }} /></div>
          <Select aria-label="Outcome" value={outcome} onChange={(e) => { setOutcome(e.target.value); setPage(1); }}>
            <option value="">All outcomes</option><option value="success">Success</option><option value="failure">Failure</option></Select>
          <Input aria-label="From date" type="date" value={from} onChange={(e) => { setFrom(e.target.value); setPage(1); }} />
          <Input aria-label="To date" type="date" value={to} onChange={(e) => { setTo(e.target.value); setPage(1); }} />
        </div>
        <DataTable rows={list.data?.items} rowKey={(a) => a.id} loading={list.isLoading} error={list.error} onRowClick={setOpen}
          empty={<EmptyState icon={<ScrollText className="h-6 w-6" />} title="No audit entries match" />} columns={[
            { key: 'ts', header: 'Time', cell: (a) => <span className="text-[13px] whitespace-nowrap">{dateTime(a.ts)}</span> },
            { key: 'action', header: 'Action', mobile: 'title', cell: (a) => <span className="font-mono text-[12.5px] text-ink">{a.action}</span> },
            { key: 'actor', header: 'Actor', cell: (a) => <span className="text-[13px]">{a.actor_label ?? (a.actor_type === 'user' ? a.actor_user_id?.slice(0, 8) : a.actor_type)}</span> },
            { key: 'entity', header: 'Entity', cell: (a) => a.entity_type ? <Badge>{a.entity_type}</Badge> : <span className="text-muted">—</span> },
            { key: 'outcome', header: 'Outcome', mobile: 'meta', cell: (a) => <StatusBadge status={a.outcome} /> },
            { key: 'ip', header: 'IP', cell: (a) => <span className="font-mono text-[12px] text-muted">{a.ip_address ?? '—'}</span> },
          ]} />
        {list.data && <Pagination page={page} pages={list.data.pages} total={list.data.total} pageSize={50} onPage={setPage} />}
      </Card>
      <Modal open={!!open} onClose={() => setOpen(null)} size="lg" title={open?.action ?? ''} description={open ? dateTime(open.ts) : undefined}>
        {open && <div className="space-y-4">
          <KeyValue items={[{ label: 'Actor', value: open.actor_label ?? open.actor_type }, { label: 'Outcome', value: <StatusBadge status={open.outcome} /> },
            { label: 'Entity', value: `${open.entity_type ?? '—'} ${open.entity_id ?? ''}` }, { label: 'IP address', value: open.ip_address ?? '—' }]} />
          {(['before', 'after', 'meta'] as const).map((k) => open[k] && Object.keys(open[k]!).length > 0 && (
            <div key={k}><p className="label capitalize">{k}</p><pre className="rounded-xl bg-surface-2 p-3 text-[12px] font-mono overflow-x-auto">{JSON.stringify(open[k], null, 2)}</pre></div>))}
        </div>}
      </Modal>
    </>
  );
}
