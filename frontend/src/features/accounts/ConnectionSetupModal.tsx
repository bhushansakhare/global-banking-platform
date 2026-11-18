import { useQuery } from '@tanstack/react-query';
import clsx from 'clsx';
import { CheckCircle2, ExternalLink, Info, Lock, ShieldCheck, Smartphone } from 'lucide-react';
import { useEffect, useState, type FormEvent } from 'react';
import { Badge, Button, Field, Input, StatusBadge } from '@/components/ui/primitives';
import { Modal } from '@/components/ui/Modal';
import { Notice } from '@/components/ui/layout';
import { ErrorState, TableSkeleton } from '@/components/ui/States';
import { errorText, useAction } from '@/hooks/useMutationToast';
import { accountsApi } from '@/services/api';
import type { Account, CapabilityMatrix, ConnectionMethodOption } from '@/types/api';
import { BrowserBankConnection } from './BrowserBankConnection';

const CAP_LABELS: [keyof CapabilityMatrix, string][] = [
  ['automatic_sync', 'Automatic sync'], ['transactions', 'Transactions'], ['current_balance', 'Current balance'],
  ['available_balance', 'Available balance'], ['ledger_balance', 'Ledger balance'], ['statements', 'Statements'],
  ['account_information', 'Account details'], ['historical_data', 'History'],
];

export function Capabilities({ caps }: { caps: Partial<CapabilityMatrix> }) {
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Capabilities">
      {CAP_LABELS.map(([k, label]) => (
        <li key={k}><Badge tone={caps[k] ? 'success' : 'neutral'}>{caps[k] ? '✓' : '✕'} {label}</Badge></li>
      ))}
    </ul>
  );
}

export function MethodCard({ m, selected, current, onSelect }: { m: ConnectionMethodOption; selected: boolean;
  current?: boolean; onSelect?: () => void }) {
  const selectable = !!onSelect && (m.available || m.status === 'provider_setup_required') && !!m.provider_code;
  return (
    <div role={selectable ? 'radio' : undefined} aria-checked={selectable ? selected : undefined} tabIndex={selectable ? 0 : undefined}
      onClick={selectable ? onSelect : undefined}
      onKeyDown={(e) => { if (selectable && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onSelect?.(); } }}
      className={clsx('rounded-xl border p-3.5 text-[13px] space-y-2 transition-colors',
        selected ? 'border-brand-500 ring-1 ring-brand-500 bg-brand-50/40 dark:bg-brand-900/10' : 'border-line',
        selectable ? 'cursor-pointer hover:bg-surface-2' : 'opacity-90')}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-ink">{m.label}</span>
        {m.provider_name && <span className="text-muted">· {m.provider_name}</span>}
        <StatusBadge status={m.status} />
        {m.recommended && <Badge tone="brand">Recommended</Badge>}
        {current && <Badge tone="info">Current</Badge>}
        {m.environment === 'sandbox' && <Badge tone="warning">Sandbox</Badge>}
        {m.integration_status === 'implemented_unverified' && <Badge tone="warning">Unverified integration</Badge>}
        {!m.automatic && m.available && <Badge>Manual</Badge>}
      </div>
      <p className="text-muted leading-relaxed">{m.reason}</p>
      {(m.available || m.status === 'provider_setup_required') && m.provider_code && <Capabilities caps={m.capabilities} />}
      {m.setup_steps.length > 0 && (
        <ol className="list-decimal pl-5 space-y-0.5 text-muted">{m.setup_steps.map((s) => <li key={s}>{s}</li>)}</ol>
      )}
      {m.reference && <a href={m.reference} target="_blank" rel="noreferrer noopener" className="link inline-flex items-center gap-1"
        onClick={(e) => e.stopPropagation()}>Reference <ExternalLink className="h-3 w-3" /></a>}
    </div>
  );
}

/** Connect an EXISTING account: choose a genuinely available method, then authorise it with the provider. */
export function ConnectionSetupModal({ account, open, onClose }: { account: Account; open: boolean; onClose: () => void }) {
  const q = useQuery({ queryKey: ['connection-methods', account.id], queryFn: () => accountsApi.connectionMethods(account.id),
    enabled: open });
  const [choice, setChoice] = useState<string | null>(null);
  const [mobile, setMobile] = useState('');
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [verified, setVerified] = useState<Account | null>(null);
  useEffect(() => { if (!open) { setChoice(null); setMobile(''); setAuthUrl(null); setVerified(null); } }, [open]);
  useEffect(() => { if (q.data && choice === null) setChoice(q.data.current_provider); }, [q.data, choice]);
  useEffect(() => { if (q.data?.consent?.authorisation_url && !authUrl) setAuthUrl(q.data.consent.authorisation_url); },
    [q.data, authUrl]);

  const inv = [['account', account.id], ['accounts'], ['automation'], ['connection-methods', account.id]];
  const setMethod = useAction((code: string) => accountsApi.setConnectionMethod(account.id, code),
    { invalidate: inv, success: 'Connection method updated' });
  const authorize = useAction(() => accountsApi.authorize(account.id, { mobile }), {
    invalidate: inv, onSuccess: (r) => {
      if (r.authorisation_url) { setAuthUrl(r.authorisation_url); window.open(r.authorisation_url, '_blank', 'noopener,noreferrer'); }
    } });
  const verify = useAction(() => accountsApi.verifyAuthorization(account.id), {
    invalidate: inv, onSuccess: (a) => setVerified(a),
    success: (a) => a.connection?.status === 'active' ? 'Bank authorised — first sync queued' : 'Authorisation not completed yet' });

  const methods = q.data?.methods ?? [];
  const current = q.data?.current_provider;
  const selected = methods.find((m) => m.provider_code === choice);
  const isCurrent = !!selected && selected.provider_code === current;
  const conn = (verified ?? account).connection;
  const isAA = selected?.provider_code === 'india_account_aggregator';
  const active = isCurrent && conn?.status === 'active';

  const submitMobile = (e: FormEvent) => { e.preventDefault(); authorize.mutate(undefined); };

  return (
    <Modal open={open} onClose={onClose} size="lg" title="Connect bank"
      description={`${account.institution.name} · ${account.account_number_masked} · ${account.currency}. Only methods this bank genuinely supports can be selected.`}
      footer={<Button variant="outline" onClick={onClose}>Close</Button>}>
      {q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data ? <TableSkeleton rows={3} /> : (
        <div className="space-y-5">
          <BrowserBankConnection account={account} />
          <section aria-label="Connection methods" role="radiogroup" className="space-y-2.5">
            {methods.map((m) => (
              <MethodCard key={m.method + (m.provider_code ?? '')} m={m} selected={choice === m.provider_code && !!m.provider_code}
                current={m.provider_code === current} onSelect={() => setChoice(m.provider_code)} />
            ))}
          </section>

          {selected && !isCurrent && (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-surface-2 p-3.5">
              <p className="text-[13px] text-muted">Switch this account to <b className="text-ink">{selected.label}</b>. The account, its history
                and statement uploads are kept.</p>
              <Button loading={setMethod.isPending} onClick={() => selected.provider_code && setMethod.mutate(selected.provider_code)}>
                Use this method</Button>
            </div>
          )}

          {isCurrent && selected?.status === 'provider_setup_required' && (
            <Notice tone="warning" title="Awaiting provider setup">
              The method is selected for this account, but the platform has no provider credentials yet. Once an administrator completes the
              steps above, return here to authorise. Nothing will be shown as connected until then.
            </Notice>
          )}

          {isCurrent && isAA && selected?.available && !active && (
            <section className="rounded-xl border border-line p-4 space-y-4" aria-label="Account Aggregator authorisation">
              <div className="flex items-start gap-3">
                <ShieldCheck className="h-5 w-5 text-brand-600 shrink-0 mt-0.5" />
                <div className="text-[13px] text-muted space-y-1">
                  <p className="text-ink font-medium">Authorise through the RBI Account Aggregator</p>
                  <p>You approve read-only access inside the Account Aggregator. Your bank password, OTPs and MPIN are never entered here.</p>
                </div>
              </div>
              <form onSubmit={submitMobile} className="flex flex-col sm:flex-row gap-3 sm:items-end">
                <Field label="Mobile number registered with your bank" htmlFor="aa-mobile" hint="Used as your Account Aggregator handle. Stored encrypted.">
                  <div className="relative">
                    <Smartphone className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted" />
                    <Input id="aa-mobile" className="pl-9" inputMode="tel" autoComplete="tel-national" placeholder="98xxxxxxxx"
                      value={mobile} onChange={(e) => setMobile(e.target.value.replace(/[^\d+\s-]/g, ''))} />
                  </div>
                </Field>
                <Button type="submit" loading={authorize.isPending} disabled={mobile.replace(/\D/g, '').length < 10}
                  icon={<Lock className="h-4 w-4" />}>Start consent</Button>
              </form>
              {authorize.error && <Notice tone="danger">{errorText(authorize.error)}</Notice>}
              {authUrl && (
                <div className="space-y-3">
                  <ol className="list-decimal pl-5 text-[13px] text-muted space-y-0.5">
                    <li>Open the Account Aggregator page and verify with the OTP the AA sends you.</li>
                    <li>Select your {account.institution.name} account ending {account.account_number_masked.slice(-4)} and approve.</li>
                    <li>Come back and click “I have approved”.</li>
                  </ol>
                  <div className="flex flex-wrap gap-2">
                    <a className="inline-flex h-10 items-center gap-2 rounded-lg border border-line px-4 text-sm font-medium text-ink hover:bg-surface-2"
                      href={authUrl} target="_blank" rel="noreferrer noopener"><ExternalLink className="h-4 w-4" />Open consent page</a>
                    <Button loading={verify.isPending} onClick={() => verify.mutate(undefined)} icon={<CheckCircle2 className="h-4 w-4" />}>I have approved</Button>
                  </div>
                </div>
              )}
              {verified && verified.connection?.status !== 'active' && (
                <Notice tone="warning">{verified.connection?.last_error ?? 'The provider has not confirmed the consent yet.'}</Notice>
              )}
            </section>
          )}

          {isCurrent && !isAA && selected?.available && selected.automatic && !active && conn && (
            <Notice tone="info" title="Provider consent">
              Complete the {selected.provider_name} consent from the account page, then use “Check connection”.
            </Notice>
          )}

          {active && (
            <Notice tone="info" title="Connected">
              {selected?.environment === 'sandbox' ? 'Sandbox connection active (test data, not your real account). ' : 'Connection is authorised. '}
              Turn on automation on the account to sync on schedule; the first synchronisation has been queued.
            </Notice>
          )}

          <p className="flex gap-2 text-[12px] text-muted"><Info className="h-4 w-4 shrink-0" />
            This platform is read-only: it never makes payments or transfers, and never bypasses OTP, CAPTCHA or other bank security checks.</p>
        </div>
      )}
    </Modal>
  );
}
