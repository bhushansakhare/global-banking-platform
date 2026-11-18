import { useQuery } from '@tanstack/react-query';
import { ExternalLink, Globe2, Landmark, Plus, Search } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Badge, Button, Card, CardHeader, DemoBadge, Field, Input, Select, StatusBadge } from '@/components/ui/primitives';
import { DataTable, Pagination, type Column } from '@/components/ui/DataTable';
import { Modal } from '@/components/ui/Modal';
import { Notice, PageHeader, Tabs } from '@/components/ui/layout';
import { EmptyState } from '@/components/ui/States';
import { useDebounce } from '@/hooks/useDebounce';
import { errorText, useAction } from '@/hooks/useMutationToast';
import { institutionsApi } from '@/services/api';
import { useAuth } from '@/stores/auth';
import type { Institution, Provider } from '@/types/api';

function AddInstitution({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [f, setF] = useState({ name: '', country_code: 'GB', bic: '' });
  const countries = useQuery({ queryKey: ['countries'], queryFn: institutionsApi.countries, enabled: open });
  const add = useAction(() => institutionsApi.create({ name: f.name, country_code: f.country_code, bic: f.bic || undefined }),
    { success: 'Institution added', invalidate: [['institutions']], onSuccess: onClose });
  const submit = (e: FormEvent) => { e.preventDefault(); add.mutate(undefined); };
  return (
    <Modal open={open} onClose={onClose} title="Add institution (statement upload)"
      description="Any bank worldwide can be added. Data arrives through official statement exports you upload."
      footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button type="submit" form="add-inst" loading={add.isPending}
        disabled={f.name.trim().length < 2}>Add institution</Button></>}>
      <form id="add-inst" onSubmit={submit} className="space-y-4">
        {add.error && <Notice tone="danger">{errorText(add.error)}</Notice>}
        <Field label="Bank name" htmlFor="i-name" required><Input id="i-name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <div className="grid grid-cols-1 xs:grid-cols-2 gap-4">
          <Field label="Country" htmlFor="i-cc"><Select id="i-cc" value={f.country_code} onChange={(e) => setF({ ...f, country_code: e.target.value })}>
            {countries.data?.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}</Select></Field>
          <Field label="BIC / SWIFT" htmlFor="i-bic" hint="Optional"><Input id="i-bic" value={f.bic} maxLength={11}
            onChange={(e) => setF({ ...f, bic: e.target.value.toUpperCase() })} /></Field>
        </div>
      </form>
    </Modal>
  );
}

function ProviderCard({ p }: { p: Provider }) {
  return (
    <Card className="flex flex-col">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0"><h3 className="font-semibold text-ink">{p.name}</h3>
          <p className="text-[12px] text-muted capitalize">{p.provider_type.replace('_', ' ')} · {p.auth_method.replace(/_/g, ' ')}</p></div>
        <StatusBadge status={p.integration_status} />
      </div>
      <p className="mt-3 text-[13px] text-muted leading-relaxed flex-1">{p.notes}</p>
      <div className="mt-4 space-y-2 text-[12.5px]">
        <div className="flex flex-wrap gap-1.5">{p.capabilities.map((c) => <Badge key={c}>{c}</Badge>)}</div>
        <p className="text-muted"><Globe2 className="inline h-3.5 w-3.5 mr-1" />{p.countries[0] === '*' ? 'All countries' : p.countries.join(', ') || 'Per bank'}</p>
        <p className="text-muted">Refresh: {p.refresh_frequency} · Consent: {p.consent_required ? 'required' : 'not required'}</p>
        <div className="flex items-center justify-between pt-2">
          <Badge tone={p.configured ? 'success' : 'neutral'} dot>{p.configured ? 'Configured' : 'Not configured'}</Badge>
          {p.documentation_url && <a href={p.documentation_url} target="_blank" rel="noreferrer noopener" className="link inline-flex items-center gap-1">
            Documentation<ExternalLink className="h-3 w-3" /></a>}
        </div>
      </div>
    </Card>
  );
}

export default function Institutions() {
  const [tab, setTab] = useState<'institutions' | 'providers'>('institutions');
  const [country, setCountry] = useState('');
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [add, setAdd] = useState(false);
  const can = useAuth((s) => s.can);
  const dq = useDebounce(q);
  const countries = useQuery({ queryKey: ['countries'], queryFn: institutionsApi.countries });
  const list = useQuery({ queryKey: ['institutions', country, dq, page], queryFn: () => institutionsApi.list({ country, q: dq, page, page_size: 25 }) });
  const providers = useQuery({ queryKey: ['providers'], queryFn: institutionsApi.providers, enabled: tab === 'providers' });

  const cols: Column<Institution>[] = [
    { key: 'name', header: 'Institution', mobile: 'title', cell: (i) => <div className="flex items-center gap-3 min-w-0">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-700 dark:bg-brand-900/40 dark:text-brand-200 text-[12px] font-semibold">
        {i.name.slice(0, 2).toUpperCase()}</span>
      <div className="min-w-0"><div className="flex items-center gap-2"><span className="font-medium text-ink truncate">{i.name}</span><DemoBadge show={i.is_demo} /></div>
        <div className="text-[12px] text-muted">{i.bic ?? '—'}{i.is_custom ? ' · custom' : ''}</div></div></div> },
    { key: 'country', header: 'Country', mobile: 'meta', cell: (i) => <Badge>{i.country_code}</Badge> },
    { key: 'provider', header: 'Data channel', cell: (i) => <div><div className="text-[13px] text-ink">{i.provider.name}</div>
      <StatusBadge status={i.provider.integration_status} /></div> },
    { key: 'caps', header: 'Data available', cell: (i) => <div className="flex flex-wrap gap-1">
      {i.supports_balances && <Badge>Balances</Badge>}{i.supports_transactions && <Badge>Transactions</Badge>}{i.supports_statements && <Badge>Statements</Badge>}</div> },
    { key: 'ccy', header: 'Currencies', cell: (i) => <span className="text-[13px]">{i.supported_currencies.join(', ') || '—'}</span> },
    { key: 'accounts', header: 'Your accounts', align: 'right', cell: (i) => <span className="num">{i.account_count ?? 0}</span> },
  ];

  return (
    <>
      <PageHeader eyebrow="Banking" title="Institutions & integrations"
        subtitle="Banks you can monitor and the channel through which their data is accessed. Real connectivity is only claimed once verified per provider."
        actions={can('accounts:write') && <Button icon={<Plus className="h-4 w-4" />} onClick={() => setAdd(true)}>Add institution</Button>} />
      <div className="mb-4"><Tabs value={tab} onChange={setTab} tabs={[{ value: 'institutions', label: 'Institutions', count: list.data?.total },
        { value: 'providers', label: 'Integration providers' }]} /></div>
      {tab === 'institutions' ? (
        <Card padded={false}>
          <div className="flex flex-col sm:flex-row gap-3 p-4 border-b border-line">
            <div className="relative flex-1"><Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted" />
              <Input aria-label="Search institutions" placeholder="Search banks…" className="pl-9" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} /></div>
            <Select aria-label="Country" className="sm:w-56" value={country} onChange={(e) => { setCountry(e.target.value); setPage(1); }}>
              <option value="">All countries</option>{countries.data?.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
            </Select>
          </div>
          <DataTable columns={cols} rows={list.data?.items} rowKey={(i) => i.id} loading={list.isLoading} error={list.error}
            empty={<EmptyState icon={<Landmark className="h-6 w-6" />} title="No institutions found" body="Add any bank as a statement-upload institution." />} />
          {list.data && <Pagination page={page} pages={list.data.pages} total={list.data.total} pageSize={25} onPage={setPage} />}
        </Card>
      ) : (
        <>
          <div className="mb-4"><Notice tone="info" title="Global coverage, honestly labelled">
            Statement upload works with any bank. Aggregator clients (Plaid, GoCardless) are implemented against their public APIs but marked unverified until tested with your credentials.
            India Account Aggregator and per-bank portal automation are planned and require an authorised participant or explicit bank permission.
          </Notice></div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {providers.data?.map((p) => <ProviderCard key={p.id} p={p} />)}
          </div>
        </>
      )}
      <AddInstitution open={add} onClose={() => setAdd(false)} />
      {tab === 'providers' && !providers.data && <Card className="mt-4"><CardHeader title="Loading providers…" /></Card>}
    </>
  );
}
