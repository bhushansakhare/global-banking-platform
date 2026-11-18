import { useMutation, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { ApiError } from '@/services/http';
import { toast } from '@/stores/ui';

/** Mutation with consistent success/error toasts and cache invalidation. */
export function useAction<TArgs, TResult>(fn: (args: TArgs) => Promise<TResult>, opts: {
  success?: string | ((r: TResult) => string);
  invalidate?: QueryKey[];
  onSuccess?: (r: TResult) => void;
} = {}) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (r) => {
      opts.invalidate?.forEach((k) => qc.invalidateQueries({ queryKey: k }));
      if (opts.success) toast({ kind: 'success', title: typeof opts.success === 'function' ? opts.success(r) : opts.success });
      opts.onSuccess?.(r);
    },
    onError: (e) => toast({ kind: 'error', title: 'Action failed', body: e instanceof ApiError ? e.message : String(e) }),
  });
}

export function errorText(e: unknown): string | null {
  if (!e) return null;
  if (e instanceof ApiError) {
    if (Array.isArray(e.details) && e.details.length) {
      const d = e.details[0] as { loc?: string[]; msg?: string };
      if (d?.msg) return `${(d.loc ?? []).slice(-1)[0] ?? 'Field'}: ${d.msg}`;
    }
    return e.message;
  }
  return 'Unexpected error';
}
