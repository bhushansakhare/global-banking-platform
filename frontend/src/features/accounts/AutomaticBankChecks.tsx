import { useEffect, useState, type FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Clock3, Play, Pause } from 'lucide-react';
import { Badge, Button, Card, CardHeader, Field, Input } from '@/components/ui/primitives';
import { Notice } from '@/components/ui/layout';
import { accountsApi, automationApi } from '@/services/api';
import { useAction } from '@/hooks/useMutationToast';
import { useAuth } from '@/stores/auth';
import { dateTime } from '@/utils/format';
import type { Account } from '@/types/api';

export function AutomaticBankChecks({ account }: { account: Account }) {
  const can = useAuth(s => s.can);
  const editable = can('accounts:write') && can('automation:manage');
  const q = useQuery({ queryKey: ['automation'], queryFn: automationApi.overview,
    enabled: can('automation:read'), refetchInterval: 10000 });
  const [time, setTime] = useState('');
  const [zone, setZone] = useState(account.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC');
  useEffect(() => {
    const match = account.sync_schedule?.match(/^(\d{1,2}) (\d{1,2}) \* \* \*$/);
    setTime(match ? `${match[2].padStart(2, '0')}:${match[1].padStart(2, '0')}` : '');
    setZone(account.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC');
  }, [account.id, account.sync_schedule, account.timezone]);
  const inv = [['account', account.id], ['accounts'], ['automation'], ['jobs']];
  const save = useAction(async () => {
    const [hour, minute] = time.split(':').map(Number);
    await accountsApi.update(account.id, { sync_schedule: `${minute} ${hour} * * *`, timezone: zone });
    await automationApi.master(true);
    await accountsApi.automationAction(account.id, 'enable');
  }, { success: 'Automatic daily bank checks enabled', invalidate: inv });
  const pause = useAction(() => accountsApi.automationAction(account.id, 'pause'),
    { success: 'Automatic checks paused for this account', invalidate: inv });
  const run = useAction(() => accountsApi.syncNow(account.id),
    { success: r => r.created ? 'Bank check queued. Approve QR / OTP in the bank window.' : 'A bank check is already in progress', invalidate: inv });
  const global = q.data?.settings;
  const on = !!global?.master_enabled && !global.paused && account.automation_enabled && !account.automation_paused;
  const submit = (e: FormEvent) => { e.preventDefault(); save.mutate(undefined); };
  return <Card className="mb-4">
    <CardHeader title="Automatic bank checks" icon={<Clock3 className="h-4 w-4" />}
      subtitle="Choose a time once. Your balance, saved screenshot and email update after each approved bank login."
      action={<Badge tone={on ? 'success' : 'neutral'} dot>{on ? 'Schedule on' : 'Schedule off'}</Badge>} />
    <div className="mb-5 grid gap-3 sm:grid-cols-3">
      {['1. Bank window opens at your time', '2. Approve QR / OTP if requested', '3. Balance checked and email sent'].map(label =>
        <div key={label} className="rounded-xl border border-line bg-slate-50 p-3 text-sm dark:bg-slate-900">{label}</div>)}
    </div>
    <form onSubmit={submit} className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Daily bank check time" htmlFor={`check-time-${account.id}`} hint="Choose any time for this account.">
          <Input id={`check-time-${account.id}`} type="time" required value={time} disabled={!editable} onChange={e => setTime(e.target.value)} />
        </Field>
        <Field label="Time zone" htmlFor={`check-zone-${account.id}`} hint="For example: Asia/Kolkata">
          <Input id={`check-zone-${account.id}`} required value={zone} disabled={!editable} onChange={e => setZone(e.target.value)} />
        </Field>
      </div>
      {account.sync_schedule && !time && <Notice>This account uses a custom schedule. Saving a daily time replaces it; the Automation Center keeps the advanced controls.</Notice>}
      <p className="text-sm text-muted">Next check: <strong>{on && account.automation?.next_sync_at ? dateTime(account.automation.next_sync_at) : 'No active run scheduled'}</strong></p>
      <p className="text-sm text-muted">Keep this computer awake and the software running. Approve QR / OTP in the bank window or bank app. Saving enables automatic runs for this account and other enabled accounts; it does not start a bank login immediately.</p>
      {q.isError && <Notice tone="warning">Schedule status could not load. <Button type="button" size="sm" onClick={() => q.refetch()}>Retry</Button></Notice>}
      {editable && <div className="flex flex-wrap gap-2">
        <Button type="submit" loading={save.isPending} disabled={!time || q.isLoading || q.isError}>Save & enable automatic checks</Button>
        {on && <Button type="button" variant="outline" loading={pause.isPending} icon={<Pause className="h-4 w-4" />} onClick={() => pause.mutate(undefined)}>Pause this account</Button>}
        <Button type="button" variant="outline" loading={run.isPending} icon={<Play className="h-4 w-4" />} onClick={() => run.mutate(undefined)}>Check now</Button>
      </div>}
    </form>
  </Card>;
}
