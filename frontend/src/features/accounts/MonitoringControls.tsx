import { useQuery } from '@tanstack/react-query';
import { useEffect, useState, type FormEvent } from 'react';
import { Button, Card, CardHeader, Field, Input } from '@/components/ui/primitives';
import { Notice } from '@/components/ui/layout';
import { useAction } from '@/hooks/useMutationToast';
import { request } from '@/services/http';
import type { Account } from '@/types/api';

interface Monitoring {
  verification_wait_seconds: number;
  email_enabled: boolean;
  recipients: string[];
  capture_statement_screenshot: boolean;
  email_configured: boolean;
}

export function MonitoringControls({ account, editable }: { account: Account; editable: boolean }) {
  const path = `/accounts/${account.id}/monitoring`;
  const q = useQuery({ queryKey: ['monitoring', account.id], queryFn: () => request<Monitoring>(path) });
  const [target, setTarget] = useState(account.low_balance_threshold ?? '');
  const [wait, setWait] = useState('600');
  const [enabled, setEnabled] = useState(false);
  const [recipients, setRecipients] = useState('');
  const [capture, setCapture] = useState(true);
  useEffect(() => { setTarget(account.low_balance_threshold ?? ''); }, [account.low_balance_threshold]);
  useEffect(() => {
    if (!q.data) return;
    setWait(String(q.data.verification_wait_seconds));
    setEnabled(q.data.email_enabled);
    setRecipients(q.data.recipients.join(', '));
    setCapture(q.data.capture_statement_screenshot);
  }, [q.data]);
  const save = useAction(async () => {
    await request(path, { method: 'PUT', body: {
      verification_wait_seconds: Number(wait), email_enabled: enabled,
      recipients: recipients.split(/[,;\n]/).map((s) => s.trim()).filter(Boolean),
      capture_statement_screenshot: capture,
      low_balance_threshold: target.trim() || null,
    } });
  }, { success: 'Monitoring settings saved', invalidate: [['monitoring', account.id], ['account', account.id], ['accounts']] });
  const submit = (event: FormEvent) => { event.preventDefault(); save.mutate(undefined); };
  return <Card className="mb-4">
    <CardHeader title="Balance target & alerts" subtitle="After a verified balance or validated statement is processed, compare it with your target." />
    {q.isLoading && <p role="status" className="text-sm text-muted">Loading balance target settings…</p>}
    {q.isError && <Notice tone="warning">Monitoring settings could not load. <Button variant="outline" size="sm" onClick={() => q.refetch()}>Retry loading settings</Button></Notice>}
    {q.data && <form onSubmit={submit} className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Field label={`Minimum balance target (${account.currency})`} htmlFor="monitor-target" hint="Empty disables balance alerts.">
          <Input id="monitor-target" type="number" min="0" step="0.01" required={enabled} value={target} disabled={!editable} onChange={(e) => setTarget(e.target.value)} />
        </Field>
        <Field label="OTP / verification wait (seconds)" htmlFor="monitor-wait" hint="60–720 seconds; applies to the next bank run.">
          <Input id="monitor-wait" type="number" min="60" max="720" required value={wait} disabled={!editable} onChange={(e) => setWait(e.target.value)} />
        </Field>
        <Field label="Alert email recipients" htmlFor="monitor-recipients" hint="Recipients for this account only; separate multiple emails with commas.">
          <Input id="monitor-recipients" value={recipients} required={enabled} disabled={!editable} onChange={(e) => setRecipients(e.target.value)} placeholder="name@example.com" />
        </Field>
      </div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={enabled} disabled={!editable} onChange={(e) => setEnabled(e.target.checked)} />Email each new verified balance: warning below target, balance report when target is met</label>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={capture} disabled={!editable} onChange={(e) => setCapture(e.target.checked)} />Save a bank statement screen image after a verified statement download</label>
      {!q.data.email_configured && <Notice tone="warning">Email delivery needs SMTP configuration on this computer. In-app alerts still work; email is not connected yet.</Notice>}
      <p className="text-sm text-muted">Statement closing balances show the statement date. They are not live bank balances. Uncertain extraction goes to review before email alerts. Bank screenshots require a supported statement workflow; login and OTP screens are never captured.</p>
      {editable && <Button type="submit" loading={save.isPending}>Save monitoring settings</Button>}
    </form>}
  </Card>;
}
