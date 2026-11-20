import { useQuery } from '@tanstack/react-query';
import clsx from 'clsx';
import { FileUp, UploadCloud } from 'lucide-react';
import { useRef, useState, type DragEvent } from 'react';
import { Link } from 'react-router-dom';
import { Button, Field, Select, StatusBadge } from '@/components/ui/primitives';
import { Modal } from '@/components/ui/Modal';
import { Notice } from '@/components/ui/layout';
import { errorText, useAction } from '@/hooks/useMutationToast';
import { accountsApi, statementsApi } from '@/services/api';
import { bytes } from '@/utils/format';
import type { Statement } from '@/types/api';

const ACCEPT = '.pdf,.csv,.xlsx,.xls,application/pdf,text/csv,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
interface UploadResult { filename: string; statement?: Statement; duplicate?: boolean; error?: string }

export function UploadStatementModal({ open, onClose, accountId }: { open: boolean; onClose: () => void; accountId?: string }) {
  const [acc, setAcc] = useState(accountId ?? '');
  const [files, setFiles] = useState<File[]>([]);
  const [drag, setDrag] = useState(false);
  const [completed, setCompleted] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: () => accountsApi.list(), enabled: open && !accountId });
  const up = useAction(async ({ a, batch }: { a: string; batch: File[] }) => {
    const results: UploadResult[] = [];
    setCompleted(0);
    for (const file of batch) {
      try {
        const result = await statementsApi.upload(a, file);
        results.push({ filename: file.name, statement: result.statement, duplicate: result.duplicate });
      } catch (error) { results.push({ filename: file.name, error: errorText(error) ?? 'Upload failed' }); }
      setCompleted(results.length);
    }
    return results;
  }, { invalidate: [['statements'], ['transactions'], ['analytics'], ['balances'], ['accounts'], ['recon']],
    success: results => `${results.filter(r => r.statement).length} of ${results.length} file(s) received. Review each result below.` });
  const onDrop = (e: DragEvent) => { e.preventDefault(); setDrag(false); if (!up.isPending) setFiles(Array.from(e.dataTransfer.files)); };
  const close = () => { if (up.isPending) return; setFiles([]); up.reset(); onClose(); };
  const locked = up.data?.find(r => r.statement?.password_required)?.statement;
  return <Modal open={open} onClose={close} title="Upload statements" size="lg"
    description="Select one or more statements for the same bank account. Saved account passwords are reused automatically."
    footer={up.data ? <Button variant="outline" onClick={close}>Done</Button> : <>
      <Button variant="outline" disabled={up.isPending} onClick={close}>Cancel</Button>
      <Button loading={up.isPending} disabled={!files.length || !(accountId || acc)} icon={<FileUp className="h-4 w-4" />}
        onClick={() => up.mutate({ a: accountId || acc, batch: files })}>Upload & process {files.length || ''} file(s)</Button></>}>
    {up.data ? <div className="space-y-4">
      {locked && <Notice tone="warning" title="One password for this account"><Link to={`/statements/${locked.id}`} onClick={close} className="link">Unlock the first protected PDF</Link>. The software will then continue other password-blocked statements from this account automatically.</Notice>}
      <ul className="space-y-3">{up.data.map((result, index) => <li key={index} className="rounded-xl border border-line p-4 text-sm">
        <p className="mb-2 break-all font-medium">{result.filename}</p>
        {result.statement ? <div className="flex flex-wrap items-center gap-3">
          <StatusBadge status={result.statement.processing_status} />
          <span className="text-muted">{result.duplicate ? 'Already imported' : result.statement.password_required ? 'PDF password needed' : `${result.statement.transaction_count ?? 0} transaction(s)`}</span>
          <Link className="link" to={`/statements/${result.statement.id}`} onClick={close}>Open statement</Link>
        </div> : <p className="text-red-700 dark:text-red-300">{result.error}</p>}
      </li>)}</ul>
    </div> : <div className="space-y-4">
      {!accountId && <Field label="Bank account" htmlFor="up-acc" required><Select id="up-acc" value={acc} disabled={up.isPending} onChange={e => setAcc(e.target.value)}>
        <option value="">Select account…</option>{accounts.data?.map(a => <option key={a.id} value={a.id}>{a.nickname} · {a.institution.name} · {a.account_number_masked}</option>)}
      </Select></Field>}
      <div onDragOver={e => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={onDrop}
        onClick={() => !up.isPending && input.current?.click()} role="button" tabIndex={0}
        onKeyDown={e => (e.key === 'Enter' || e.key === ' ') && !up.isPending && input.current?.click()}
        className={clsx('flex flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-8 text-center cursor-pointer transition', drag ? 'border-brand-400 bg-brand-50/60 dark:bg-brand-900/20' : 'border-line hover:border-brand-300 hover:bg-surface-2')}>
        <UploadCloud className="h-8 w-8 text-brand-500 mb-3" />
        <p className="text-sm font-medium">{files.length ? `${files.length} file(s) selected` : 'Drop statements here or click to browse'}</p>
        <p className="mt-1 text-xs text-muted">PDF, Excel or CSV · up to 25 MB per file</p>
        <input ref={input} aria-label="Statement files" type="file" accept={ACCEPT} multiple disabled={up.isPending} className="sr-only" onChange={e => setFiles(Array.from(e.target.files ?? []))} />
      </div>
      {files.length > 0 && <ul className="max-h-40 space-y-1 overflow-auto text-xs text-muted">{files.map((file, index) => <li key={index} className="break-all">{file.name} · {bytes(file.size)}</li>)}</ul>}
      {up.isPending && <p role="status" className="text-sm">Processing {completed} of {files.length} files. Keep this window open.</p>}
      <p className="text-xs text-muted">Files are processed one by one. Originals stay unchanged. Passwords belong to the selected account; uncertain extraction pauses for review.</p>
    </div>}
  </Modal>;
}
