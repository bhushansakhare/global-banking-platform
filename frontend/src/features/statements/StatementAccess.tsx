import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { LockKeyhole, CheckCircle2, Clock3 } from 'lucide-react';
import { Badge, Button, Card, CardHeader, Field, Input } from '@/components/ui/primitives';
import { Notice } from '@/components/ui/layout';
import { ApiError, request } from '@/services/http';
import { errorText, useAction } from '@/hooks/useMutationToast';
import type { Statement } from '@/types/api';

export function StatementAccess({ statement: s, editable }: { statement: Statement; editable: boolean }) {
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState('');
  const access = s.document_access;
  const unlock = useAction(async () => {
    const result = await request<{ statements_queued: number }>(`/statements/${s.id}/unlock`, {
      method: 'POST', body: { password },
    });
    const confirmed = await request<Statement>(`/statements/${s.id}`);
    if (!confirmed.document_access?.password_saved || !confirmed.document_access.preview_ready) {
      throw new ApiError(409, 'pdf_access_not_confirmed', 'Saved PDF access could not be confirmed. The statement is still locked; check the password and try again.');
    }
    return result;
  }, { success: 'Statement password verified and saved for this account',
    invalidate: [['statement', s.id], ['statements'], ['recon'], ['accounts'], ['account', s.account_id]],
    onSuccess: result => { setPassword(''); setMessage(`Saved for this account. ${result.statements_queued} other waiting statement(s) queued automatically.`); },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); unlock.mutate(undefined); };
  const forget = useAction(() => request(`/accounts/${s.account_id}/statement-password`, { method: 'DELETE' }), {
    success: 'Saved PDF passwords removed for this account', invalidate: [['statement'], ['statements']],
  });
  if (s.file?.format !== 'pdf') return null;
  const required = access?.password_required ?? s.password_required;
  const form = <form onSubmit={submit} className="mt-4 space-y-3">
    <Field label="Statement PDF password" htmlFor={`pdf-password-${s.id}`} hint="Use the PDF password provided by your bank, not your banking login password.">
      <Input id={`pdf-password-${s.id}`} type="password" autoComplete="new-password" required value={password}
        disabled={unlock.isPending} onChange={e => setPassword(e.target.value)} />
    </Field>
    {unlock.error && <Notice tone="warning">{errorText(unlock.error)}</Notice>}
    <Button type="submit" loading={unlock.isPending} disabled={!password}>Unlock & save for this account</Button>
  </form>;
  return <Card className="mb-4">
    <CardHeader title={required ? 'Unlock this statement once' : 'PDF access'} icon={<LockKeyhole className="h-4 w-4" />}
      subtitle="A verified password is reused only for statements belonging to this account." />
    {access?.error && <Notice tone="warning">{access.error}</Notice>}
    <div className="flex flex-wrap gap-2">
      <Badge tone={access?.preview_ready ? 'success' : 'warning'}>{required ? 'PDF password needed' : access?.preview_ready ? 'Preview ready' : 'PDF needs attention'}</Badge>
      {access?.password_saved && <Badge>Password saved securely</Badge>}
    </div>
    <p className="mt-3 text-sm text-muted">{required ? 'Unlock here to view the PDF and extract transactions automatically. ' : ''}Future uploads for this account reuse its saved password. Other accounts keep their own passwords. The original file stays unchanged.</p>
    {message && <div className="mt-3"><Notice>{message}</Notice></div>}
    {editable && (required ? form : access?.encrypted ? <details className="mt-3"><summary className="cursor-pointer text-sm">Change saved PDF password</summary>{form}</details> : null)}
    {editable && access?.password_saved && <details className="mt-4 text-sm"><summary className="cursor-pointer text-muted">Saved password controls</summary>
      <p className="my-3 text-xs text-muted">Forgetting removes saved PDF access for all statements of this account. Original files and extracted data are kept.</p>
      <Button type="button" variant="outline" loading={forget.isPending} onClick={() => forget.mutate(undefined)}>Forget this account’s PDF passwords</Button>
    </details>}
    {!editable && required && <p className="mt-3 text-sm text-muted">An account editor with statement access can save the password here.</p>}
  </Card>;
}

export function StatementPipeline({ statement: s }: { statement: Statement }) {
  const extracted = !!s.transaction_count;
  const valid = ['processed', 'processed_with_warnings'].includes(s.processing_status) && s.arithmetic_valid === true;
  const session = s.latest_reconciliation;
  const steps = [
    { label: 'File received', done: !!s.file, detail: 'Original safely stored' },
    { label: 'PDF access', done: s.file?.format !== 'pdf' || !!s.document_access?.preview_ready,
      detail: s.file?.format === 'pdf' ? s.document_access?.password_required ? 'Enter password below' : s.document_access?.preview_ready ? 'Preview available' : 'File needs attention' : 'No PDF password needed' },
    { label: 'Transactions extracted', done: extracted, detail: extracted ? `${s.transaction_count} rows recognised` : 'Waiting for readable data' },
    { label: 'Figures verified', done: valid, detail: valid ? 'Arithmetic checked' : s.arithmetic_valid === false ? 'Balance mismatch: review required' : 'Review or extraction needed' },
    { label: 'Reconciliation', done: !!session && session.status !== 'running', detail: session ? session.status === 'running' ? 'Checks in progress' : session.exception_count ? `${session.exception_count} exception(s)` : session.has_book_side ? 'Bank and cash-book checked' : 'Statement integrity checked' : s.duplicate_of_id ? 'See the original period statement' : s.auto_reconcile === false ? 'Automatic reconciliation is off' : valid ? 'Queued automatically' : 'Starts after validation' },
  ];
  return <Card className="mb-4">
    <CardHeader title="Statement processing" subtitle="Upload → unlock → extract → verify → reconcile. Bank layouts are detected from the file; unclear figures pause for review." />
    <ol className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
      {steps.map(step => <li key={step.label} className="rounded-xl border border-line p-3">
        <div className="flex items-center gap-2 text-sm font-medium">{step.done ? <CheckCircle2 className="h-4 w-4 shrink-0 text-olive-600" /> : <Clock3 className="h-4 w-4 shrink-0 text-muted" />}{step.label}</div>
        <p className="mt-2 text-xs text-muted">{step.detail}</p>
      </li>)}
    </ol>
    {session && <div className="mt-4 flex flex-wrap items-center gap-3"><Link className="link text-sm" to={`/reconciliation/${session.id}`}>View reconciliation result</Link>
      {!session.has_book_side && <span className="text-xs text-muted">Import this account’s cash-book to match bank transactions against your books.</span>}</div>}
  </Card>;
}
