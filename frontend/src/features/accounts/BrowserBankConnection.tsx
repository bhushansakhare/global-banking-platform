import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Badge, Button, Field, Input, Select, StatusBadge } from '@/components/ui/primitives';
import { Notice } from '@/components/ui/layout';
import { errorText, useAction } from '@/hooks/useMutationToast';
import { browserBankingApi, automationApi } from '@/services/api';
import type { Account } from '@/types/api';
import { useAuth } from '@/stores/auth';

export function BrowserBankConnection({ account }: { account: Account }) {
  const q = useQuery({ queryKey: ['bank-browser', account.id], queryFn: () => browserBankingApi.status(account.id),
    refetchInterval: 3000 });
  const [selected, setSelected] = useState('');
  const [customer, setCustomer] = useState('');
  const [password, setPassword] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [search, setSearch] = useState('');
  const [editing, setEditing] = useState(false);
  const [config, setConfig] = useState('');
  const [configError, setConfigError] = useState<string | null>(null);
  const invalidations = [['bank-browser', account.id], ['account', account.id], ['accounts'], ['automation']];
  const start = useAction((loginOnly: boolean) => browserBankingApi.start(account.id, {
    profile_key: selected || q.data?.profile_key || '', customer_id: customer || undefined,
    login_only: loginOnly, date_from: from || undefined, date_to: to || undefined,
  }), { invalidate: invalidations, onSuccess: () => setCustomer(''), success: 'Bank browser request queued' });
  const cancel = useAction((id: string) => automationApi.cancel(id), { invalidate: invalidations });
  const saveLogin = useAction(() => browserBankingApi.saveLogin(account.id, {
    profile_key: selected || q.data?.profile_key || '', customer_id: customer.trim(), password: password || undefined,
  }), { invalidate: invalidations, success: 'Bank login saved encrypted', onSuccess: () => { setCustomer(''); setPassword(''); } });
  const forgetLogin = useAction(() => browserBankingApi.forgetLogin(account.id), {
    invalidate: invalidations, success: 'Saved bank login removed', onSuccess: () => { setCustomer(''); setPassword(''); },
  });
  const authenticate = useAction(() => browserBankingApi.start(account.id, {
    profile_key: selected || q.data?.profile_key || '', login_only: !profile?.capabilities.automatic_statement_retrieval,
    authenticate: true, date_from: from || undefined, date_to: to || undefined,
  }), { invalidate: invalidations, success: 'Authentication queued — finish any bank verification in the visible browser' });
  const qrLogin = useAction(() => browserBankingApi.start(account.id, {
    profile_key: selected || q.data?.profile_key || '', login_only: !profile?.capabilities.automatic_statement_retrieval,
    qr_login: true, date_from: from || undefined, date_to: to || undefined,
  }), { invalidate: invalidations, success: 'Bank window queued — choose the bank’s QR login and approve in its official app' });
  const save = useAction((body: { key: string }) => browserBankingApi.saveProfile(body.key, body), {
    invalidate: invalidations, onSuccess: () => { setEditing(false); setConfigError(null); }, success: 'Bank profile saved',
  });
  const profile = q.data?.profiles.find((p) => p.key === (selected || q.data?.profile_key));
  const busy = q.data?.job && ['queued', 'running', 'retry_scheduled'].includes(q.data.job.status);
  const canConfigure = useAuth((s) => s.user?.permissions.includes('settings:manage') ?? false);
  const canConnect = useAuth((s) => s.user?.permissions.includes('connections:manage') ?? false);
  const canCancel = useAuth((s) => s.user?.permissions.includes('automation:manage') ?? false);
  if (account.is_demo) return null;
  return <section className="rounded-xl border border-brand-500 p-4 space-y-4" aria-label="Bank website connection">
    <div><div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold text-ink">Connect through your bank website</h3><Badge tone="info">Visible browser (headed)</Badge></div>
      <p className="text-sm text-muted">Choose QR / bank app login, or save your bank login and Authenticate. Complete verification in the official bank window or banking app.</p></div>
    {q.data?.enabled && <p className="text-sm text-muted">The visible bank window waits up to {q.data.verification_wait_seconds ?? 600} seconds for OTP, CAPTCHA or app approval. Complete verification there; do not enter OTP in this app.</p>}
    {q.error && <Notice tone="danger">{errorText(q.error)}</Notice>}
    <Field label="Search bank" htmlFor="browser-bank-search"><Input id="browser-bank-search" value={search}
      onChange={(e) => setSearch(e.target.value)} placeholder="Bank name, code or website" /></Field>
    <Field label={`Bank profile · ${account.country_code}`} htmlFor="browser-bank"><Select id="browser-bank"
      value={selected || q.data?.profile_key || ''} onChange={(e) => { setSelected(e.target.value); setCustomer(''); setPassword(''); }}>
      <option value="">Select bank profile</option>
      {q.data?.profiles.filter((p) => `${p.name} ${p.key} ${p.website}`.toLowerCase().includes(search.toLowerCase()))
        .map((p) => <option key={p.key} value={p.key}>{p.name} · {p.permission}</option>)}
    </Select></Field>
    {!q.isLoading && !q.data?.profiles.length && <Notice>No browser profile configured for this country yet.</Notice>}
    {profile && <>
      <div className="flex flex-wrap items-center gap-2 text-sm"><StatusBadge status={profile.capabilities.browser_login} />
        <a className="link break-all" href={profile.login_url} target="_blank" rel="noopener noreferrer">Official bank login</a></div>
      <div className="rounded-lg bg-surface-2 p-3 space-y-2">
        <Button variant="outline" disabled={!canConnect || !q.data?.enabled || !!busy} loading={qrLogin.isPending}
          onClick={() => qrLogin.mutate(undefined)}>QR / bank app login</Button>
        <p className="text-sm text-muted">If your bank offers QR login, choose its QR option in the visible bank window, then scan and approve with its official banking app. Saved ID/password are not submitted in this mode. After approval, a supported statement workflow continues automatically.</p>
        {profile.key === 'hdfc_bank' && <a className="link text-sm" href="https://www.hdfc.bank.in/resources/way-to-bank/online-banking/net-banking/two-step-verification" target="_blank" rel="noopener noreferrer">HDFC QR login instructions</a>}
      </div>
      <Field label="Customer ID / User ID" htmlFor="browser-customer"
        hint={q.data?.has_customer_id ? 'An encrypted identifier is saved. Leave blank to keep it.' :
          profile.capabilities.customer_id_autofill ? 'Enter once to save encrypted and auto-fill the verified bank login field.' : 'Stored encrypted. Auto-fill requires a reviewed login field.'}>
        <Input id="browser-customer" autoComplete="off" value={customer} maxLength={120} onChange={(e) => setCustomer(e.target.value)} />
      </Field>
      {profile.capabilities.saved_login && <>
        {profile.credential_storage_notice && <Notice tone="warning">{profile.credential_storage_notice}{' '}
          {profile.credential_storage_reference && <a className="link" href={profile.credential_storage_reference}
            target="_blank" rel="noopener noreferrer">Bank terms</a>}</Notice>}
        <Field label="Bank password / IPIN" htmlFor="browser-password"
          hint={q.data?.has_password && profile.key === q.data.profile_key ? 'A password is saved encrypted. Enter a Customer ID to update the saved login.' : 'Saved encrypted for this bank only. Never returned by the API.'}>
          <Input id="browser-password" type="password" autoComplete="new-password" value={password} maxLength={1024}
            onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" disabled={!canConnect || !!busy || !customer.trim()} loading={saveLogin.isPending}
            onClick={() => saveLogin.mutate(undefined)}>Save bank login</Button>
          <Button disabled={!canConnect || !q.data?.enabled || !!busy || !q.data?.has_password || profile.key !== q.data.profile_key || !!customer || !!password}
            loading={authenticate.isPending} onClick={() => authenticate.mutate(undefined)}>Authenticate</Button>
          {q.data?.has_password && profile.key === q.data.profile_key && <Button variant="ghost" disabled={!canConnect || !!busy}
            loading={forgetLogin.isPending} onClick={() => forgetLogin.mutate(undefined)}>Forget saved login</Button>}
        </div>
        {(saveLogin.error || authenticate.error || forgetLogin.error) && <Notice tone="danger">{errorText(saveLogin.error || authenticate.error || forgetLogin.error)}</Notice>}
      </>}
      <div className="grid sm:grid-cols-2 gap-3">
        <Field label="Statement from" htmlFor="browser-from"><Input id="browser-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
        <Field label="Statement to" htmlFor="browser-to"><Input id="browser-to" type="date" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
      </div>
      {!profile.capabilities.automatic_statement_retrieval && <Notice tone="warning" title="Statement automation requires profile review">
        {profile.limitations.join(' ') || 'Bank permission and statement navigation have not been verified.'}
        {' '}Opening login does not mark this account connected or synced.
      </Notice>}
      {!q.data?.enabled && <Notice tone="warning">Local bank browser is disabled. Enable BROWSER_AUTOMATION_ENABLED on this Windows desktop and restart the backend.</Notice>}
      <div className="flex flex-wrap gap-2">
        <Button disabled={!canConnect || !q.data?.enabled || !!busy} loading={start.isPending} onClick={() => start.mutate(true)}>Open bank login</Button>
        <Button disabled={!canConnect || !q.data?.enabled || !!busy || !profile.capabilities.automatic_statement_retrieval}
          loading={start.isPending} onClick={() => start.mutate(false)}>Connect and retrieve statements</Button>
        {canCancel && busy && q.data?.job && <Button variant="outline" onClick={() => cancel.mutate(q.data!.job!.id)}>Close bank session</Button>}
      </div>
      {start.error && <Notice tone="danger">{errorText(start.error)}</Notice>}
      {qrLogin.error && <Notice tone="danger">{errorText(qrLogin.error)}</Notice>}
    </>}
    {q.data?.job && <div role="status" className="text-sm space-y-1"><StatusBadge status={q.data.job.status} />
      <p>{q.data.job.error || q.data.job.message}</p>
      {q.data.job.error_code === 'bank_authorization_denied' && <Notice tone="warning" title="Bank declined this session">
        Automatic retries are paused. Use Open bank login for a fresh session and complete login manually.
        If normal bank login also fails, contact the bank. If it succeeds, this bank's automated flow needs review.
      </Notice>}
      {q.data.job.result?.data_retrieved === true && <p>Statements retrieved. Processing jobs update transactions and statement balances.</p>}
    </div>}
    {canConfigure && <details onToggle={(e) => setEditing(e.currentTarget.open)} open={editing}>
      <summary className="cursor-pointer text-sm text-muted">Configure another bank profile</summary>
      <p className="text-xs text-muted my-2">Add a reviewed JSON profile with official domains, permission reference and read-only statement selectors. Saved profiles apply to your organisation.</p>
      {profile && <Button variant="ghost" size="sm" onClick={() => {
        const { capabilities: _capabilities, ...body } = profile;
        void _capabilities;
        setConfig(JSON.stringify(body, null, 2));
      }}>Load selected profile as template</Button>}
      <textarea aria-label="Bank profile JSON" className="w-full rounded-lg border border-line bg-surface-2 p-3 font-mono text-xs"
        rows={8} value={config} onChange={(e) => setConfig(e.target.value)} placeholder={'{"key":"your_bank","name":"Your Bank","country":"IN",...}'} />
      {(configError || save.error) && <Notice tone="danger">{configError || errorText(save.error)}</Notice>}
      <Button variant="outline" loading={save.isPending} onClick={() => {
        try { const body = JSON.parse(config) as { key: string }; setConfigError(null); save.mutate(body); }
        catch { setConfigError('Enter valid bank profile JSON'); }
      }}>Save bank profile</Button>
    </details>}
  </section>;
}
