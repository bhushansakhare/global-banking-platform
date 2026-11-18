import clsx from 'clsx';
import { AnimatePresence, motion } from 'framer-motion';
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react';
import type { ReactNode } from 'react';
import { useUi } from '@/stores/ui';

export function PageHeader({ title, subtitle, actions, eyebrow }: { title: ReactNode; subtitle?: ReactNode;
  actions?: ReactNode; eyebrow?: ReactNode }) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between mb-6">
      <div className="min-w-0">
        {eyebrow && <div className="mb-1 text-[12px] font-medium uppercase tracking-wider text-olive-600 dark:text-olive-400">{eyebrow}</div>}
        <h1 className="text-[22px] sm:text-2xl font-semibold tracking-tight text-ink">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted max-w-2xl">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function StatCard({ label, value, sub, icon, tone = 'brand', footer }: { label: string; value: ReactNode;
  sub?: ReactNode; icon?: ReactNode; tone?: 'brand' | 'olive' | 'accent' | 'amber' | 'red' | 'slate'; footer?: ReactNode }) {
  const tones = {
    brand: 'bg-brand-50 text-brand-700 dark:bg-brand-900/50 dark:text-brand-200',
    olive: 'bg-olive-50 text-olive-700 dark:bg-olive-900/30 dark:text-olive-300',
    accent: 'bg-sky-50 text-sky-700 dark:bg-sky-900/30 dark:text-sky-300',
    amber: 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
    red: 'bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300',
    slate: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
  };
  return (
    <motion.div className="card p-5 flex flex-col" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}>
      <div className="flex items-start justify-between gap-3">
        <p className="text-[13px] font-medium text-muted">{label}</p>
        {icon && <div className={clsx('flex h-8 w-8 items-center justify-center rounded-lg', tones[tone])}>{icon}</div>}
      </div>
      <div className="mt-2 text-[24px] leading-tight font-semibold tracking-tight text-ink num break-words">{value}</div>
      {sub && <div className="mt-1 text-[12.5px] text-muted">{sub}</div>}
      {footer && <div className="mt-3 pt-3 border-t border-line text-[12.5px]">{footer}</div>}
    </motion.div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { value: T; label: ReactNode; count?: number }[];
  value: T; onChange: (v: T) => void }) {
  return (
    <div role="tablist" className="flex gap-1 overflow-x-auto border-b border-line -mx-1 px-1">
      {tabs.map((t) => (
        <button key={t.value} role="tab" aria-selected={value === t.value} onClick={() => onChange(t.value)}
          className={clsx('relative whitespace-nowrap px-3 py-2.5 text-sm font-medium transition-colors',
            value === t.value ? 'text-ink' : 'text-muted hover:text-ink')}>
          <span className="inline-flex items-center gap-2">{t.label}
            {t.count !== undefined && <span className="rounded-full bg-surface-2 border border-line px-1.5 text-[11px] num">{t.count}</span>}
          </span>
          {value === t.value && <motion.span layoutId="tab-underline" className="absolute inset-x-2 -bottom-px h-0.5 rounded bg-brand-600 dark:bg-brand-300" />}
        </button>
      ))}
    </div>
  );
}

const ICONS = { success: CheckCircle2, error: XCircle, warning: AlertTriangle, info: Info };
const ICON_TONE = { success: 'text-olive-600', error: 'text-red-600', warning: 'text-amber-600', info: 'text-sky-600' };

export function Toaster() {
  const { toasts, dismiss } = useUi();
  return (
    <div aria-live="polite" className="pointer-events-none fixed inset-x-0 bottom-0 z-[60] flex flex-col items-center gap-2 p-4 sm:items-end">
      <AnimatePresence>
        {toasts.map((t) => {
          const Icon = ICONS[t.kind];
          return (
            <motion.div key={t.id} layout initial={{ opacity: 0, y: 16, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, scale: 0.96 }}
              className="pointer-events-auto w-full max-w-sm card shadow-pop p-3.5 flex gap-3">
              <Icon className={clsx('h-5 w-5 shrink-0', ICON_TONE[t.kind])} aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-ink">{t.title}</p>
                {t.body && <p className="mt-0.5 text-[13px] text-muted break-words">{t.body}</p>}
              </div>
              <button onClick={() => dismiss(t.id)} aria-label="Dismiss" className="text-muted hover:text-ink"><X className="h-4 w-4" /></button>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}

export function KeyValue({ items, cols = 2 }: { items: { label: string; value: ReactNode }[]; cols?: 2 | 3 | 4 }) {
  return (
    <dl className={clsx('grid gap-x-6 gap-y-4 grid-cols-1 xs:grid-cols-2', cols >= 3 && 'lg:grid-cols-3', cols === 4 && 'xl:grid-cols-4')}>
      {items.map((i) => (
        <div key={i.label} className="min-w-0">
          <dt className="text-[12px] text-muted">{i.label}</dt>
          <dd className="mt-0.5 text-sm font-medium text-ink break-words">{i.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Notice({ tone = 'info', title, children, action }: { tone?: 'info' | 'warning' | 'demo' | 'danger';
  title?: ReactNode; children: ReactNode; action?: ReactNode }) {
  const t = {
    info: 'border-sky-200 bg-sky-50 text-sky-900 dark:border-sky-900/60 dark:bg-sky-950/40 dark:text-sky-100',
    warning: 'border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-100',
    demo: 'border-fuchsia-200 bg-fuchsia-50 text-fuchsia-900 dark:border-fuchsia-900/60 dark:bg-fuchsia-950/30 dark:text-fuchsia-100',
    danger: 'border-red-200 bg-red-50 text-red-900 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-100',
  }[tone];
  return (
    <div className={clsx('rounded-xl border px-4 py-3 text-[13px] flex flex-col sm:flex-row sm:items-center gap-3', t)}>
      <div className="flex-1 min-w-0">{title && <p className="font-semibold mb-0.5">{title}</p>}<div className="opacity-90">{children}</div></div>
      {action}
    </div>
  );
}
