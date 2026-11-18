import { useQuery } from '@tanstack/react-query';
import clsx from 'clsx';
import { FileUp, UploadCloud } from 'lucide-react';
import { useRef, useState, type DragEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Field, Select, StatusBadge } from '@/components/ui/primitives';
import { Modal } from '@/components/ui/Modal';
import { Notice } from '@/components/ui/layout';
import { errorText, useAction } from '@/hooks/useMutationToast';
import { accountsApi, statementsApi } from '@/services/api';
import { bytes, money } from '@/utils/format';

const ACCEPT = '.pdf,.csv,.xlsx,.xls,application/pdf,text/csv,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

export function UploadStatementModal({ open, onClose, accountId }: { open: boolean; onClose: () => void; accountId?: string }) {
  const [acc, setAcc] = useState(accountId ?? '');
  const [file, setFile] = useState<File | null>(null);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: () => accountsApi.list(), enabled: open && !accountId });
  const up = useAction(({ a, f }: { a: string; f: File }) => statementsApi.upload(a, f), {
    invalidate: [['statements'], ['transactions'], ['analytics'], ['balances'], ['accounts']],
    success: (r) => (r.duplicate ? 'Already imported — duplicate detected' : 'Statement processed'),
  });
  const onDrop = (e: DragEvent) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); };
  const close = () => { setFile(null); up.reset(); onClose(); };
  const res = up.data;

  return (
    <Modal open={open} onClose={close} title="Upload statement" size="md"
      description="Official exports in PDF, Excel (XLSX/XLS) or CSV. The original file is stored unchanged for audit."
      footer={res ? <><Button variant="outline" onClick={close}>Close</Button>
        <Button onClick={() => { close(); navigate(`/statements/${res.statement.id}`); }}>View statement</Button></>
        : <><Button variant="outline" onClick={close}>Cancel</Button>
          <Button loading={up.isPending} disabled={!file || !(accountId || acc)} icon={<FileUp className="h-4 w-4" />}
            onClick={() => file && up.mutate({ a: accountId || acc, f: file })}>Upload and process</Button></>}>
      {res ? (
        <div className="space-y-3">
          {res.duplicate && <Notice tone="warning">{res.message}</Notice>}
          <div className="rounded-xl border border-line p-4 space-y-2 text-sm">
            <div className="flex items-center justify-between"><span className="text-muted">Status</span><StatusBadge status={res.statement.processing_status} /></div>
            <div className="flex items-center justify-between"><span className="text-muted">Transactions</span><span className="num">{res.statement.transaction_count ?? '—'}</span></div>
            <div className="flex items-center justify-between"><span className="text-muted">Opening → closing</span>
              <span className="num">{money(res.statement.opening_balance, res.statement.currency)} → {money(res.statement.closing_balance, res.statement.currency)}</span></div>
            <div className="flex items-center justify-between"><span className="text-muted">Arithmetic check</span>
              <span>{res.statement.arithmetic_valid === null ? 'Not possible' : res.statement.arithmetic_valid ? 'Balances' : 'Does not balance'}</span></div>
          </div>
          {res.statement.warnings.slice(0, 4).map((w, i) => <p key={i} className="text-[12px] text-muted">• {w.message}</p>)}
        </div>
      ) : (
        <div className="space-y-4">
          {up.error && <Notice tone="danger">{errorText(up.error)}</Notice>}
          {!accountId && (
            <Field label="Account" htmlFor="up-acc" required>
              <Select id="up-acc" value={acc} onChange={(e) => setAcc(e.target.value)}>
                <option value="">Select account…</option>
                {accounts.data?.map((a) => <option key={a.id} value={a.id}>{a.nickname} · {a.institution.name} ({a.currency})</option>)}
              </Select>
            </Field>
          )}
          <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={onDrop}
            onClick={() => input.current?.click()} role="button" tabIndex={0}
            onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && input.current?.click()}
            className={clsx('flex flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-10 text-center cursor-pointer transition',
              drag ? 'border-brand-400 bg-brand-50/60 dark:bg-brand-900/20' : 'border-line hover:border-brand-300 hover:bg-surface-2')}>
            <UploadCloud className="h-8 w-8 text-brand-500 mb-3" />
            {file ? <><p className="text-sm font-medium text-ink break-all">{file.name}</p><p className="text-[12px] text-muted">{bytes(file.size)}</p></>
              : <><p className="text-sm font-medium text-ink">Drop a file here or click to browse</p><p className="text-[12px] text-muted mt-1">PDF, XLSX, XLS or CSV · up to 25 MB</p></>}
            <input ref={input} type="file" accept={ACCEPT} className="sr-only" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </div>
          <p className="text-[12px] text-muted">Scanned PDFs are routed to manual review — the platform never guesses at unreadable figures.</p>
        </div>
      )}
    </Modal>
  );
}
