import clsx from 'clsx';
import { Loader2 } from 'lucide-react';
import { forwardRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode,
  type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react';
import { statusTone, type Tone } from '@/utils/status';

/* ---------- Button ---------- */
type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'success' | 'outline';
type Size = 'sm' | 'md' | 'lg' | 'icon';

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-brand-700 text-white hover:bg-brand-800 shadow-sm dark:bg-brand-500 dark:hover:bg-brand-400',
  secondary: 'bg-surface-2 text-ink border border-line hover:bg-line/50',
  outline: 'border border-line text-ink hover:bg-surface-2 bg-surface',
  ghost: 'text-muted hover:text-ink hover:bg-surface-2',
  danger: 'bg-red-600 text-white hover:bg-red-700 shadow-sm',
  success: 'bg-olive-600 text-white hover:bg-olive-700 shadow-sm',
};
const SIZES: Record<Size, string> = {
  sm: 'h-8 px-3 text-[13px] gap-1.5', md: 'h-10 px-4 text-sm gap-2', lg: 'h-11 px-5 text-[15px] gap-2',
  icon: 'h-9 w-9 justify-center',
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  icon?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', loading, icon, className, children, disabled, type = 'button', ...rest }, ref) {
  return (
    <button ref={ref} type={type} disabled={disabled || loading}
      className={clsx('inline-flex items-center rounded-lg font-medium transition-colors select-none',
        'disabled:opacity-55 disabled:cursor-not-allowed whitespace-nowrap', VARIANTS[variant], SIZES[size], className)}
      {...rest}>
      {loading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
});

/* ---------- Badge ---------- */
const TONES: Record<Tone, string> = {
  neutral: 'bg-slate-100 text-slate-700 ring-slate-200 dark:bg-slate-800/60 dark:text-slate-300 dark:ring-slate-700',
  brand: 'bg-brand-50 text-brand-700 ring-brand-200 dark:bg-brand-900/50 dark:text-brand-200 dark:ring-brand-800',
  success: 'bg-olive-50 text-olive-800 ring-olive-200 dark:bg-olive-900/30 dark:text-olive-300 dark:ring-olive-800',
  warning: 'bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-900/25 dark:text-amber-300 dark:ring-amber-800/60',
  danger: 'bg-red-50 text-red-700 ring-red-200 dark:bg-red-900/25 dark:text-red-300 dark:ring-red-800/60',
  info: 'bg-sky-50 text-sky-700 ring-sky-200 dark:bg-sky-900/25 dark:text-sky-300 dark:ring-sky-800/60',
  demo: 'bg-fuchsia-50 text-fuchsia-700 ring-fuchsia-200 dark:bg-fuchsia-900/25 dark:text-fuchsia-300 dark:ring-fuchsia-800/60',
};

export function Badge({ tone = 'neutral', children, className, dot }: { tone?: Tone; children: ReactNode;
  className?: string; dot?: boolean }) {
  return (
    <span className={clsx('inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11.5px] font-medium ring-1 ring-inset',
      'whitespace-nowrap', TONES[tone], className)}>
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current opacity-80" aria-hidden />}
      {children}
    </span>
  );
}

export function StatusBadge({ status, className }: { status: string | null | undefined; className?: string }) {
  const [tone, label] = statusTone(status);
  return <Badge tone={tone} dot className={className}>{label}</Badge>;
}

export function DemoBadge({ show = true, label = 'DEMO' }: { show?: boolean; label?: string }) {
  if (!show) return null;
  return <Badge tone="demo" className="tracking-wide font-semibold" >{label}</Badge>;
}

/* ---------- Card ---------- */
export function Card({ className, children, padded = true }: { className?: string; children: ReactNode; padded?: boolean }) {
  return <div className={clsx('card', padded && 'p-5', className)}>{children}</div>;
}

export function CardHeader({ title, subtitle, action, icon }: { title: ReactNode; subtitle?: ReactNode;
  action?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 mb-4">
      <div className="flex items-start gap-3 min-w-0">
        {icon && <div className="mt-0.5 text-brand-600 dark:text-brand-300">{icon}</div>}
        <div className="min-w-0">
          <h3 className="text-[15px] font-semibold text-ink leading-tight">{title}</h3>
          {subtitle && <p className="text-[13px] text-muted mt-0.5">{subtitle}</p>}
        </div>
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

/* ---------- Skeleton / Spinner ---------- */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div className={clsx('relative overflow-hidden rounded-md bg-line/60', className)} aria-hidden>
      <div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-white/40 to-transparent dark:via-white/5" />
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={clsx('h-5 w-5 animate-spin text-brand-500', className)} aria-label="Loading" />;
}

/* ---------- Form controls ---------- */
export function Field({ label, hint, error, children, htmlFor, required }: { label: string; hint?: ReactNode;
  error?: string | null; children: ReactNode; htmlFor?: string; required?: boolean }) {
  return (
    <div>
      <label htmlFor={htmlFor} className="label">{label}{required && <span className="text-red-500"> *</span>}</label>
      {children}
      {error ? <p className="mt-1 text-xs text-red-600 dark:text-red-400" role="alert">{error}</p>
        : hint ? <p className="mt-1 text-xs text-muted">{hint}</p> : null}
    </div>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...rest }, ref) {
  return <input ref={ref} className={clsx('input', className)} {...rest} />;
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, children, ...rest }, ref) {
  return (
    <select ref={ref} className={clsx('input pr-8 appearance-none bg-no-repeat bg-[right_0.6rem_center] bg-[length:14px]',
      "bg-[url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%23888'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M6 9l6 6 6-6'/%3E%3C/svg%3E\")]",
      className)} {...rest}>
      {children}
    </select>
  );
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
  { className, ...rest }, ref) {
  return <textarea ref={ref} className={clsx('input h-auto py-2 min-h-[80px]', className)} {...rest} />;
});

export function Toggle({ checked, onChange, disabled, label, size = 'md' }: { checked: boolean;
  onChange: (v: boolean) => void; disabled?: boolean; label: string; size?: 'sm' | 'md' | 'lg' }) {
  const dims = { sm: ['h-5 w-9', 'h-4 w-4', 'translate-x-4'], md: ['h-6 w-11', 'h-5 w-5', 'translate-x-5'],
    lg: ['h-8 w-[60px]', 'h-7 w-7', 'translate-x-7'] }[size];
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled}
      onClick={() => onChange(!checked)}
      className={clsx('relative inline-flex shrink-0 items-center rounded-full p-0.5 transition-colors',
        'disabled:opacity-50 disabled:cursor-not-allowed', dims[0],
        checked ? 'bg-olive-500' : 'bg-slate-300 dark:bg-slate-600')}>
      <span className={clsx('rounded-full bg-white shadow transition-transform', dims[1], checked ? dims[2] : 'translate-x-0')} />
    </button>
  );
}

export function Checkbox({ checked, onChange, label, indeterminate }: { checked: boolean; onChange: (v: boolean) => void;
  label: string; indeterminate?: boolean }) {
  return (
    <input type="checkbox" aria-label={label} checked={checked}
      ref={(el) => { if (el) el.indeterminate = !!indeterminate && !checked; }}
      onChange={(e) => onChange(e.target.checked)}
      className="h-4 w-4 rounded border-line text-brand-600 focus:ring-brand-500 accent-brand-700 cursor-pointer" />
  );
}
