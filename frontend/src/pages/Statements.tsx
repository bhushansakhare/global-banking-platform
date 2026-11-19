import { useQuery } from '@tanstack/react-query';
import { FileText, Upload } from 'lucide-react';
import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Badge, Button, Card, DemoBadge, Select, StatusBadge } from '@/components/ui/primitives';
import { DataTable, Pagination, type Column } from '@/components/ui/DataTable';
import { PageHeader } from '@/components/ui/layout';
import { EmptyState } from '@/components/ui/States';
import { Money } from '@/components/Money';
import { UploadStatementModal } from '@/features/statements/UploadStatementModal';
import { accountsApi, statementsApi } from '@/services/api';
import { useAuth } from '@/stores/auth';
import { useAction } from '@/hooks/useMutationToast';
import { request } from '@/services/http';
import { Notice } from '@/components/ui/layout';
import type { Statement } from '@/types/api';
import { date, relative } from '@/utils/format';

const SOURCE: Record<string, string> = { provider_download: 'Downloaded', upload: 'Uploaded', demo: 'Demo download' };

export default function Statements() {
  const [params, setParams] = useSearchParams();
  const [page, setPage] = useState(1);
  const [upload, setUpload] = useState(false);
  const navigate = useNavigate();
  const can = useAuth((s) => s.can);
  const account_id = params.get('account_id') ?? '';
  const status = params.get('status') ?? '';
  const [extractionMessage, setExtractionMessage] = useState('');
  const extract = useAction(() => request<{ message: string }>('/statements/extract-pending', {
    method: 'POST', query: account_id ? { account_id } : {},
  }), { invalidate: [['statements'], ['jobs']], onSuccess: (r) => setExtractionMessage(r.message) });
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: () => accountsApi.list() });
  const list = useQuery({ queryKey: ['statements', account_id, status, page], refetchInterval: 15000,
    queryFn: () => statementsApi.list({ account_id, status, page, page_size: 25 }) });
  const setFilter = (k: string, v: string) => { if (v) params.set(k, v); else params.delete(k); setParams(params); setPage(1); };

  const cols: Column<Statement>[] = [
    { key: 'acc', header: 'Account', mobile: 'title', cell: (s) => <div className="min-w-0"><div className="flex items-center gap-2">
      <span className="font-medium text-ink truncate">{s.account_name}</span><DemoBadge show={s.is_demo} /></div>
      <div className="text-[12px] text-muted truncate">{s.file?.original_filename}</div></div> },
    { key: 'period', header: 'Period', cell: (s) => <span className="text-[13px] whitespace-nowrap">{date(s.period_start)} – {date(s.period_end)}</span> },
    { key: 'status', header: 'Processing', mobile: 'meta', cell: (s) => <StatusBadge status={s.processing_status} /> },
    { key: 'fmt', header: 'Format', cell: (s) => <Badge>{s.file?.format.toUpperCase() ?? '—'}</Badge> },
    { key: 'src', header: 'Source', cell: (s) => <span className="text-[13px]">{SOURCE[s.source] ?? s.source}</span> },
    { key: 'tx', header: 'Txns', align: 'right', cell: (s) => <span className="num">{s.transaction_count ?? '—'}</span> },
    { key: 'close', header: 'Closing', align: 'right', cell: (s) => <Money value={s.closing_balance} currency={s.currency} /> },
    { key: 'check', header: 'Arithmetic', cell: (s) => s.arithmetic_valid === null ? <span className="text-muted text-[13px]">—</span>
      : <Badge tone={s.arithmetic_valid ? 'success' : 'warning'} dot>{s.arithmetic_valid ? 'Balances' : 'Mismatch'}</Badge> },
    { key: 'dup', header: 'Duplicate', cell: (s) => s.duplicate_of_id ? <Badge tone="warning">Same period</Badge> : <span className="text-muted">—</span> },
    { key: 'dl', header: 'Received', cell: (s) => <span className="text-[13px]">{relative(s.downloaded_at ?? s.created_at)}</span> },
  ];

  return (
    <>
      <PageHeader eyebrow="Data" title="Statements" subtitle="Downloaded and uploaded statements with extraction status. Originals are stored unchanged and content-hashed for duplicate detection."
        actions={can('statements:write') && <>
          <Button variant="outline" loading={extract.isPending} onClick={() => extract.mutate(undefined)}>Extract pending statements</Button>
          <Button icon={<Upload className="h-4 w-4" />} onClick={() => setUpload(true)}>Upload statement</Button></>} />
      {extractionMessage && <div className="mb-4"><Notice>{extractionMessage}</Notice></div>}
      <Card padded={false}>
        <div className="flex flex-col sm:flex-row gap-3 p-4 border-b border-line">
          <Select aria-label="Account" value={account_id} onChange={(e) => setFilter('account_id', e.target.value)} className="sm:w-72">
            <option value="">All accounts</option>{accounts.data?.map((a) => <option key={a.id} value={a.id}>{a.nickname}</option>)}
          </Select>
          <Select aria-label="Status" value={status} onChange={(e) => setFilter('status', e.target.value)} className="sm:w-56">
            <option value="">All statuses</option>
            {['processed', 'processed_with_warnings', 'needs_review', 'needs_ocr', 'pending', 'failed'].map((s) =>
              <option key={s} value={s}>{s.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())}</option>)}
          </Select>
        </div>
        <DataTable columns={cols} rows={list.data?.items} rowKey={(s) => s.id} loading={list.isLoading} error={list.error}
          onRowClick={(s) => navigate(`/statements/${s.id}`)}
          empty={<EmptyState icon={<FileText className="h-6 w-6" />} title="No statements yet"
            body="Statements appear here when automation downloads them or when you upload an official export." />} />
        {list.data && <Pagination page={page} pages={list.data.pages} total={list.data.total} pageSize={25} onPage={setPage} />}
      </Card>
      <UploadStatementModal open={upload} onClose={() => setUpload(false)} accountId={account_id || undefined} />
    </>
  );
}
