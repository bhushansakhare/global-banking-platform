import { useEffect, useState, type FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button, Card, CardHeader, Field, Input, Select, Badge } from '@/components/ui/primitives';
import { Notice } from '@/components/ui/layout';
import { useAction } from '@/hooks/useMutationToast';
import { download } from '@/services/http';
import { dateTime } from '@/utils/format';
import { DOMAINS, finzolaApi, ROOT, type Domain, type GatewayConfig } from './api';

export function GatewayPanel({ config, editable }: { config: GatewayConfig; editable: boolean }) {
  const [key, setKey] = useState('');
  const [domains, setDomains] = useState<Domain[]>(config.domains);
  const [poll, setPoll] = useState(config.polling_enabled);
  const [interval, setInterval] = useState<5 | 15 | 60>(config.interval_minutes);
  const [domain, setDomain] = useState<Domain>('expenses');
  const [page, setPage] = useState(1);
  useEffect(() => { setDomains(config.domains); setPoll(config.polling_enabled); setInterval(config.interval_minutes); },
    [config.domains, config.polling_enabled, config.interval_minutes]);
  const save = useAction(() => finzolaApi.saveConfig({ ...(key ? { api_key: key } : {}), domains,
    polling_enabled: poll, interval_minutes: interval }), { success: 'Gateway settings saved',
    invalidate: [['finzola']], onSuccess: () => setKey('') });
  const fetch = useAction(finzolaApi.fetch, { success: 'Fetch queued. Domain status updates below.', invalidate: [['finzola']] });
  const data = useQuery({ queryKey: ['finzola', 'records', domain, page], queryFn: () => finzolaApi.records(domain, page),
    refetchInterval: 5000 });
  const exportData = useAction((format: 'csv' | 'json') => download(`${ROOT}/custom-api/data/${domain}/export`, { format }, `finzola-${domain}.${format}`));
  const submit = (event: FormEvent) => { event.preventDefault(); save.mutate(undefined); };
  return <div className="space-y-4">
    {config.configured && config.authorization_valid === false && <Notice tone="warning">Gateway authorizer access changed. An authorized editor must save the settings and fetch fresh data before comparison can continue.</Notice>}
    <Card><CardHeader title="Import from Finzola" subtitle="The gateway key stays encrypted on this computer’s server. Existing cached records remain visible if a fetch fails." action={<Badge tone={config.configured ? 'info' : 'neutral'}>{config.configured ? 'Key saved' : 'Setup needed'}</Badge>} />
      <form onSubmit={submit} className="space-y-4">
        <Field label="Finzola gateway key" htmlFor="fz-gateway-key" hint={config.configured ? 'Leave blank to keep the saved key. Saving a replacement clears cached access.' : 'Use your approved gateway key; the organization webhook key is a separate credential.'}>
          <Input id="fz-gateway-key" type="password" autoComplete="new-password" value={key} disabled={!editable} required={!config.configured} onChange={e => setKey(e.target.value)} />
        </Field>
        <fieldset><legend className="mb-2 text-sm font-medium">Domains to import</legend><div className="flex flex-wrap gap-3">{DOMAINS.map(d => <label key={d} className="flex items-center gap-2 rounded-lg border border-line px-3 py-2 text-sm"><input type="checkbox" checked={domains.includes(d)} disabled={!editable} onChange={e => setDomains(e.target.checked ? [...domains, d] : domains.filter(v => v !== d))} />{d}</label>)}</div></fieldset>
        <div className="flex flex-wrap items-end gap-4"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={poll} disabled={!editable} onChange={e => setPoll(e.target.checked)} />Fetch automatically</label>
          <Field label="Fetch interval" htmlFor="fz-interval"><Select id="fz-interval" value={interval} disabled={!editable} onChange={e => setInterval(Number(e.target.value) as 5 | 15 | 60)}>{[5,15,60].map(n => <option key={n} value={n}>Every {n} minutes</option>)}</Select></Field>
          {editable && <><Button type="submit" loading={save.isPending} disabled={!domains.length}>Save gateway settings</Button><Button type="button" variant="outline" disabled={!config.configured} loading={fetch.isPending} onClick={() => fetch.mutate(undefined)}>Fetch now</Button></>}
        </div>
      </form>
      <p className="mt-4 text-xs text-muted">Automatic fetch needs the computer, backend and Automation Center master switch on. Next due: {dateTime(config.next_due)}. This imports full snapshots, not remote incremental pages.</p>
    </Card>
    <Card><CardHeader title="Domain freshness" subtitle="A successful domain replaces its snapshot, including an empty result. Failed domains retain the previous data and show an error." />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{config.domains.map(d => { const state = config.state[d]; return <div key={d} className="rounded-xl border border-line p-4"><div className="flex items-center justify-between gap-2"><strong className="text-sm capitalize">{d}</strong><Badge tone={state?.error ? 'warning' : state?.last_success ? 'success' : 'neutral'}>{state?.error ? 'Fetch failed' : state?.last_success ? `${state.count} records` : 'Not fetched'}</Badge></div><p className="mt-2 text-xs text-muted">Last success: {dateTime(state?.last_success)}</p>{state?.error && <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">{state.error}</p>}</div>; })}</div>
    </Card>
    <Card><CardHeader title="Imported records" subtitle="Cached Finzola records. These are not bank transactions or payment confirmations." />
      <div className="mb-4 flex flex-wrap gap-2"><Select aria-label="Record domain" className="sm:w-60" value={domain} onChange={e => { setDomain(e.target.value as Domain); setPage(1); }}>{DOMAINS.map(d => <option key={d} value={d}>{d}</option>)}</Select><Button variant="outline" loading={exportData.isPending} onClick={() => exportData.mutate('csv')}>Export CSV</Button><Button variant="outline" onClick={() => exportData.mutate('json')}>Export JSON</Button></div>
      {data.isError ? <Notice tone="warning">Records could not load. <Button size="sm" onClick={() => data.refetch()}>Retry</Button></Notice> : <>
        <p className="mb-3 text-xs text-muted">{data.data?.total ?? 0} records · page {page}</p>
        <div className="max-h-[32rem] space-y-2 overflow-auto">{data.data?.items.map(record => <details key={String(record.id)} className="rounded-xl border border-line p-3"><summary className="cursor-pointer text-sm"><strong>{String(record.id)}</strong><span className="ml-3 text-muted">{String(record.org_id ?? record.status ?? '')}</span></summary><pre className="mt-3 whitespace-pre-wrap break-all text-xs text-muted">{JSON.stringify(record, null, 2)}</pre></details>)}{!data.data?.items.length && <p className="text-sm text-muted">No cached records in this domain yet.</p>}</div>
        <div className="mt-4 flex gap-2"><Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Previous</Button><Button variant="outline" size="sm" disabled={page * 25 >= (data.data?.total ?? 0)} onClick={() => setPage(p => p + 1)}>Next</Button></div>
      </>}
    </Card>
  </div>;
}
