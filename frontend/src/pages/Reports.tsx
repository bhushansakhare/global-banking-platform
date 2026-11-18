import { useQuery } from '@tanstack/react-query';
import { Download, FileBarChart, FileSpreadsheet } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Badge, Button, Card, CardHeader, DemoBadge, Field, Input, Select } from '@/components/ui/primitives';
import { DataTable, Pagination } from '@/components/ui/DataTable';
import { PageHeader } from '@/components/ui/layout';
import { EmptyState } from '@/components/ui/States';
import { useAction } from '@/hooks/useMutationToast';
import { accountsApi, reportsApi } from '@/services/api';
import { download } from '@/services/http';
import { useAuth } from '@/stores/auth';
import { bytes, daysAgo, isoDate, relative } from '@/utils/format';

const NEEDS: Record<string, 'date' | 'range' | 'month' | 'none'> = {
  daily: 'date', weekly: 'date', monthly: 'month', bank: 'range', account: 'range', transactions: 'range', balances: 'none',
  credit_debit: 'range', reconciliation: 'range', failed_automation: 'range', missing_statements: 'none', currency: 'none',
};

export default function Reports() {
  const can = useAuth((s) => s.can);
  const [page, setPage] = useState(1);
  const today = isoDate(new Date());
  const [f, setF] = useState({ report_type: 'daily', file_format: 'xlsx', date: today, date_from: daysAgo(30), date_to: today,
    month: today.slice(0, 7), account_id: '' });
  const types = useQuery({ queryKey: ['report-types'], queryFn: reportsApi.types });
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: () => accountsApi.list() });
  const list = useQuery({ queryKey: ['reports', page], queryFn: () => reportsApi.list({ page, page_size: 20 }) });
  const need = NEEDS[f.report_type] ?? 'range';
  const gen = useAction(() => reportsApi.create({ report_type: f.report_type, file_format: f.file_format,
    ...(need === 'date' ? (f.report_type === 'weekly' ? { date_to: f.date } : { date: f.date }) : {}),
    ...(need === 'range' ? { date_from: f.date_from, date_to: f.date_to } : {}),
    ...(need === 'month' ? { month: f.month } : {}), account_id: f.account_id || undefined }),
  { success: 'Report generated', invalidate: [['reports']], onSuccess: (r) => download(`/reports/${r.id}/download`, undefined, `${r.report_type}.${r.file_format}`) });
  const submit = (e: FormEvent) => { e.preventDefault(); gen.mutate(undefined); };

  return (
    <>
      <PageHeader eyebrow="Output" title="Reports" subtitle="Generate banking, balance, reconciliation and exception reports in Excel, CSV or PDF. Reports containing demo data are watermarked." />
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        {can('reports:write') && (
          <Card>
            <CardHeader title="Generate report" icon={<FileBarChart className="h-4 w-4" />} />
            <form onSubmit={submit} className="space-y-4">
              <Field label="Report" htmlFor="rt"><Select id="rt" value={f.report_type} onChange={(e) => setF({ ...f, report_type: e.target.value })}>
                {types.data?.map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}</Select></Field>
              {need === 'date' && <Field label={f.report_type === 'weekly' ? 'Week ending' : 'Date'} htmlFor="rd"><Input id="rd" type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} /></Field>}
              {need === 'month' && <Field label="Month" htmlFor="rm"><Input id="rm" type="month" value={f.month} onChange={(e) => setF({ ...f, month: e.target.value })} /></Field>}
              {need === 'range' && <div className="grid grid-cols-2 gap-3">
                <Field label="From" htmlFor="rf"><Input id="rf" type="date" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} /></Field>
                <Field label="To" htmlFor="rto"><Input id="rto" type="date" value={f.date_to} onChange={(e) => setF({ ...f, date_to: e.target.value })} /></Field></div>}
              <Field label="Account" htmlFor="ra"><Select id="ra" value={f.account_id} onChange={(e) => setF({ ...f, account_id: e.target.value })}>
                <option value="">All accounts</option>{accounts.data?.map((a) => <option key={a.id} value={a.id}>{a.nickname}</option>)}</Select></Field>
              <Field label="Format" htmlFor="rfmt">
                <div className="grid grid-cols-3 gap-2" role="radiogroup" id="rfmt">
                  {(['xlsx', 'csv', 'pdf'] as const).map((x) => (
                    <button type="button" key={x} role="radio" aria-checked={f.file_format === x} onClick={() => setF({ ...f, file_format: x })}
                      className={`h-10 rounded-lg border text-sm font-medium transition ${f.file_format === x ? 'border-brand-500 bg-brand-50 text-brand-800 dark:bg-brand-900/40 dark:text-brand-100' : 'border-line text-muted hover:text-ink'}`}>
                      {x === 'xlsx' ? 'Excel' : x.toUpperCase()}</button>))}
                </div>
              </Field>
              <Button type="submit" className="w-full justify-center" loading={gen.isPending} icon={<FileSpreadsheet className="h-4 w-4" />}>Generate & download</Button>
            </form>
          </Card>
        )}
        <Card className={can('reports:write') ? 'xl:col-span-2' : 'xl:col-span-3'} padded={false}>
          <div className="p-5 pb-2"><CardHeader title="Generated reports" subtitle="Including automated daily reports" /></div>
          <DataTable rows={list.data?.items} rowKey={(r) => r.id} loading={list.isLoading} error={list.error}
            empty={<EmptyState title="No reports yet" body="Generate one, or enable the daily report in the Automation Center." />} columns={[
              { key: 't', header: 'Report', mobile: 'title', cell: (r) => <div className="min-w-0"><div className="flex items-center gap-2"><span className="text-ink truncate">{r.title}</span>
                <DemoBadge show={r.contains_demo_data} label="DEMO DATA" /></div><div className="text-[12px] text-muted">{r.trigger === 'automation' ? 'Automated' : 'Manual'}</div></div> },
              { key: 'f', header: 'Format', mobile: 'meta', cell: (r) => <Badge>{r.file_format.toUpperCase()}</Badge> },
              { key: 's', header: 'Size', cell: (r) => <span className="text-[13px]">{bytes(r.size_bytes)}</span> },
              { key: 'c', header: 'Created', cell: (r) => <span className="text-[13px]">{relative(r.created_at)}</span> },
              { key: 'd', header: '', align: 'right', cell: (r) => <Button size="sm" variant="outline" icon={<Download className="h-3.5 w-3.5" />}
                onClick={() => download(`/reports/${r.id}/download`, undefined, `${r.report_type}.${r.file_format}`)}>Download</Button> },
            ]} />
          {list.data && <Pagination page={page} pages={list.data.pages} total={list.data.total} pageSize={20} onPage={setPage} />}
        </Card>
      </div>
    </>
  );
}
