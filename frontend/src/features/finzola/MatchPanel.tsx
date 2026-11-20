import { useState, type FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Button, Card, CardHeader, Field, Input, Select, Textarea, Badge } from '@/components/ui/primitives';
import { DataTable } from '@/components/ui/DataTable';
import { Modal } from '@/components/ui/Modal';
import { Notice } from '@/components/ui/layout';
import { Money } from '@/components/Money';
import { accountsApi, statementsApi } from '@/services/api';
import { download } from '@/services/http';
import { useAction } from '@/hooks/useMutationToast';
import { finzolaApi, ROOT, type BridgeItem, type MappingDraft, type Pipeline } from './api';

const empty: MappingDraft = { external_org_id: '', account_id: '', routing_field: 'default', routing_value: '',
  amount_field: 'net_amount', amount_units: '', date_field: 'due_date', reference_field: 'invoice_number', direction: 'debit', enabled: true };
export function MatchPanel({ pipeline, editable, page, setPage }: { pipeline: Pipeline; editable: boolean; page: number; setPage: (p: number) => void }) {
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: () => accountsApi.list() });
  const [draft, setDraft] = useState<MappingDraft>(empty);
  const [account, setAccount] = useState('');
  const [statement, setStatement] = useState('');
  const [reason, setReason] = useState('');
  const [review, setReview] = useState<BridgeItem | null>(null);
  const [reviewReason, setReviewReason] = useState('');
  const [decision, setDecision] = useState<'retain' | 'replace_unmatched' | 'cancel'>('retain');
  const stmts = useQuery({ queryKey: ['finzola', 'evidence', account], queryFn: () => statementsApi.list({ account_id: account, page_size: 100 }), enabled: !!account });
  const preview = useQuery({ queryKey: ['finzola', 'review', review?.id], queryFn: () => finzolaApi.reviewPreview(review!.id), enabled: !!review });
  const save = useAction(() => finzolaApi.saveMapping(draft), { success: r => r.comparison_queued ? 'Account mapping saved; comparison processing queued' : 'Account mapping saved. Configure the gateway to start importing.', invalidate: [['finzola']] });
  const process = useAction(finzolaApi.process, { success: 'Comparison queued', invalidate: [['finzola'], ['recon']] });
  const evidence = useAction(() => finzolaApi.reviewEvidence(statement, reason), { success: r => r.comparison_queued ? 'Bank evidence reviewed; comparison queued' : 'Bank evidence reviewed. Configure and import Finzola data to compare.', invalidate: [['finzola']], onSuccess: () => setReason('') });
  const reviewSave = useAction(() => finzolaApi.review(review!.id, { reason: reviewReason, decision }), { success: 'Source review saved', invalidate: [['finzola']], onSuccess: () => setReview(null) });
  const exportItems = useAction(() => download(`${ROOT}/custom-api/pipeline/export`, { format: 'csv' }, 'finzola-comparison.csv'));
  const submit = (e: FormEvent) => { e.preventDefault(); save.mutate(undefined); };
  return <div className="space-y-4">
    <Card><CardHeader title="Route expenses to the correct account" subtitle="External organization IDs are Finzola IDs. Choose a local bank account separately; currency and amount units are validated." />
      <form onSubmit={submit} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <Field label="Finzola organization ID" htmlFor="fz-map-org"><Input id="fz-map-org" required disabled={!editable} value={draft.external_org_id} onChange={e => setDraft({ ...draft, external_org_id: e.target.value })} /></Field>
          <Field label="Local bank account" htmlFor="fz-map-account"><Select id="fz-map-account" required disabled={!editable} value={draft.account_id} onChange={e => setDraft({ ...draft, account_id: e.target.value })}><option value="">Choose account…</option>{accounts.data?.filter(a => !a.is_demo).map(a => <option key={a.id} value={a.id}>{a.nickname} · {a.account_number_masked} · {a.currency}</option>)}</Select></Field>
          <Field label="Routing" htmlFor="fz-map-route"><Select id="fz-map-route" disabled={!editable} value={draft.routing_field} onChange={e => setDraft({ ...draft, routing_field: e.target.value as MappingDraft['routing_field'], routing_value: '' })}><option value="default">Default for this organization</option><option value="vendor_id">Exact vendor ID</option><option value="bank_account_id">Exact Finzola bank account ID</option></Select></Field>
          {draft.routing_field !== 'default' && <Field label="Exact routing value" htmlFor="fz-map-value"><Input id="fz-map-value" required disabled={!editable} value={draft.routing_value} onChange={e => setDraft({ ...draft, routing_value: e.target.value })} /></Field>}
          <Field label="Amount field" htmlFor="fz-map-amount"><Select id="fz-map-amount" disabled={!editable} value={draft.amount_field} onChange={e => setDraft({ ...draft, amount_field: e.target.value as MappingDraft['amount_field'] })}><option value="net_amount">net_amount</option><option value="amount">amount</option></Select></Field>
          <Field label="Source amount units" htmlFor="fz-map-units" hint="Choose explicitly: INR 1250.50 or 125050 paise."><Select id="fz-map-units" required disabled={!editable} value={draft.amount_units} onChange={e => setDraft({ ...draft, amount_units: e.target.value as MappingDraft['amount_units'] })}><option value="">Choose units…</option><option value="major">Major units (1250.50 INR)</option><option value="minor">Minor units (125050 paise)</option></Select></Field>
          <Field label="Expected date field" htmlFor="fz-map-date"><Select id="fz-map-date" disabled={!editable} value={draft.date_field} onChange={e => setDraft({ ...draft, date_field: e.target.value as MappingDraft['date_field'] })}>{['due_date','expected_payment_date','payment_date'].map(v => <option key={v}>{v}</option>)}</Select></Field>
          <Field label="Reference field" htmlFor="fz-map-ref"><Select id="fz-map-ref" disabled={!editable} value={draft.reference_field} onChange={e => setDraft({ ...draft, reference_field: e.target.value as MappingDraft['reference_field'] })}><option value="invoice_number">invoice_number</option><option value="transaction_reference">Finance transaction_reference</option></Select></Field>
          <Field label="Direction" htmlFor="fz-map-direction"><Select id="fz-map-direction" disabled={!editable} value={draft.direction} onChange={e => setDraft({ ...draft, direction: e.target.value as 'debit' | 'credit' })}><option value="debit">Debit / outgoing</option><option value="credit">Credit / incoming</option></Select></Field>
        </div>
        <div className="flex flex-wrap items-center gap-3"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.enabled} disabled={!editable} onChange={e => setDraft({ ...draft, enabled: e.target.checked })} />Mapping enabled</label>{editable && <><Button type="submit" loading={save.isPending}>Save account mapping</Button><Button type="button" variant="outline" onClick={() => setDraft(empty)}>Clear form</Button></>}</div>
      </form>
      <div className="mt-5 space-y-2">{pipeline.mappings.map(m => <div key={m.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-line p-3 text-sm"><span>{m.external_org_id} · {m.routing_field}{m.routing_value ? `: ${m.routing_value}` : ''} · {accounts.data?.find(a => a.id === m.account_id)?.nickname ?? m.account_id} <Badge>{m.enabled ? 'Enabled' : 'Disabled'}</Badge></span>{editable && <Button variant="outline" size="sm" onClick={() => { const { id: _id, revision: _revision, ...body } = m; setDraft(body); }}>Edit mapping</Button>}</div>)}</div>
    </Card>
    <Card><CardHeader title="Review the original bank evidence" subtitle="Finzola expenses cannot prove payment. Compare only statements you have checked against their original bank export." />
      <form onSubmit={e => { e.preventDefault(); evidence.mutate(undefined); }} className="grid gap-4 sm:grid-cols-2">
        <Field label="Evidence account" htmlFor="fz-evidence-account"><Select id="fz-evidence-account" required value={account} onChange={e => { setAccount(e.target.value); setStatement(''); }}><option value="">Choose account…</option>{accounts.data?.filter(a => !a.is_demo).map(a => <option key={a.id} value={a.id}>{a.nickname} · {a.account_number_masked}</option>)}</Select></Field>
        <Field label="Validated bank statement" htmlFor="fz-evidence-statement"><Select id="fz-evidence-statement" required value={statement} onChange={e => setStatement(e.target.value)}><option value="">Choose statement…</option>{stmts.data?.items.filter(s => s.arithmetic_valid && ['processed','processed_with_warnings'].includes(s.processing_status)).map(s => <option key={s.id} value={s.id}>{s.file?.original_filename} · {s.period_start}–{s.period_end}</option>)}</Select></Field>
        <Field label="Evidence review note" htmlFor="fz-evidence-note"><Input id="fz-evidence-note" minLength={3} required value={reason} disabled={!editable} onChange={e => setReason(e.target.value)} placeholder="What did you verify against the original?" /></Field>
        <div className="flex flex-wrap items-end gap-3">{statement && <Link className="link text-sm" to={`/statements/${statement}`}>Open original and extracted figures</Link>}{editable && <Button type="submit" loading={evidence.isPending} disabled={!statement}>Allow this reviewed bank evidence</Button>}</div>
      </form>
    </Card>
    <Card padded={false}><div className="p-5"><CardHeader title="Expected payments & bank comparison" subtitle="An approved expense is an expectation. Only a confirmed, reviewed bank match is labelled reconciled." action={<div className="flex gap-2"><Button variant="outline" loading={exportItems.isPending} onClick={() => exportItems.mutate(undefined)}>Export</Button>{editable && <Button loading={process.isPending} onClick={() => process.mutate(undefined)}>Compare now</Button>}</div>} /></div>
      <DataTable rows={pipeline.items} rowKey={r => r.id} columns={[
        { key: 'source', header: 'Source expense', mobile: 'title', cell: i => <div><strong>{i.external_id}</strong><p className="text-xs text-muted">{i.external_org_id}</p></div> },
        { key: 'amount', header: 'Expected', cell: i => i.expected.amount ? <Money value={i.expected.amount} currency={i.expected.currency} /> : '—' },
        { key: 'date', header: 'Date / reference', cell: i => <div className="text-xs">{i.expected.date ?? '—'}<p>{i.expected.reference}</p></div> },
        { key: 'state', header: 'Comparison', cell: i => <div className="max-w-72"><Badge tone={i.status === 'reconciled' ? 'success' : i.status.includes('attention') || i.status.includes('exceptions') ? 'warning' : 'neutral'}>{i.status.replace(/_/g, ' ')}</Badge><p className="mt-1 text-xs text-muted">{i.reason}</p></div> },
        { key: 'actions', header: '', cell: i => <div className="flex flex-wrap gap-2">{i.session_id && <Link className="link text-sm" to={`/reconciliation/${i.session_id}`}>Report</Link>}{editable && <Button size="sm" variant="outline" onClick={() => { setReview(i); setDecision('retain'); setReviewReason(''); }}>Review source</Button>}</div> },
      ]} />
      <div className="flex items-center justify-between gap-2 p-4 text-xs text-muted"><span>{pipeline.total} items · page {page}</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button><Button size="sm" variant="outline" disabled={page * 25 >= pipeline.total} onClick={() => setPage(page + 1)}>Next</Button></div></div>
    </Card>
    <Modal open={!!review} onClose={() => setReview(null)} title="Review source changes">
      <p className="mb-3 text-sm text-muted">Existing financial links are preserved. A replacement is allowed only when the previous expectation has no comparison links.</p>
      {preview.data?.error && <Notice tone="warning">{preview.data.error}</Notice>}
      <p className="my-3 text-xs text-muted">Stored account: {accounts.data?.find(a => a.id === review?.account_id)?.nickname ?? review?.account_id ?? 'Not mapped'} · Current account: {accounts.data?.find(a => a.id === preview.data?.account_id)?.nickname ?? preview.data?.account_id ?? 'Unavailable'}</p>
      <div className="my-4 grid gap-3 sm:grid-cols-2"><div><p className="text-sm font-medium">Stored expectation</p><pre className="mt-2 whitespace-pre-wrap break-all text-xs">{JSON.stringify(preview.data?.existing, null, 2)}</pre></div><div><p className="text-sm font-medium">Current source</p><pre className="mt-2 whitespace-pre-wrap break-all text-xs">{JSON.stringify(preview.data?.current, null, 2)}</pre></div></div>
      <form onSubmit={e => { e.preventDefault(); reviewSave.mutate(undefined); }} className="space-y-3"><Field label="Decision" htmlFor="fz-review-decision"><Select id="fz-review-decision" value={decision} onChange={e => setDecision(e.target.value as typeof decision)}><option value="retain">Retain existing figures; pause comparison</option><option value="replace_unmatched">Accept current source for an unmatched expectation</option><option value="cancel">Cancel this expectation locally</option></Select></Field><Field label="Review reason" htmlFor="fz-review-reason"><Textarea id="fz-review-reason" required minLength={3} value={reviewReason} onChange={e => setReviewReason(e.target.value)} /></Field><Button type="submit" loading={reviewSave.isPending}>Save review decision</Button></form>
    </Modal>
  </div>;
}
