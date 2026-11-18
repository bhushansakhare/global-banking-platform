import { AlertOctagon, Inbox, RefreshCw } from 'lucide-react';
import type { ReactNode } from 'react';
import { ApiError } from '@/services/http';
import { Button, Skeleton } from './primitives';

export function EmptyState({ icon, title, body, action }: { icon?: ReactNode; title: string; body?: ReactNode;
  action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-12 px-6">
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-50 text-brand-600 dark:bg-brand-900/40 dark:text-brand-300">
        {icon ?? <Inbox className="h-6 w-6" />}
      </div>
      <h3 className="text-[15px] font-semibold text-ink">{title}</h3>
      {body && <p className="mt-1.5 max-w-md text-sm text-muted leading-relaxed">{body}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof ApiError ? error.message : 'Something went wrong while loading this data.';
  const code = error instanceof ApiError ? error.code : undefined;
  return (
    <div role="alert" className="flex flex-col items-center text-center py-10 px-6">
      <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-2xl bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-300">
        <AlertOctagon className="h-5 w-5" />
      </div>
      <h3 className="text-sm font-semibold text-ink">Unable to load</h3>
      <p className="mt-1 max-w-md text-sm text-muted">{message}</p>
      {code && <p className="mt-1 font-mono text-[11px] text-muted/80">{code}</p>}
      {onRetry && <Button variant="outline" size="sm" className="mt-4" icon={<RefreshCw className="h-3.5 w-3.5" />}
        onClick={onRetry}>Try again</Button>}
    </div>
  );
}

export function TableSkeleton({ rows = 6, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div className="p-4 space-y-3" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex gap-4">
          {Array.from({ length: cols }).map((__, j) => <Skeleton key={j} className="h-4 flex-1" />)}
        </div>
      ))}
    </div>
  );
}

export function CardsSkeleton({ n = 4 }: { n?: number }) {
  return (
    <div className="grid grid-cols-1 xs:grid-cols-2 xl:grid-cols-4 gap-4">
      {Array.from({ length: n }).map((_, i) => (
        <div key={i} className="card p-5 space-y-3"><Skeleton className="h-3 w-24" /><Skeleton className="h-7 w-36" />
          <Skeleton className="h-3 w-20" /></div>
      ))}
    </div>
  );
}
