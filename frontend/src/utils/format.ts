const LOCALE = 'en-GB';

export function money(value: string | number | null | undefined, currency?: string, opts: { compact?: boolean;
  sign?: boolean } = {}): string {
  if (value === null || value === undefined || value === '') return '—';
  const n = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(n)) return '—';
  try {
    return new Intl.NumberFormat(LOCALE, {
      style: currency ? 'currency' : 'decimal',
      currency: currency || undefined,
      notation: opts.compact ? 'compact' : 'standard',
      minimumFractionDigits: opts.compact ? 0 : 2,
      maximumFractionDigits: opts.compact ? 1 : 2,
      signDisplay: opts.sign ? 'exceptZero' : 'auto',
    }).format(n);
  } catch {
    return `${n.toFixed(2)} ${currency ?? ''}`.trim();
  }
}

export function num(n: number | null | undefined): string {
  return n === null || n === undefined ? '—' : new Intl.NumberFormat(LOCALE).format(n);
}

export function pct(v: number | string | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return '—';
  return `${(Number(v) * 100).toFixed(digits)}%`;
}

export function date(v: string | null | undefined, opts: Intl.DateTimeFormatOptions = { day: '2-digit', month: 'short',
  year: 'numeric' }): string {
  if (!v) return '—';
  const d = new Date(v.length === 10 ? `${v}T00:00:00` : v);
  return Number.isNaN(d.getTime()) ? '—' : new Intl.DateTimeFormat(LOCALE, opts).format(d);
}

export function dateTime(v: string | null | undefined): string {
  return date(v, { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function relative(v: string | null | undefined): string {
  if (!v) return 'never';
  const diff = (new Date(v).getTime() - Date.now()) / 1000;
  const abs = Math.abs(diff);
  const rtf = new Intl.RelativeTimeFormat(LOCALE, { numeric: 'auto' });
  if (abs < 60) return rtf.format(Math.round(diff), 'second');
  if (abs < 3600) return rtf.format(Math.round(diff / 60), 'minute');
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), 'hour');
  if (abs < 86400 * 30) return rtf.format(Math.round(diff / 86400), 'day');
  return date(v);
}

export function bytes(n: number | null | undefined): string {
  if (!n) return '—';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
}

export function titleCase(s: string | null | undefined): string {
  if (!s) return '';
  const t = s.replace(/_/g, ' ');
  return t.charAt(0).toUpperCase() + t.slice(1);
}

export function isoDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return isoDate(d);
}
