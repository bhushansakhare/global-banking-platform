import { useQuery } from '@tanstack/react-query';
import clsx from 'clsx';
import { KeyRound, RotateCcw, XCircle } from 'lucide-react';
import { useState } from 'react';
import { Button, Field, Input, StatusBadge } from '@/components/ui/primitives';
import { Modal } from '@/components/ui/Modal';
import { KeyValue, Notice } from '@/components/ui/layout';
import { TableSkeleton } from '@/components/ui/States';
import { useAction } from '@/hooks/useMutationToast';
import { automationApi } from '@/services/api';
import { useAuth } from '@/stores/auth';
import { dateTime, titleCase } from '@/utils/format';
import { JOB_LABELS } from '@/utils/status';

const LEVEL = { info: 'text-muted', warning: 'text-amber-600 dark:text-amber-400', error: 'text-red-600 dark:text-red-400' } as Record<string, string>;

export function JobDetailModal({ jobId, onClose }: { jobId: string | null; onClose: () => void }) {
  const can = useAuth((s) => s.can);
  const [otp, setOtp] = useState('');
  const q = useQuery({ queryKey: ['job', jobId], queryFn: () => automationApi.job(jobId!), enabled: !!jobId,
    refetchInterval: (query) => (['running', 'queued', 'retry_scheduled'].includes(query.state.data?.status ?? '') ? 3000 : false) });
  const inv = [['job', jobId], ['jobs'], ['automation']];
  const retry = useAction(() => automationApi.retry(jobId!), { invalidate: inv, success: 'Job re-queued' });
  const cancel = useAction(() => automationApi.cancel(jobId!), { invalidate: inv, success: 'Cancellation requested' });
  const resume = useAction(() => automationApi.resumeJob(jobId!, otp || undefined), { invalidate: inv, success: 'Job resumed',
    onSuccess: () => setOtp('') });
  const j = q.data;

  return (
    <Modal open={!!jobId} onClose={onClose} size="xl" title={j ? `${JOB_LABELS[j.job_type] ?? j.job_type}${j.account_name ? ` · ${j.account_name}` : ''}` : 'Job'}
      description={j ? `Job ${j.id.slice(0, 8)} · ${titleCase(j.trigger)} trigger` : undefined}
      footer={j && can('automation:manage') && <>
        {['queued', 'running', 'retry_scheduled', 'awaiting_user'].includes(j.status) &&
          <Button variant="outline" icon={<XCircle className="h-4 w-4" />} loading={cancel.isPending} onClick={() => cancel.mutate(undefined)}>Cancel job</Button>}
        {['failed', 'cancelled'].includes(j.status) &&
          <Button icon={<RotateCcw className="h-4 w-4" />} loading={retry.isPending} onClick={() => retry.mutate(undefined)}>Retry</Button>}
      </>}>
      {!j ? <TableSkeleton rows={4} /> : (
        <div className="space-y-5">
          {j.status === 'awaiting_user' && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 dark:border-amber-900/60 dark:bg-amber-950/30 p-4">
              <p className="text-sm font-semibold text-amber-900 dark:text-amber-100">Your action is required: {j.requires_user_action?.toUpperCase()}</p>
              <p className="mt-1 text-[13px] text-amber-900/80 dark:text-amber-100/80">{j.error_message}</p>
              <p className="mt-1 text-[12px] text-amber-900/70 dark:text-amber-100/70">MFA, OTP and CAPTCHA steps are never bypassed. The passcode is handed to the worker in memory and never stored or logged.</p>
              {can('automation:manage') && (
                <div className="mt-3 flex flex-col xs:flex-row gap-2 xs:items-end">
                  {j.requires_user_action === 'otp' && (
                    <div className="flex-1"><Field label="One-time passcode" htmlFor="otp">
                      <Input id="otp" inputMode="numeric" autoComplete="one-time-code" maxLength={12} value={otp}
                        onChange={(e) => setOtp(e.target.value.replace(/\s/g, ''))} placeholder="e.g. 123456" />
                    </Field></div>
                  )}
                  <Button icon={<KeyRound className="h-4 w-4" />} loading={resume.isPending}
                    disabled={j.requires_user_action === 'otp' && otp.length < 4} onClick={() => resume.mutate(undefined)}>
                    {j.requires_user_action === 'otp' ? 'Submit and resume' : 'I have completed this — resume'}</Button>
                </div>
              )}
            </div>
          )}
          <KeyValue cols={4} items={[
            { label: 'Status', value: <StatusBadge status={j.status} /> }, { label: 'Attempts', value: `${j.attempts} / ${j.max_attempts}` },
            { label: 'Started', value: dateTime(j.started_at) }, { label: 'Finished', value: dateTime(j.finished_at) },
            { label: 'Next attempt', value: j.status === 'retry_scheduled' ? dateTime(j.run_after) : '—' },
            { label: 'Duration', value: j.duration_seconds !== null ? `${j.duration_seconds.toFixed(1)} s` : '—' },
            { label: 'Progress', value: `${j.progress}%${j.progress_message ? ` · ${j.progress_message}` : ''}` },
            { label: 'Error code', value: j.error_code ?? '—' },
          ]} />
          {j.error_message && j.status !== 'awaiting_user' && <Notice tone={j.status === 'failed' ? 'danger' : 'warning'} title="Error details">{j.error_message}</Notice>}
          {Object.keys(j.result ?? {}).length > 0 && (
            <div><p className="label">Result</p>
              <div className="flex flex-wrap gap-2">{Object.entries(j.result).map(([k, v]) => (
                <span key={k} className="rounded-lg border border-line bg-surface-2 px-2.5 py-1 text-[12px]"><span className="text-muted">{titleCase(k)}:</span> <span className="num text-ink">{String(v)}</span></span>
              ))}</div></div>
          )}
          {j.children.length > 0 && (
            <div><p className="label">Pipeline jobs</p>
              <ul className="divide-y divide-line rounded-xl border border-line">{j.children.map((c) => (
                <li key={c.id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                  <span className="text-ink">{JOB_LABELS[c.job_type] ?? c.job_type}</span><StatusBadge status={c.status} /></li>))}</ul></div>
          )}
          <div>
            <p className="label">Execution log</p>
            <ol className="max-h-72 overflow-y-auto rounded-xl border border-line bg-surface-2/60 p-3 font-mono text-[12px] space-y-1.5">
              {j.logs.map((l) => (
                <li key={l.id} className="flex gap-3">
                  <span className="shrink-0 text-muted/80">{new Date(l.ts).toLocaleTimeString('en-GB')}</span>
                  <span className={clsx('shrink-0 uppercase w-14', LEVEL[l.level])}>{l.level}</span>
                  <span className="text-ink break-words min-w-0">{l.message}</span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}
    </Modal>
  );
}
