import { useQuery } from '@tanstack/react-query';
import { KeyRound, Save, Sparkles, Trash2, UserPlus } from 'lucide-react';
import { useEffect, useState, type FormEvent } from 'react';
import { Badge, Button, Card, CardHeader, Field, Input, Select, StatusBadge, Toggle } from '@/components/ui/primitives';
import { DataTable } from '@/components/ui/DataTable';
import { ConfirmDialog, Modal } from '@/components/ui/Modal';
import { KeyValue, Notice, PageHeader, Tabs } from '@/components/ui/layout';
import { TableSkeleton } from '@/components/ui/States';
import { passwordProblems } from '@/features/auth/validation';
import { errorText, useAction } from '@/hooks/useMutationToast';
import { authApi, reconApi, settingsApi, usersApi, type SettingsPayload } from '@/services/api';
import { useAuth } from '@/stores/auth';
import { relative, titleCase } from '@/utils/format';

type Tab = 'organisation' | 'users' | 'security' | 'reconciliation' | 'integrations' | 'data' | 'demo';

function OrgTab({ s, can }: { s: SettingsPayload; can: boolean }) {
  const [f, setF] = useState(s.organisation);
  const [prefs, setPrefs] = useState(s.currency_preferences as Record<string, unknown>);
  useEffect(() => setF(s.organisation), [s]);
  const save = useAction(() => settingsApi.saveOrg({ name: f.name, reporting_currency: f.reporting_currency, timezone: f.timezone,
    country_code: f.country_code || undefined }), { success: 'Organisation saved', invalidate: [['settings'], ['analytics']] });
  const savePrefs = useAction(() => settingsApi.save('currency_preferences', prefs), { success: 'Currency preferences saved' });
  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <Card>
        <CardHeader title="Organisation" subtitle="Tenant-level settings applied to reporting and scheduling" />
        <form onSubmit={(e) => { e.preventDefault(); save.mutate(undefined); }} className="space-y-4">
          <Field label="Name" htmlFor="o-n"><Input id="o-n" disabled={!can} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <div className="grid grid-cols-1 xs:grid-cols-3 gap-4">
            <Field label="Reporting currency" htmlFor="o-c"><Input id="o-c" disabled={!can} maxLength={3} value={f.reporting_currency} onChange={(e) => setF({ ...f, reporting_currency: e.target.value.toUpperCase() })} /></Field>
            <Field label="Country" htmlFor="o-cc"><Input id="o-cc" disabled={!can} maxLength={2} value={f.country_code ?? ''} onChange={(e) => setF({ ...f, country_code: e.target.value.toUpperCase() })} /></Field>
            <Field label="Reporting time zone" htmlFor="o-tz"><Input id="o-tz" disabled={!can} value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })} /></Field>
          </div>
          {can && <div className="flex justify-end"><Button type="submit" loading={save.isPending} icon={<Save className="h-4 w-4" />}>Save</Button></div>}
        </form>
      </Card>
      <Card>
        <CardHeader title="Currency preferences" subtitle="Foreign balances are never converted silently" />
        <div className="space-y-4">
          <div className="flex items-center justify-between rounded-xl border border-line p-3">
            <div><p className="text-sm font-medium text-ink">Always show original currency</p><p className="text-[12px] text-muted">Display source amounts next to converted totals</p></div>
            <Toggle label="Show original" disabled={!can} checked={!!prefs.display_original_currency} onChange={(v) => setPrefs({ ...prefs, display_original_currency: v })} />
          </div>
          <Field label="Exchange-rate source" htmlFor="fx"><Select id="fx" disabled={!can} value={String(prefs.fx_source)} onChange={(e) => setPrefs({ ...prefs, fx_source: e.target.value })}>
            <option value="manual">Manually recorded rates</option></Select></Field>
          {can && <div className="flex justify-end"><Button variant="outline" loading={savePrefs.isPending} onClick={() => savePrefs.mutate(undefined)}>Save preferences</Button></div>}
        </div>
      </Card>
    </div>
  );
}

function UsersTab() {
  const me = useAuth((s) => s.user);
  const [add, setAdd] = useState(false);
  const [f, setF] = useState({ email: '', full_name: '', role: 'analyst', password: '' });
  const users = useQuery({ queryKey: ['users'], queryFn: usersApi.list });
  const roles = useQuery({ queryKey: ['roles'], queryFn: usersApi.roles });
  const create = useAction(() => usersApi.create(f), { success: 'User created', invalidate: [['users']], onSuccess: () => { setAdd(false); setF({ email: '', full_name: '', role: 'analyst', password: '' }); } });
  const update = useAction(({ id, b }: { id: string; b: { role?: string; is_active?: boolean } }) => usersApi.update(id, b), { success: 'User updated', invalidate: [['users']] });
  const pp = passwordProblems(f.password);
  return (
    <>
      <Card padded={false}>
        <div className="p-5 pb-2"><CardHeader title="Users" subtitle="Role-based access with least privilege" action={<Button size="sm" icon={<UserPlus className="h-4 w-4" />} onClick={() => setAdd(true)}>Add user</Button>} /></div>
        <DataTable rows={users.data} rowKey={(u) => u.id} loading={users.isLoading} error={users.error} columns={[
          { key: 'n', header: 'User', mobile: 'title', cell: (u) => <div><div className="font-medium text-ink">{u.full_name}{u.id === me?.id && <span className="text-muted"> (you)</span>}</div>
            <div className="text-[12px] text-muted">{u.email}</div></div> },
          { key: 'r', header: 'Role', cell: (u) => <Select aria-label={`Role for ${u.full_name}`} className="h-8 w-40 text-[13px]" disabled={u.id === me?.id} value={u.role}
            onChange={(e) => update.mutate({ id: u.id, b: { role: e.target.value } })}>
            {roles.data?.map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}</Select> },
          { key: 'l', header: 'Last sign-in', cell: (u) => <span className="text-[13px]">{relative(u.last_login_at)}</span> },
          { key: 'a', header: 'Active', mobile: 'meta', align: 'center', cell: (u) => <Toggle size="sm" label={`Active ${u.full_name}`} disabled={u.id === me?.id}
            checked={u.is_active} onChange={(v) => update.mutate({ id: u.id, b: { is_active: v } })} /> },
        ]} />
      </Card>
      <Card className="mt-4">
        <CardHeader title="Roles & permissions" />
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
          {roles.data?.map((r) => (
            <div key={r.code} className="rounded-xl border border-line p-4">
              <p className="font-medium text-ink">{r.name}</p>
              <div className="mt-2 flex flex-wrap gap-1">{r.permissions.map((p) => <Badge key={p} tone={p.endsWith(':read') ? 'neutral' : 'brand'}>{p}</Badge>)}</div>
            </div>
          ))}
        </div>
      </Card>
      <Modal open={add} onClose={() => setAdd(false)} title="Add user" footer={<><Button variant="outline" onClick={() => setAdd(false)}>Cancel</Button>
        <Button type="submit" form="add-user" loading={create.isPending} disabled={pp.length > 0 || !f.email || f.full_name.length < 2}>Create user</Button></>}>
        <form id="add-user" onSubmit={(e) => { e.preventDefault(); create.mutate(undefined); }} className="space-y-4">
          {create.error && <Notice tone="danger">{errorText(create.error)}</Notice>}
          <Field label="Full name" htmlFor="u-n"><Input id="u-n" value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
          <Field label="Email" htmlFor="u-e"><Input id="u-e" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
          <Field label="Role" htmlFor="u-r"><Select id="u-r" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>
            {roles.data?.map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}</Select></Field>
          <Field label="Initial password" htmlFor="u-p" error={f.password && pp.length ? `Needs ${pp.join(', ')}` : null}>
            <Input id="u-p" type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></Field>
        </form>
      </Modal>
    </>
  );
}

function SecurityTab({ s }: { s: SettingsPayload }) {
  const [f, setF] = useState({ current: '', next: '', confirm: '' });
  const clear = useAuth((x) => x.clear);
  const change = useAction(() => authApi.changePassword(f.current, f.next), { success: 'Password changed — please sign in again',
    onSuccess: () => { clear(); window.location.assign('/login'); } });
  const pp = passwordProblems(f.next);
  const sec = s.security as Record<string, number | boolean>;
  const submit = (e: FormEvent) => { e.preventDefault(); change.mutate(undefined); };
  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <Card>
        <CardHeader title="Change password" icon={<KeyRound className="h-4 w-4" />} subtitle="All your sessions are revoked after a change" />
        <form onSubmit={submit} className="space-y-4">
          {change.error && <Notice tone="danger">{errorText(change.error)}</Notice>}
          <Field label="Current password" htmlFor="p-c"><Input id="p-c" type="password" autoComplete="current-password" value={f.current} onChange={(e) => setF({ ...f, current: e.target.value })} /></Field>
          <Field label="New password" htmlFor="p-n" error={f.next && pp.length ? `Needs ${pp.join(', ')}` : null}>
            <Input id="p-n" type="password" autoComplete="new-password" value={f.next} onChange={(e) => setF({ ...f, next: e.target.value })} /></Field>
          <Field label="Confirm new password" htmlFor="p-x" error={f.confirm && f.confirm !== f.next ? 'Passwords do not match' : null}>
            <Input id="p-x" type="password" autoComplete="new-password" value={f.confirm} onChange={(e) => setF({ ...f, confirm: e.target.value })} /></Field>
          <div className="flex justify-end"><Button type="submit" loading={change.isPending} disabled={!f.current || pp.length > 0 || f.next !== f.confirm}>Change password</Button></div>
        </form>
      </Card>
      <Card>
        <CardHeader title="Security policy" subtitle="Configured on the server via environment variables" />
        <KeyValue items={[
          { label: 'Access token lifetime', value: `${sec.access_token_minutes} minutes` }, { label: 'Session idle timeout', value: `${sec.session_idle_minutes} minutes` },
          { label: 'Failed sign-ins before lockout', value: String(sec.max_failed_logins) }, { label: 'Lockout duration', value: `${sec.lockout_minutes} minutes` },
          { label: 'Password hashing', value: 'Argon2id' }, { label: 'Credential encryption', value: 'AES-256-GCM, tenant-bound' },
          { label: 'Refresh tokens', value: 'Rotated on use, reuse revokes family' }, { label: 'CSRF protection', value: 'Double-submit token' },
        ]} />
      </Card>
    </div>
  );
}

function ReconTab({ can }: { can: boolean }) {
  const q = useQuery({ queryKey: ['recon-rules'], queryFn: reconApi.rules });
  const [f, setF] = useState<Record<string, string | number | boolean>>({});
  useEffect(() => { if (q.data) setF(q.data); }, [q.data]);
  const save = useAction(() => reconApi.saveRules(f), { success: 'Matching rules saved', invalidate: [['recon-rules']] });
  if (!q.data) return <Card padded={false}><TableSkeleton rows={4} /></Card>;
  const num = (k: string, label: string, hint: string, step = '1') => (
    <Field label={label} htmlFor={k} hint={hint}><Input id={k} type="number" step={step} min={0} disabled={!can} value={String(f[k] ?? '')}
      onChange={(e) => setF({ ...f, [k]: e.target.value })} /></Field>);
  return (
    <Card>
      <CardHeader title="Matching tolerances" subtitle="Applied to every new reconciliation run. Past runs keep the rules they used." />
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
        {num('date_tolerance_days', 'Date tolerance (days)', 'Maximum date difference for a match')}
        {num('amount_tolerance_abs', 'Amount tolerance (absolute)', 'Partial matches within this amount are suggested', '0.01')}
        {num('amount_tolerance_pct', 'Amount tolerance (%)', 'Alternative percentage tolerance', '0.1')}
        {num('auto_confirm_threshold', 'Auto-confirm threshold', 'Score (0.5–1.0) required to confirm unique matches', '0.01')}
        {num('balance_tolerance', 'Balance tolerance', 'Allowed rounding difference on balance checks', '0.01')}
        <div className="flex items-center justify-between rounded-xl border border-line p-3">
          <div><p className="text-sm font-medium text-ink">Match by reference</p><p className="text-[12px] text-muted">Use references as the strongest signal</p></div>
          <Toggle label="Match by reference" disabled={!can} checked={!!f.match_references} onChange={(v) => setF({ ...f, match_references: v })} />
        </div>
      </div>
      {can && <div className="mt-4 flex justify-end"><Button loading={save.isPending} icon={<Save className="h-4 w-4" />} onClick={() => save.mutate(undefined)}>Save rules</Button></div>}
    </Card>
  );
}

function DataTab({ s, can }: { s: SettingsPayload; can: boolean }) {
  const sections = [['storage', 'Storage'], ['data_retention', 'Data retention'], ['backup', 'Backup configuration'], ['import_export', 'Import / export']] as const;
  const [vals, setVals] = useState(() => Object.fromEntries(sections.map(([k]) => [k, { ...(s[k] as Record<string, unknown>) }])) as Record<string, Record<string, unknown>>);
  const save = useAction((k: string) => settingsApi.save(k, vals[k]), { success: 'Settings saved', invalidate: [['settings']] });
  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      {sections.map(([k, title]) => (
        <Card key={k}>
          <CardHeader title={title} action={can && <Button size="sm" variant="outline" loading={save.isPending && save.variables === k} onClick={() => save.mutate(k)}>Save</Button>} />
          <div className="grid grid-cols-1 xs:grid-cols-2 gap-4">
            {Object.entries(vals[k]).map(([field, v]) => typeof v === 'boolean' ? (
              <div key={field} className="flex items-center justify-between rounded-xl border border-line p-3 xs:col-span-2">
                <span className="text-sm text-ink">{titleCase(field)}</span>
                <Toggle label={titleCase(field)} disabled={!can} checked={v} onChange={(nv) => setVals({ ...vals, [k]: { ...vals[k], [field]: nv } })} />
              </div>
            ) : (
              <Field key={field} label={titleCase(field)} htmlFor={`${k}-${field}`}>
                <Input id={`${k}-${field}`} disabled={!can} value={String(v ?? '')} type={typeof v === 'number' ? 'number' : 'text'}
                  onChange={(e) => setVals({ ...vals, [k]: { ...vals[k], [field]: typeof v === 'number' ? Number(e.target.value) : e.target.value } })} />
              </Field>
            ))}
          </div>
        </Card>
      ))}
    </div>
  );
}

function DemoTab({ s }: { s: SettingsPayload }) {
  const [confirm, setConfirm] = useState(false);
  const seed = useAction(settingsApi.seedDemo, { success: (r) => `${r.accounts_created} demo accounts created`, invalidate: [['settings'], ['accounts'], ['analytics']] });
  const clear = useAction(settingsApi.clearDemo, { success: (r) => `${r.accounts_removed} demo accounts removed`, invalidate: [['settings'], ['accounts'], ['analytics']],
    onSuccess: () => setConfirm(false) });
  return (
    <Card>
      <CardHeader title="Demo environment" subtitle="Explore the full workflow with synthetic data from the Demo Bank Simulator" action={<Badge tone={s.organisation.demo_mode ? 'demo' : 'neutral'}>{s.organisation.demo_mode ? 'Demo data present' : 'No demo data'}</Badge>} />
      <Notice tone="demo">Creates 10 demo accounts across 5 countries and currencies, illustrative FX rates and a demo cash-book with realistic imperfections. Demo data is labelled everywhere, never presented as real bank data, and excluded when demo is disabled in production.</Notice>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button icon={<Sparkles className="h-4 w-4" />} loading={seed.isPending} disabled={!s.demo_allowed} onClick={() => seed.mutate(undefined)}>Load demo environment</Button>
        <Button variant="outline" icon={<Trash2 className="h-4 w-4" />} onClick={() => setConfirm(true)}>Remove demo accounts</Button>
      </div>
      {!s.demo_allowed && <p className="mt-3 text-[13px] text-muted">Demo mode is disabled in this environment (ALLOW_DEMO=false).</p>}
      <ConfirmDialog open={confirm} onClose={() => setConfirm(false)} loading={clear.isPending} onConfirm={() => clear.mutate(undefined)} destructive
        title="Remove demo accounts?" confirmLabel="Remove" body="Demo accounts are archived and their connections closed. Audit history is retained." />
    </Card>
  );
}

export default function Settings() {
  const can = useAuth((s) => s.can);
  const [tab, setTab] = useState<Tab>('organisation');
  const q = useQuery({ queryKey: ['settings'], queryFn: settingsApi.get });
  const manage = can('settings:manage');
  const tabs: { value: Tab; label: string }[] = [
    { value: 'organisation', label: 'Organisation' }, ...(can('users:manage') ? [{ value: 'users' as Tab, label: 'Users' }] : []),
    { value: 'security', label: 'Security' }, { value: 'reconciliation', label: 'Reconciliation' }, { value: 'integrations', label: 'Integrations' },
    { value: 'data', label: 'Storage & data' }, ...(manage ? [{ value: 'demo' as Tab, label: 'Demo' }] : []),
  ];
  const integ = (q.data?.api_integrations ?? {}) as Record<string, string | boolean>;
  return (
    <>
      <PageHeader eyebrow="Configuration" title="Settings" subtitle={q.data ? `Environment: ${q.data.environment}` : undefined} />
      <div className="mb-4"><Tabs value={tab} onChange={setTab} tabs={tabs} /></div>
      {!q.data ? <Card padded={false}><TableSkeleton /></Card> : (
        <>
          {tab === 'organisation' && <OrgTab s={q.data} can={manage} />}
          {tab === 'users' && <UsersTab />}
          {tab === 'security' && <SecurityTab s={q.data} />}
          {tab === 'reconciliation' && <ReconTab can={manage} />}
          {tab === 'integrations' && (
            <Card>
              <CardHeader title="API integration settings" subtitle="Provider secrets are configured as server environment variables and never exposed to the browser" />
              <KeyValue items={[
                { label: 'Plaid', value: <StatusBadge status={integ.plaid_configured ? 'active' : 'pending'} /> }, { label: 'Plaid environment', value: String(integ.plaid_env ?? '—') },
                { label: 'GoCardless Bank Account Data', value: <StatusBadge status={integ.gocardless_configured ? 'active' : 'pending'} /> },
                { label: 'Statement upload', value: <StatusBadge status="active" /> },
              ]} />
              <p className="mt-4 text-[13px] text-muted">Set <code className="kbd">PLAID_CLIENT_ID</code>, <code className="kbd">PLAID_SECRET</code>, <code className="kbd">GOCARDLESS_SECRET_ID</code> and <code className="kbd">GOCARDLESS_SECRET_KEY</code> in <code className="kbd">backend/.env</code>, then restart. See docs/BANK_INTEGRATIONS.md.</p>
            </Card>
          )}
          {tab === 'data' && <DataTab s={q.data} can={manage} />}
          {tab === 'demo' && <DemoTab s={q.data} />}
        </>
      )}
    </>
  );
}
