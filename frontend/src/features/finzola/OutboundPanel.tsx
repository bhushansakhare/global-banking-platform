import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button, Card, CardHeader, Field, Input, Select, Badge } from '@/components/ui/primitives';
import { DataTable } from '@/components/ui/DataTable';
import { Modal } from '@/components/ui/Modal';
import { Notice } from '@/components/ui/layout';
import { accountsApi, transactionsApi } from '@/services/api';
import { useAction } from '@/hooks/useMutationToast';
import { dateTime } from '@/utils/format';
import { Money } from '@/components/Money';
import { finzolaApi, type Delivery, type Outbound } from './api';

export function OutboundPanel({ outbound, editable, page, setPage }: { outbound: Outbound; editable: boolean; page: number; setPage: (p: number) => void }) {
  const [org, setOrg] = useState('');
  const [key, setKey] = useState('');
  const [destination, setDestination] = useState('');
  const [account, setAccount] = useState('');
  const [transaction, setTransaction] = useState('');
  const [details, setDetails] = useState<Delivery | null>(null);
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: () => accountsApi.list() });
  const txs = useQuery({ queryKey: ['finzola', 'debits', account], queryFn: () => transactionsApi.list({ account_id: account, direction: 'debit', page_size: 100 }), enabled: !!account });
  const save = useAction(() => finzolaApi.saveDestination({ external_org_id: org, webhook_key: key }), {
    success: 'Destination key saved. Test it before enabling delivery.', invalidate: [['finzola']], onSuccess: () => setKey('') });
  const test = useAction((organization: string) => finzolaApi.testDestination(organization), { success: 'No-expense-write connection test queued', invalidate: [['finzola']] });
  const auto = useAction(({ organization, enabled }: { organization: string; enabled: boolean }) => finzolaApi.saveDestination({ external_org_id: organization, auto_delivery: enabled }), {
    success: 'Automatic result delivery setting saved', invalidate: [['finzola']] });
  const remove = useAction((organization: string) => finzolaApi.removeDestination(organization), { success: 'Destination removed; pending deliveries cancelled', invalidate: [['finzola']] });
  const preview = useAction(() => finzolaApi.previewDebit({ source_id: transaction, external_org_id: destination }));
  const send = useAction(() => finzolaApi.sendDebit({ source_id: transaction, external_org_id: destination }), {
    success: result => result.status === 'delivered' ? 'This expense was already acknowledged' : 'Expense delivery queued; wait for acknowledgement below',
    invalidate: [['finzola']], onSuccess: () => preview.reset() });
  const retry = useAction((id: string) => finzolaApi.retry(id), { success: 'Transient delivery retry requested', invalidate: [['finzola']] });
  return <div className="space-y-4">
    <Card><CardHeader title="Organization destinations" subtitle="Use the organization-generated webhook key. A saved key is not a verified connection; verification requires the documented no-write test acknowledgement." />
      {editable && <form onSubmit={e => { e.preventDefault(); save.mutate(undefined); }} className="mb-5 grid gap-4 sm:grid-cols-2">
        <Field label="Destination Finzola organization ID" htmlFor="fz-destination-org"><Input id="fz-destination-org" required value={org} onChange={e => setOrg(e.target.value)} /></Field>
        <Field label="Organization webhook key" htmlFor="fz-webhook-key" hint="Encrypted after saving. Replacing a key resets verification and turns automatic delivery off."><Input id="fz-webhook-key" type="password" autoComplete="new-password" required minLength={8} value={key} onChange={e => setKey(e.target.value)} /></Field>
        <div><Button type="submit" loading={save.isPending}>Save destination key</Button></div>
      </form>}
      <div className="space-y-3">{outbound.destinations.map(d => <div key={d.id} className="rounded-xl border border-line p-4">
        <div className="flex flex-wrap items-center justify-between gap-3"><div><strong className="text-sm">{d.external_org_id}</strong><div className="mt-2 flex flex-wrap gap-2"><Badge tone={d.verified ? 'success' : 'warning'}>{d.verified ? 'Verified key' : 'Test required'}</Badge><Badge>{d.auto_delivery ? 'Automatic results on' : 'Automatic results off'}</Badge></div><p className="mt-2 text-xs text-muted">Verified: {dateTime(d.verified_at)} · revision {d.revision}</p></div>
          {editable && <div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" loading={test.isPending} onClick={() => test.mutate(d.external_org_id)}>Test destination</Button><Button variant="outline" size="sm" disabled={!d.verified || auto.isPending} onClick={() => auto.mutate({ organization: d.external_org_id, enabled: !d.auto_delivery })}>{d.auto_delivery ? 'Turn automatic results off' : 'Enable automatic results'}</Button><Button variant="ghost" size="sm" loading={remove.isPending} onClick={() => remove.mutate(d.external_org_id)}>Remove destination</Button></div>}
        </div>{d.last_error && <p className="mt-3 text-sm text-amber-700 dark:text-amber-300">{d.last_error}</p>}
      </div>)}{!outbound.destinations.length && <p className="text-sm text-muted">No destination configured. Nothing is sent to Finzola until you set up and verify a destination.</p>}</div>
    </Card>
    {editable && <Card><CardHeader title="Send one bank debit as an expense" subtitle="Manual expense publishing works independently of automatic result delivery. Bank statements must first be validated and reviewed in Match bank payments." />
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Send to organization" htmlFor="fz-send-org"><Select id="fz-send-org" value={destination} onChange={e => { setDestination(e.target.value); preview.reset(); }}><option value="">Choose verified destination…</option>{outbound.destinations.filter(d => d.verified).map(d => <option key={d.id} value={d.external_org_id}>{d.external_org_id}</option>)}</Select></Field>
        <Field label="Debit account" htmlFor="fz-debit-account"><Select id="fz-debit-account" value={account} onChange={e => { setAccount(e.target.value); setTransaction(''); preview.reset(); }}><option value="">Choose bank account…</option>{accounts.data?.filter(a => !a.is_demo).map(a => <option key={a.id} value={a.id}>{a.nickname} · {a.account_number_masked}</option>)}</Select></Field>
        <Field label="Bank debit" htmlFor="fz-debit"><Select id="fz-debit" disabled={!account} value={transaction} onChange={e => { setTransaction(e.target.value); preview.reset(); }}><option value="">Choose debit…</option>{txs.data?.items.filter(t => t.source === 'statement' && !t.is_demo).map(t => <option key={t.id} value={t.id}>{t.booking_date} · {t.description.slice(0, 55)} · {t.debit} {t.currency}</option>)}</Select></Field>
      </div>
      <div className="mt-4"><Button variant="outline" disabled={!destination || !transaction} loading={preview.isPending} onClick={() => preview.mutate(undefined)}>Preview expense payload</Button></div>
      {preview.data && <div className="mt-4 rounded-xl border border-line p-4"><p className="text-sm font-medium">{String(preview.data.payload.purpose)}</p><Money value={Number(preview.data.payload.amount)} currency={String(preview.data.payload.currency)} className="my-2 block text-xl font-semibold" /><p className="mb-3 text-sm text-muted">{String(preview.data.payload.expenseDate)} · {destination}</p><Notice>This publishes an expense display record. It does not approve an expense or initiate a bank payment.</Notice><Button className="mt-4" loading={send.isPending} onClick={() => send.mutate(undefined)}>Queue this bank debit for Finzola</Button></div>}
    </Card>}
    <Card padded={false}><div className="p-5"><CardHeader title="Delivery history" subtitle="Queued or running means pending. Delivered means the receiver acknowledged this exact request ID. Stable IDs reduce duplicates; receiver-side replay handling is also required." /></div>
      <DataTable rows={outbound.deliveries} rowKey={d => d.id} columns={[
        { key: 'source', header: 'Source', mobile: 'title', cell: d => <div><strong className="text-sm">{d.source_kind === 'debit' ? 'Bank debit expense' : 'Comparison result'}</strong><p className="max-w-64 truncate text-xs text-muted">{d.request_id}</p></div> },
        { key: 'state', header: 'Delivery', mobile: 'meta', cell: d => <Badge tone={d.status === 'delivered' ? 'success' : d.status.includes('failed') || d.status === 'retry_pending' ? 'warning' : 'neutral'}>{d.status.replace(/_/g, ' ')}</Badge> },
        { key: 'attempts', header: 'Attempts', cell: d => `${d.attempts} / 4` },
        { key: 'time', header: 'Created', cell: d => <span className="text-xs">{dateTime(d.created_at)}</span> },
        { key: 'error', header: 'Last result', cell: d => <span className="max-w-64 text-xs text-muted">{d.error || (d.acknowledgement ? 'Exact request ID acknowledged' : 'Awaiting receiver')}</span> },
        { key: 'actions', header: '', cell: d => <div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => setDetails(d)}>Details</Button>{editable && d.status === 'retry_pending' && <Button size="sm" variant="outline" loading={retry.isPending} onClick={() => retry.mutate(d.id)}>Retry</Button>}</div> },
      ]} />
      <div className="flex items-center justify-between gap-2 p-4 text-xs text-muted"><span>{outbound.total} deliveries · page {page}</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button><Button size="sm" variant="outline" disabled={page * 25 >= outbound.total} onClick={() => setPage(page + 1)}>Next</Button></div></div>
    </Card>
    <Modal open={!!details} onClose={() => setDetails(null)} title="Finzola delivery details"><pre className="max-h-[65vh] overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(details, null, 2)}</pre></Modal>
  </div>;
}
