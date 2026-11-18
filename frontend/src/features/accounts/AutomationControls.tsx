import { Pause, Play, PlugZap, Power, PowerOff, RefreshCw } from 'lucide-react';
import { Button, StatusBadge } from '@/components/ui/primitives';
import { KeyValue } from '@/components/ui/layout';
import { useAction } from '@/hooks/useMutationToast';
import { accountsApi } from '@/services/api';
import { useAuth } from '@/stores/auth';
import type { Account, AutomationView } from '@/types/api';
import { dateTime, relative } from '@/utils/format';

const COLLECTABLE = new Set(['ready', 'scheduled', 'healthy', 'degraded', 'failed', 'paused']);

export function AutomationStatusBadge({ view }: { view: AutomationView | null | undefined }) {
  return <StatusBadge status={view?.status ?? 'not_configured'} />;
}

/** Per-account automation actions. Buttons only appear when the action is genuinely possible. */
export function AutomationActions({ account, onConnect, size = 'sm' }: { account: Account; onConnect?: () => void;
  size?: 'sm' | 'md' }) {
  const can = useAuth((s) => s.can);
  const inv = [['account', account.id], ['accounts'], ['automation'], ['jobs'], ['sync-history', account.id]];
  const act = useAction((a: 'enable' | 'pause' | 'resume' | 'disable') => accountsApi.automationAction(account.id, a), {
    invalidate: inv, success: (a) => a.automation_paused ? 'Automation paused' : a.automation_enabled ? 'Automation on' : 'Automation off' });
  const sync = useAction(() => accountsApi.syncNow(account.id), { invalidate: inv,
    success: (r) => (r.created ? 'Synchronisation queued' : 'A synchronisation is already in progress') });
  const v = account.automation;
  const status = v?.status ?? 'not_configured';
  const needsConnect = ['not_configured', 'authorization_required', 'provider_setup_required'].includes(status)
    || v?.action_required === 'authorize';
  const manage = can('automation:manage');
  const browser = account.connection?.provider_code === 'browser_automation';

  return (
    <div className="flex flex-wrap gap-2" onClick={(e) => e.stopPropagation()}>
      {needsConnect && can('connections:manage') && onConnect && (
        <Button size={size} icon={<PlugZap className="h-3.5 w-3.5" />} onClick={onConnect}>
          {status === 'authorization_required' || v?.action_required === 'authorize' ? 'Authorise' : 'Connect bank'}</Button>
      )}
      {manage && COLLECTABLE.has(status) && (
        <Button size={size} variant="outline" icon={<RefreshCw className="h-3.5 w-3.5" />} loading={sync.isPending}
          onClick={() => sync.mutate(undefined)}>Run sync now</Button>
      )}
      {manage && (browser || !needsConnect) && status !== 'waiting_for_user' && (!account.automation_enabled ? (
        <Button size={size} variant="outline" icon={<Power className="h-3.5 w-3.5" />} onClick={() => act.mutate('enable')}>Enable automation</Button>
      ) : account.automation_paused ? (
        <Button size={size} variant="outline" icon={<Play className="h-3.5 w-3.5" />} onClick={() => act.mutate('resume')}>Resume</Button>
      ) : (
        <Button size={size} variant="outline" icon={<Pause className="h-3.5 w-3.5" />} onClick={() => act.mutate('pause')}>Pause</Button>
      ))}
      {manage && account.automation_enabled && (browser || !needsConnect) && (
        <Button size={size} variant="ghost" icon={<PowerOff className="h-3.5 w-3.5" />} onClick={() => act.mutate('disable')}>Disable</Button>
      )}
    </div>
  );
}

export function AutomationSummary({ account }: { account: Account }) {
  const v = account.automation;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2"><AutomationStatusBadge view={v} />
        <span className="text-[13px] text-muted">{v?.reason}</span></div>
      <KeyValue items={[
        { label: 'Next scheduled sync', value: v?.next_sync_at ? dateTime(v.next_sync_at) : '—' },
        { label: 'Last successful sync', value: account.last_successful_sync_at ? dateTime(account.last_successful_sync_at) : 'Never' },
        { label: 'Last attempted sync', value: account.last_attempted_sync_at ? relative(account.last_attempted_sync_at) : 'Never' },
        { label: 'Schedule', value: account.sync_schedule ? `${account.sync_schedule} (${account.timezone ?? 'org time zone'})` : 'Organisation schedule' },
        { label: 'Retry status', value: v?.retry ? `Attempt ${v.retry.attempt}/${v.retry.max_attempts} · next ${relative(v.retry.next_attempt_at)}` : 'None' },
        { label: 'Last error', value: account.last_sync_error ?? 'None' },
      ]} />
    </div>
  );
}
