import clsx from 'clsx';
import { money } from '@/utils/format';

/** Monetary amount with tabular numerals; credits in olive, debits in ink (never red — red is for errors). */
export function Money({ value, currency, signed, className, compact }: { value: string | number | null | undefined;
  currency?: string; signed?: boolean; className?: string; compact?: boolean }) {
  const n = value === null || value === undefined || value === '' ? null : Number(value);
  return (
    <span className={clsx('num whitespace-nowrap', signed && n !== null && n > 0 && 'text-olive-700 dark:text-olive-400',
      className)}>
      {money(value, currency, { sign: signed, compact })}
    </span>
  );
}

export function DebitCredit({ debit, credit, currency }: { debit: string; credit: string; currency: string }) {
  const d = Number(debit), c = Number(credit);
  if (c > 0) return <Money value={c} currency={currency} signed />;
  if (d > 0) return <Money value={-d} currency={currency} signed />;
  return <span className="text-muted">—</span>;
}
