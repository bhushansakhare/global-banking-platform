import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Button, Card, CardHeader } from '@/components/ui/primitives';
import { Notice } from '@/components/ui/layout';
import { Modal } from '@/components/ui/Modal';
import { Money } from '@/components/Money';
import { request, fetchBlobUrl } from '@/services/http';
import { useAction } from '@/hooks/useMutationToast';
import { useAuth } from '@/stores/auth';
import { dateTime } from '@/utils/format';

interface Observation {
  available: boolean; amount?: string; currency?: string; captured_at?: string;
  target: string | null; below_target?: boolean | null; screenshot_available: boolean;
}

export function BankObservation({ accountId }: { accountId: string }) {
  const q = useQuery({ queryKey: ['bank-observation', accountId],
    queryFn: () => request<Observation>(`/accounts/${accountId}/bank-observation`), refetchInterval: 5000 });
  const [image, setImage] = useState<string | null>(null);
  const can = useAuth((s) => s.can);
  const view = useAction(async () => {
    const result = await fetchBlobUrl(`/accounts/${accountId}/bank-observation/screenshot`);
    setImage(result.url);
  });
  useEffect(() => () => { if (image) URL.revokeObjectURL(image); }, [image]);
  const observation = q.data;
  return <Card className="mb-4">
    <CardHeader title="Latest bank check" subtitle="Balance and statement screen saved after verifying this account on the bank website." />
    {q.isError ? <Notice tone="warning">Bank check could not load. <Button size="sm" variant="outline" onClick={() => q.refetch()}>Retry</Button></Notice>
      : !observation?.available ? <p className="text-sm text-muted">No bank screen saved yet. Run a bank check and approve its QR login.</p>
      : <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-6">
          <div><p className="text-sm text-muted">Available balance</p><Money value={observation.amount!} currency={observation.currency!} className="text-xl font-semibold" /></div>
          <div><p className="text-sm text-muted">Your target</p>{observation.target !== null
            ? <Money value={observation.target} currency={observation.currency!} className="text-lg font-medium" /> : <span>Not set</span>}</div>
        </div>
        {observation.below_target === true && <Notice tone="warning" title="Balance below target">This account's observed balance is below the target you set.</Notice>}
        {observation.below_target === false && <Notice title="Target met">This account's observed balance meets your target.</Notice>}
        {observation.target === null && <Notice>Set a minimum balance target below to enable low-balance alerts.</Notice>}
        <p className="text-sm text-muted">Checked {dateTime(observation.captured_at)} · Observed on bank page</p>
        {observation.screenshot_available && can('statements:read') && <Button variant="outline" loading={view.isPending} onClick={() => view.mutate(undefined)}>View bank screenshot</Button>}
      </div>}
    <Modal open={!!image} onClose={() => setImage(null)} title="Saved bank statement screen" size="xl">
      {image && <img src={image} alt="Saved bank statement screen for this account" className="max-h-[75vh] w-full object-contain" />}
    </Modal>
  </Card>;
}
