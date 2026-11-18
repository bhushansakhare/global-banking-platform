import { useQuery } from '@tanstack/react-query';
import clsx from 'clsx';
import { AlertTriangle, Bell, CheckCheck, CheckCircle2, Info, XCircle } from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Card, CardHeader, Toggle } from '@/components/ui/primitives';
import { Pagination } from '@/components/ui/DataTable';
import { PageHeader, Tabs } from '@/components/ui/layout';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/States';
import { useAction } from '@/hooks/useMutationToast';
import { notificationsApi } from '@/services/api';
import { relative } from '@/utils/format';

const ICON = { info: Info, success: CheckCircle2, warning: AlertTriangle, error: XCircle };
const TONE = { info: 'text-sky-600 bg-sky-50 dark:bg-sky-900/30', success: 'text-olive-600 bg-olive-50 dark:bg-olive-900/30',
  warning: 'text-amber-600 bg-amber-50 dark:bg-amber-900/30', error: 'text-red-600 bg-red-50 dark:bg-red-900/30' };
const PREF_LABELS: Record<string, string> = {
  sync_success: 'Successful synchronisation', connection_failed: 'Failed connections', auth_required: 'Authentication required',
  statement_missing: 'Missing statements', low_balance: 'Low-balance thresholds', reconciliation_mismatch: 'Reconciliation mismatches',
  duplicate_transaction: 'Duplicate transactions', job_failed: 'Failed background jobs', report_ready: 'Reports ready',
};

export default function Notifications() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<'all' | 'unread' | 'prefs'>('all');
  const [page, setPage] = useState(1);
  const list = useQuery({ queryKey: ['notifications', tab, page], enabled: tab !== 'prefs', refetchInterval: 30_000,
    queryFn: () => notificationsApi.list({ unread: tab === 'unread', page, page_size: 30 }) });
  const prefs = useQuery({ queryKey: ['notification-prefs'], queryFn: notificationsApi.prefs, enabled: tab === 'prefs' });
  const readAll = useAction(() => notificationsApi.readAll(), { invalidate: [['notifications']], success: 'All marked as read' });
  const read = useAction((id: string) => notificationsApi.read(id), { invalidate: [['notifications']] });
  const savePrefs = useAction((p: Record<string, boolean>) => notificationsApi.savePrefs(p), { invalidate: [['notification-prefs']], success: 'Preferences saved' });

  return (
    <>
      <PageHeader eyebrow="Inbox" title="Notifications" subtitle="Synchronisation results, authentication requests, balance alerts and reconciliation issues."
        actions={tab !== 'prefs' && <Button variant="outline" icon={<CheckCheck className="h-4 w-4" />} loading={readAll.isPending}
          disabled={!list.data?.unread} onClick={() => readAll.mutate(undefined)}>Mark all as read</Button>} />
      <div className="mb-4"><Tabs value={tab} onChange={(t) => { setTab(t); setPage(1); }} tabs={[
        { value: 'all', label: 'All' }, { value: 'unread', label: 'Unread', count: list.data?.unread }, { value: 'prefs', label: 'Preferences' }]} /></div>
      {tab === 'prefs' ? (
        <Card>
          <CardHeader title="In-app notification preferences" subtitle="Choose which events notify you" />
          {prefs.data ? (
            <ul className="divide-y divide-line">
              {Object.entries(prefs.data).map(([k, v]) => (
                <li key={k} className="flex items-center justify-between gap-4 py-3">
                  <span className="text-sm text-ink">{PREF_LABELS[k] ?? k}</span>
                  <Toggle label={PREF_LABELS[k] ?? k} checked={v} onChange={(nv) => savePrefs.mutate({ [k]: nv })} />
                </li>
              ))}
            </ul>
          ) : <TableSkeleton rows={5} cols={2} />}
        </Card>
      ) : (
        <Card padded={false}>
          {list.isError ? <ErrorState error={list.error} onRetry={() => list.refetch()} /> : !list.data ? <TableSkeleton /> : list.data.items.length === 0
            ? <EmptyState icon={<Bell className="h-6 w-6" />} title={tab === 'unread' ? 'You are all caught up' : 'No notifications yet'} />
            : (
              <ul className="divide-y divide-line">
                {list.data.items.map((n) => {
                  const Icon = ICON[n.severity];
                  return (
                    <li key={n.id}>
                      <button className={clsx('w-full text-left flex gap-4 px-5 py-4 hover:bg-surface-2 transition-colors', !n.read_at && 'bg-brand-50/40 dark:bg-brand-900/10')}
                        onClick={() => { if (!n.read_at) read.mutate(n.id); if (n.link) navigate(n.link); }}>
                        <span className={clsx('flex h-9 w-9 shrink-0 items-center justify-center rounded-xl', TONE[n.severity])}><Icon className="h-4 w-4" /></span>
                        <span className="min-w-0 flex-1">
                          <span className="flex items-center gap-2"><span className={clsx('text-sm text-ink', !n.read_at && 'font-semibold')}>{n.title}</span>
                            {!n.read_at && <span className="h-2 w-2 rounded-full bg-accent-500" aria-label="Unread" />}</span>
                          {n.body && <span className="block mt-0.5 text-[13px] text-muted">{n.body}</span>}
                        </span>
                        <span className="shrink-0 text-[12px] text-muted whitespace-nowrap">{relative(n.created_at)}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          {list.data && <Pagination page={page} pages={list.data.pages} total={list.data.total} pageSize={30} onPage={setPage} />}
        </Card>
      )}
    </>
  );
}
