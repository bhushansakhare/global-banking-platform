import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { Badge, Button, Field, Input, Select, Toggle } from '@/components/ui/primitives';
import { Modal } from '@/components/ui/Modal';
import { Notice } from '@/components/ui/layout';
import { TableSkeleton } from '@/components/ui/States';
import { MethodCard } from '@/features/accounts/ConnectionSetupModal';
import { errorText } from '@/hooks/useMutationToast';
import { useDebounce } from '@/hooks/useDebounce';
import { accountsApi, browserBankingApi, institutionsApi, type AccountInput } from '@/services/api';
import { useQueryClient } from '@tanstack/react-query';
import type { Account } from '@/types/api';

const TYPES = ['savings', 'current', 'deposit', 'credit_card', 'loan', 'escrow', 'other'];
const OTHER = '__other__';

/**
 * Add bank account — country → bank → genuinely supported connection methods → details → automation.
 * After creation the user is taken to the account to authorise the connection.
 */
export function AddAccountModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void;
  onCreated?: (a: Account) => void }) {
  const qc = useQueryClient();
  const [country, setCountry] = useState('');
  const [instId, setInstId] = useState('');
  const [otherName, setOtherName] = useState('');
  const [provider, setProvider] = useState<string | null>(null);
  const [scheduleMode, setScheduleMode] = useState<'org' | 'custom'>('org');
  const [f, setF] = useState<AccountInput>({ institution_id: '', nickname: '', currency: '', account_type: 'savings',
    automation_enabled: true, statement_period: 'monthly' });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const countries = useQuery({ queryKey: ['countries'], queryFn: institutionsApi.countries, enabled: open });
  const insts = useQuery({ queryKey: ['institutions', 'pick', country], enabled: open && !!country,
    queryFn: () => institutionsApi.list({ country, page_size: 200 }) });
  const browserProfiles = useQuery({ queryKey: ['browser-bank-directory', country], enabled: open && !!country,
    queryFn: () => browserBankingApi.profiles({ country }) });
  const inst = useMemo(() => insts.data?.items.find((i) => i.id === instId), [insts.data, instId]);
  const bankName = useDebounce(instId === OTHER ? otherName.trim() : '');
  const methods = useQuery({
    queryKey: ['bank-methods', country, instId, bankName], enabled: open && !!country && (!!inst || bankName.length >= 2),
    queryFn: () => institutionsApi.connectionMethods({ country, institution_id: inst?.id, bank_name: inst ? undefined : bankName }) });

  useEffect(() => {
    if (!open) {
      setCountry(''); setInstId(''); setOtherName(''); setProvider(null); setErr(null); setScheduleMode('org');
      setF({ institution_id: '', nickname: '', currency: '', account_type: 'savings', automation_enabled: true, statement_period: 'monthly' });
    }
  }, [open]);
  useEffect(() => {
    const c = countries.data?.find((x) => x.code === country);
    if (c) setF((x) => ({ ...x, currency: inst?.supported_currencies[0] ?? c.default_currency }));
  }, [country, inst, countries.data]);
  useEffect(() => {
    const opts = methods.data ?? [];
    const best = opts.find((m) => m.recommended) ?? opts.find((m) => m.status === 'provider_setup_required' && m.automatic)
      ?? opts.find((m) => m.available && m.provider_code);
    setProvider(best?.provider_code ?? null);
  }, [methods.data]);

  const chosen = methods.data?.find((m) => m.provider_code === provider);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!country) return setErr('Choose a country');
    if (!inst && !(instId === OTHER && otherName.trim().length >= 2)) return setErr('Choose your bank');
    if (!f.nickname.trim()) return setErr('Enter an account display name');
    if (!/^[A-Z]{3}$/.test(f.currency)) return setErr('Enter a 3-letter currency code');
    if (!provider) return setErr('Choose a connection method');
    if (scheduleMode === 'custom' && !f.sync_schedule?.trim()) return setErr('Enter a cron schedule or use the organisation schedule');
    setErr(null);
    setBusy(true);
    try {
      let institutionId = inst?.id;
      if (!institutionId) {
        const created = await institutionsApi.create({ name: otherName.trim(), country_code: country });
        institutionId = created.id;
      }
      const acc = await accountsApi.create({
        ...f, institution_id: institutionId, country_code: country, connection_provider: provider,
        account_number: f.account_number?.trim() || undefined, holder_name: f.holder_name?.trim() || undefined,
        low_balance_threshold: f.low_balance_threshold || undefined,
        demo_scenario: inst?.is_demo ? f.demo_scenario : undefined,
        sync_schedule: scheduleMode === 'custom' ? f.sync_schedule?.trim() : undefined,
        timezone: scheduleMode === 'custom' ? f.timezone || undefined : undefined,
      });
      ['accounts', 'automation', 'analytics', 'institutions'].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      onCreated?.(acc);
      onClose();
    } catch (ex) {
      setErr(errorText(ex));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} size="lg" title="Add bank account"
      description="Configure the account once. Automatic methods are authorised with the bank or provider — you never type your banking password here."
      footer={<><Button variant="outline" onClick={onClose}>Cancel</Button>
        <Button type="submit" form="add-account" loading={busy} disabled={!provider}>Add account</Button></>}>
      <form id="add-account" onSubmit={submit} className="space-y-5" noValidate>
        {err && <Notice tone="danger">{err}</Notice>}
        <section className="grid grid-cols-1 sm:grid-cols-2 gap-4" aria-label="Bank">
          <Field label="Country" htmlFor="country" required>
            <Select id="country" value={country} onChange={(e) => { setCountry(e.target.value); setInstId(''); setProvider(null); }}>
              <option value="">Select…</option>
              {countries.data?.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
            </Select>
          </Field>
          <Field label="Bank name" htmlFor="inst" required>
            <Select id="inst" value={instId} disabled={!country} onChange={(e) => {
              const value = e.target.value;
              if (value.startsWith('browser:')) {
                const profile = browserProfiles.data?.find((p) => p.key === value.slice(8));
                setInstId(OTHER); setOtherName(profile?.name ?? '');
              } else { setInstId(value); }
              setProvider(null);
            }}>
              <option value="">{country ? 'Select…' : 'Choose a country first'}</option>
              {insts.data?.items.map((i) => <option key={i.id} value={i.id}>{i.is_demo ? '[DEMO] ' : ''}{i.name}</option>)}
              {browserProfiles.data?.filter((p) => !insts.data?.items.some((i) => i.name.toLowerCase() === p.name.toLowerCase()))
                .map((p) => <option key={`browser:${p.key}`} value={`browser:${p.key}`}>{p.name} · website profile ({p.permission})</option>)}
              {country && <option value={OTHER}>My bank is not listed…</option>}
            </Select>
          </Field>
          {instId === OTHER && (
            <Field label="Bank name (as it appears on statements)" htmlFor="other">
              <Input id="other" value={otherName} onChange={(e) => setOtherName(e.target.value)} placeholder="e.g. HDFC Bank" />
            </Field>
          )}
        </section>

        {(inst || bankName.length >= 2) && (
          <section aria-label="Connection method" className="space-y-2.5">
            <div className="flex items-center gap-2"><h3 className="text-sm font-semibold text-ink">Connection method</h3>
              <Badge>Determined by real integration availability</Badge></div>
            {methods.isLoading ? <TableSkeleton rows={2} /> : methods.error ? <Notice tone="danger">{errorText(methods.error)}</Notice> : (
              <div role="radiogroup" className="space-y-2.5">
                {methods.data?.map((m) => <MethodCard key={m.method + (m.provider_code ?? '')} m={m}
                  selected={provider === m.provider_code && !!m.provider_code} onSelect={() => setProvider(m.provider_code)} />)}
              </div>
            )}
            {chosen?.method === 'manual_statement_import' && (
              <Notice tone="warning">Manual import is a fallback: you will need to upload statements yourself and nothing is collected automatically.</Notice>
            )}
          </section>
        )}

        <section className="grid grid-cols-1 sm:grid-cols-2 gap-4" aria-label="Account details">
          <Field label="Account display name" htmlFor="nick" required>
            <Input id="nick" value={f.nickname} onChange={(e) => setF({ ...f, nickname: e.target.value })} placeholder="e.g. Household savings" />
          </Field>
          <Field label="Account type" htmlFor="type">
            <Select id="type" value={f.account_type} onChange={(e) => setF({ ...f, account_type: e.target.value })}>
              {TYPES.map((t) => <option key={t} value={t}>{t.replace('_', ' ').replace(/^./, (c) => c.toUpperCase())}</option>)}
            </Select>
          </Field>
          <Field label="Account number" htmlFor="num" hint="Encrypted at rest; only the last four digits are ever displayed. Used to match the account shared by the provider.">
            <Input id="num" value={f.account_number ?? ''} autoComplete="off" onChange={(e) => setF({ ...f, account_number: e.target.value })} />
          </Field>
          <Field label="Currency" htmlFor="ccy" required>
            <Input id="ccy" value={f.currency} maxLength={3} onChange={(e) => setF({ ...f, currency: e.target.value.toUpperCase() })} />
          </Field>
          <Field label="Account holder" htmlFor="holder">
            <Input id="holder" value={f.holder_name ?? ''} onChange={(e) => setF({ ...f, holder_name: e.target.value })} />
          </Field>
          <Field label="Low-balance alert threshold" htmlFor="thr" hint={`In ${f.currency || 'account currency'}`}>
            <Input id="thr" inputMode="decimal" value={f.low_balance_threshold ?? ''}
              onChange={(e) => setF({ ...f, low_balance_threshold: e.target.value.replace(/[^\d.]/g, '') })} />
          </Field>
        </section>

        {inst?.is_demo && (
          <Field label="Demo scenario" htmlFor="sc" hint="Simulate provider behaviour to explore failure handling">
            <Select id="sc" value={f.demo_scenario ?? 'ok'} onChange={(e) => setF({ ...f, demo_scenario: e.target.value })}>
              <option value="ok">Healthy connection</option><option value="otp_required">Requires one-time passcode</option>
              <option value="provider_down">Provider downtime</option><option value="consent_expired">Consent expired</option>
              <option value="auth_failed">Authentication failed</option>
            </Select>
          </Field>
        )}

        <section aria-label="Automation" className="rounded-xl border border-line p-3.5 space-y-3">
          <div className="flex items-center justify-between gap-3">
            <div><p className="text-sm font-medium text-ink">Automatic banking automation</p>
              <p className="text-[12px] text-muted">Starts only after the connection is authorised and verified.</p></div>
            <Toggle label="Enable automation" checked={!!f.automation_enabled} onChange={(v) => setF({ ...f, automation_enabled: v })} />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <Field label="Automation schedule" htmlFor="sched">
              <Select id="sched" value={scheduleMode} onChange={(e) => setScheduleMode(e.target.value as 'org' | 'custom')}>
                <option value="org">Organisation schedule</option><option value="custom">Custom for this account</option>
              </Select>
            </Field>
            {scheduleMode === 'custom' && <>
              <Field label="Cron" htmlFor="cron" hint="e.g. 0 7,19 * * * (07:00 and 19:00)">
                <Input id="cron" className="font-mono" value={f.sync_schedule ?? ''} onChange={(e) => setF({ ...f, sync_schedule: e.target.value })} />
              </Field>
              <Field label="Time zone" htmlFor="tz">
                <Input id="tz" value={f.timezone ?? ''} placeholder={country === 'IN' ? 'Asia/Kolkata' : 'Europe/London'}
                  onChange={(e) => setF({ ...f, timezone: e.target.value })} />
              </Field>
            </>}
          </div>
        </section>
      </form>
    </Modal>
  );
}
