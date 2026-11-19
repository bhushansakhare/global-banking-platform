import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, Download, Eye, GitCompareArrows, RefreshCcw } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Badge, Button, Card, CardHeader, DemoBadge, Input, StatusBadge } from '@/components/ui/primitives';
import { DataTable } from '@/components/ui/DataTable';
import { ConfirmDialog, Modal } from '@/components/ui/Modal';
import { KeyValue, Notice, PageHeader } from '@/components/ui/layout';
import { ErrorState, TableSkeleton } from '@/components/ui/States';
import { DebitCredit, Money } from '@/components/Money';
import { useAction } from '@/hooks/useMutationToast';
import { reconApi, statementsApi } from '@/services/api';
import { download, fetchBlobUrl, request } from '@/services/http';
import { useAuth } from '@/stores/auth';
import { bytes, date, dateTime, pct } from '@/utils/format';

function Preview({ id, open, onClose, format }: { id: string; open: boolean; onClose: () => void; format: string }) {
  const [state, setState] = useState<{ url?: string; text?: string; error?: string }>({});
  useEffect(() => {
    if (!open) return;
    let url: string | undefined;
    const path = `/statements/${id}/file`;
    const load = format === 'csv'
      ? request<Response>(path, { query: { inline: true }, raw: true }).then(async (r) => setState({ text: await r.text() }))
      : fetchBlobUrl(path, { inline: true }).then((r) => { url = r.url; setState({ url: r.url }); });
    load.catch(() => setState({ error: 'Preview unavailable' }));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [open, id, format]);
  return (
    <Modal open={open} onClose={onClose} size="xl" title="Original file preview" description="Exactly as received — never modified.">
      {state.error ? <p className="text-sm text-muted">{state.error}</p>
        : state.text !== undefined ? <pre className="max-h-[65vh] overflow-auto rounded-xl bg-surface-2 p-4 text-[12px] font-mono whitespace-pre">{state.text}</pre>
        : state.url && format === 'pdf' ? <iframe title="Statement PDF" src={state.url} className="w-full h-[70vh] rounded-xl border border-line" />
        : state.url ? <p className="text-sm text-muted">Spreadsheet files cannot be previewed in the browser. Download the original instead.</p>
        : <TableSkeleton rows={6} />}
    </Modal>
  );
}

export default function StatementDetail() {
  const { id = '' } = useParams();
  const navigate = useNavigate();
  const can = useAuth((s) => s.can);
  const [preview, setPreview] = useState(false);
  const [confirmFigures, setConfirmFigures] = useState(false);
  const confirmExtraction = useAction(() => request(`/statements/${id}/confirm-extraction`, { method: 'POST' }), {
    success: 'Reviewed figures confirmed', invalidate: [['statement', id], ['statements'], ['account'], ['notifications']],
    onSuccess: () => setConfirmFigures(false),
  });
  const [imagePage, setImagePage] = useState('1');
  const imageDownload = useAction(() => download(`/statements/${id}/screenshot`, { page: Number(imagePage) }, `statement-page-${imagePage}.png`), {});
  const bankImageDownload = useAction(() => download(`/statements/${id}/screenshot`, { source: 'bank' }, 'bank-statement-screen.png'), {});
  const q = useQuery({ queryKey: ['statement', id], queryFn: () => statementsApi.get(id) });
  const tx = useQuery({ queryKey: ['statement', id, 'tx'], queryFn: () => statementsApi.transactions(id) });
  const reprocess = useAction(() => statementsApi.reprocess(id), { success: 'Statement reprocessed', invalidate: [['statement', id], ['statements']] });
  const recon = useAction(() => reconApi.run({ account_id: q.data!.account_id, statement_id: id }), {
    success: 'Reconciliation complete', invalidate: [['recon']], onSuccess: (s) => navigate(`/reconciliation/${s.id}`) });

  if (q.isError) return <Card><ErrorState error={q.error} onRetry={() => q.refetch()} /></Card>;
  if (!q.data) return <Card padded={false}><TableSkeleton /></Card>;
  const s = q.data;
  const job = s.parsing_jobs?.[0];
  const diag = (job?.diagnostics ?? {}) as Record<string, unknown>;

  return (
    <>
      <Link to="/statements" className="inline-flex items-center gap-1.5 text-[13px] text-muted hover:text-ink mb-3"><ArrowLeft className="h-4 w-4" />Statements</Link>
      <PageHeader title={<span className="flex flex-wrap items-center gap-2">{s.account_name}<DemoBadge show={s.is_demo} /><StatusBadge status={s.processing_status} /></span>}
        subtitle={`${s.institution ?? ''} · ${date(s.period_start)} – ${date(s.period_end)} · ${s.file?.original_filename ?? ''}`}
        actions={<>
          <Button variant="outline" icon={<Eye className="h-4 w-4" />} onClick={() => setPreview(true)}>Preview</Button>
          <Button variant="outline" icon={<Download className="h-4 w-4" />} onClick={() => download(`/statements/${id}/file`, undefined, s.file?.original_filename)}>Download original</Button>
          {can('statements:write') && <Button variant="outline" icon={<RefreshCcw className="h-4 w-4" />} loading={reprocess.isPending} onClick={() => reprocess.mutate(undefined)}>Reprocess</Button>}
          {can('statements:write') && s.processing_status === 'needs_review' && s.arithmetic_valid && !!s.transaction_count &&
            <Button variant="outline" onClick={() => setConfirmFigures(true)}>Confirm reviewed figures</Button>}
          {can('reconciliation:write') && <Button icon={<GitCompareArrows className="h-4 w-4" />} loading={recon.isPending} onClick={() => recon.mutate(undefined)}
            disabled={!s.period_start}>Reconcile</Button>}
        </>} />
      {s.is_demo && <div className="mb-4"><Notice tone="demo">Synthetic demo statement produced by the Demo Bank Simulator — not a real bank document.</Notice></div>}
      {(s.file?.format === 'pdf' || s.bank_screenshot_available) && <Card className="mb-4">
        <CardHeader title="Statement images" subtitle="Download an image of any PDF page or the bank statement screen captured during collection." />
        <div className="flex flex-wrap items-end gap-3">
          {s.file?.format === 'pdf' && <>
            <label className="text-sm">PDF page
              <Input aria-label="Statement image page" type="number" min="1" max={s.file.page_count ?? undefined} value={imagePage} onChange={(e) => setImagePage(e.target.value)} className="mt-1 w-28" />
            </label>
            <Button variant="outline" loading={imageDownload.isPending} disabled={!Number.isInteger(Number(imagePage)) || Number(imagePage) < 1} onClick={() => imageDownload.mutate(undefined)}>Download page image</Button>
          </>}
          {s.bank_screenshot_available && <Button variant="outline" loading={bankImageDownload.isPending} onClick={() => bankImageDownload.mutate(undefined)}>Download bank screen</Button>}
        </div>
      </Card>}
      {s.processing_status === 'needs_ocr' && <div className="mb-4"><Notice tone="warning" title="Scanned document">
        This PDF contains no extractable text. OCR with manual verification is required — figures are never guessed.</Notice></div>}
      {s.duplicate_of_id && <div className="mb-4"><Notice tone="warning">Another statement covers the same period. <Link className="link" to={`/statements/${s.duplicate_of_id}`}>Open it</Link></Notice></div>}

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <Card className="xl:col-span-2">
          <CardHeader title="Balance verification" subtitle="Opening + credits − debits must equal the closing balance" />
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {[['Opening', s.opening_balance], ['Credits', s.total_credits], ['Debits', s.total_debits], ['Closing', s.closing_balance]].map(([l, v]) => (
              <div key={l as string} className="rounded-xl bg-surface-2 p-3.5"><p className="text-[12px] text-muted">{l}</p>
                <Money value={v as string} currency={s.currency} className="block mt-1 text-[17px] font-semibold text-ink" /></div>
            ))}
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            {s.arithmetic_valid === null ? <Badge>Arithmetic check not possible</Badge>
              : s.arithmetic_valid ? <Badge tone="success" dot>Statement balances</Badge>
              : <Badge tone="warning" dot>Difference <Money value={s.arithmetic_difference} currency={s.currency} /></Badge>}
            <span className="text-[13px] text-muted">Extraction confidence {pct(s.parse_confidence, 0)}</span>
          </div>
          {s.warnings.length > 0 && (
            <ul className="mt-4 space-y-1.5 text-[13px]">
              {s.warnings.slice(0, 12).map((w, i) => <li key={i} className="flex gap-2"><Badge tone="warning">{w.code.replace(/_/g, ' ')}</Badge><span className="text-muted">{w.message}</span></li>)}
            </ul>
          )}
        </Card>
        <Card>
          <CardHeader title="File & extraction" />
          <KeyValue items={[
            { label: 'Format', value: s.file?.format.toUpperCase() }, { label: 'Size', value: bytes(s.file?.size_bytes) },
            { label: 'Pages', value: s.file?.page_count ?? '—' }, { label: 'Parser', value: s.parser_name ?? '—' },
            { label: 'Rows extracted', value: job?.rows_extracted ?? '—' }, { label: 'New / duplicate', value: job ? `${job.rows_inserted} / ${job.rows_duplicate}` : '—' },
            { label: 'Date format', value: String(diag.date_format ?? '—') }, { label: 'Received', value: dateTime(s.downloaded_at ?? s.created_at) },
          ]} />
          <p className="mt-4 text-[11px] text-muted font-mono break-all">SHA-256 {s.file?.sha256}</p>
        </Card>
      </div>

      <Card className="mt-4" padded={false}>
        <div className="p-5 pb-2"><CardHeader title="Extracted transactions" subtitle={`${tx.data?.length ?? 0} rows · original values preserved`} /></div>
        <DataTable rows={tx.data} rowKey={(t) => t.id} loading={tx.isLoading} error={tx.error} columns={[
          { key: 'd', header: 'Date', cell: (t) => <span className="text-[13px] whitespace-nowrap">{date(t.booking_date)}</span> },
          { key: 'desc', header: 'Description', mobile: 'title', cell: (t) => <span className="text-ink">{t.description}</span> },
          { key: 'ref', header: 'Reference', cell: (t) => <span className="font-mono text-[12px] text-muted">{t.reference ?? '—'}</span> },
          { key: 'amt', header: 'Amount', align: 'right', mobile: 'meta', cell: (t) => <DebitCredit debit={t.debit} credit={t.credit} currency={t.currency} /> },
          { key: 'bal', header: 'Balance', align: 'right', cell: (t) => <Money value={t.running_balance} currency={t.currency} /> },
          { key: 'rec', header: 'Reconciliation', cell: (t) => <StatusBadge status={t.reconciliation_status} /> },
        ]} />
      </Card>
      <Preview id={id} open={preview} onClose={() => setPreview(false)} format={s.file?.format ?? ''} />
      <ConfirmDialog open={confirmFigures} onClose={() => setConfirmFigures(false)} title="Confirm extracted figures?"
        body="Check every extracted transaction and the balances against the original statement first. Confirming makes this dated closing balance eligible for your account's target alerts."
        confirmLabel="Confirm reviewed figures" loading={confirmExtraction.isPending} onConfirm={() => confirmExtraction.mutate(undefined)} />
    </>
  );
}
