import { useEffect, useState, type FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button, Field, Input, Select } from '@/components/ui/primitives';
import { Notice } from '@/components/ui/layout';
import { request } from '@/services/http';
import { useAction } from '@/hooks/useMutationToast';

interface MailSettings {
  configured: boolean; host: string; port: number; sender: string;
  username: string; security: 'starttls' | 'ssl'; password_saved: boolean;
}

export function AccountMailSettings({ accountId }: { accountId: string }) {
  const path = `/accounts/${accountId}/mail-settings`;
  const q = useQuery({ queryKey: ['account-mail', accountId], queryFn: () => request<MailSettings>(path) });
  const [form, setForm] = useState({ host: '', port: 587, sender: '', username: '', security: 'starttls' });
  const [password, setPassword] = useState('');
  useEffect(() => {
    if (q.data) {
      const { host, port, sender, username, security } = q.data;
      setForm({ host, port, sender, username, security });
      setPassword('');
    }
  }, [q.data]);
  const save = useAction(async () => {
    await request(path, { method: 'PUT', body: { ...form, ...(password ? { password } : {}) } });
    setPassword('');
  }, { success: 'Account sender settings saved', invalidate: [['account-mail', accountId], ['monitoring', accountId]] });
  const submit = (e: FormEvent) => { e.preventDefault(); save.mutate(undefined); };
  return <details className="mt-4 border-t pt-4">
    <summary className="cursor-pointer font-medium">Sender email / SMTP for this account</summary>
    <p className="my-3 text-sm text-muted">Each bank account can use its own sender. Alerts go to the recipients saved above. Use your mail provider’s SMTP details and app password.</p>
    {q.isError && <Notice tone="warning">Sender settings could not load. <Button type="button" onClick={() => q.refetch()}>Retry</Button></Notice>}
    {q.data && <form onSubmit={submit} className="space-y-3">
      <p className="text-sm">{q.data.configured ? 'Account sender saved; delivery is confirmed only after a successful email job.' : 'No sender saved for this account.'}</p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field label="SMTP host" htmlFor={`smtp-host-${accountId}`}><Input id={`smtp-host-${accountId}`} required value={form.host} onChange={e => setForm({ ...form, host: e.target.value })} placeholder="Mail provider SMTP hostname" /></Field>
        <Field label="SMTP port" htmlFor={`smtp-port-${accountId}`}><Input id={`smtp-port-${accountId}`} type="number" min="1" max="65535" required value={form.port} onChange={e => setForm({ ...form, port: Number(e.target.value) })} /></Field>
        <Field label="Sender email" htmlFor={`smtp-from-${accountId}`}><Input id={`smtp-from-${accountId}`} type="email" required value={form.sender} onChange={e => setForm({ ...form, sender: e.target.value })} /></Field>
        <Field label="SMTP username" htmlFor={`smtp-user-${accountId}`}><Input id={`smtp-user-${accountId}`} autoComplete="off" value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} /></Field>
        <Field label="SMTP app password" htmlFor={`smtp-password-${accountId}`} hint={q.data.password_saved ? 'Password saved. Leave blank to keep it for the same server and username.' : 'Stored encrypted; never shown after saving.'}><Input id={`smtp-password-${accountId}`} type="password" autoComplete="new-password" value={password} onChange={e => setPassword(e.target.value)} /></Field>
        <Field label="Connection security" htmlFor={`smtp-security-${accountId}`}><Select id={`smtp-security-${accountId}`} value={form.security} onChange={e => setForm({ ...form, security: e.target.value })}><option value="starttls">STARTTLS</option><option value="ssl">SSL / TLS</option></Select></Field>
      </div>
      <Button type="submit" loading={save.isPending}>Save account sender</Button>
    </form>}
  </details>;
}
