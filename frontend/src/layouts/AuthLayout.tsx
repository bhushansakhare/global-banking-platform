import { Activity, GitCompareArrows, ShieldCheck, Workflow } from 'lucide-react';
import type { ReactNode } from 'react';

export function AuthLayout({ children, title, subtitle }: { children: ReactNode; title: string; subtitle: string }) {
  return (
    <div className="min-h-screen grid lg:grid-cols-[1.05fr_1fr]">
      <section className="hidden lg:flex relative overflow-hidden bg-brand-800 text-white p-12 flex-col justify-between">
        <div className="absolute inset-0 opacity-[0.07]" aria-hidden
          style={{ backgroundImage: 'radial-gradient(circle at 1px 1px, white 1px, transparent 0)', backgroundSize: '22px 22px' }} />
        <div className="absolute -right-24 -top-24 h-80 w-80 rounded-full bg-olive-500/20 blur-3xl" aria-hidden />
        <div className="relative flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/10 ring-1 ring-white/20"><Activity className="h-5 w-5" /></div>
          <div><div className="text-lg font-semibold tracking-tight">Meridian</div><div className="text-[12px] text-white/60">Global Banking Intelligence</div></div>
        </div>
        <div className="relative max-w-md">
          <h2 className="text-[30px] leading-tight font-semibold tracking-tight">Set up once. Let automation handle the daily banking routine.</h2>
          <p className="mt-4 text-white/70 text-[15px] leading-relaxed">Collect balances and statements, extract transactions and reconcile every account — across banks, countries and currencies.</p>
          <ul className="mt-8 space-y-4 text-[14px]">
            {[[Workflow, 'Durable daily automation with retries and full audit trail'],
              [GitCompareArrows, 'Deterministic reconciliation — uncertain matches are never auto-confirmed'],
              [ShieldCheck, 'Read-only by design: no payments, transfers or account changes']].map(([Icon, text], i) => {
              const I = Icon as typeof Workflow;
              return <li key={i} className="flex gap-3"><I className="h-5 w-5 text-olive-300 shrink-0" /><span className="text-white/85">{text as string}</span></li>;
            })}
          </ul>
        </div>
        <p className="relative text-[12px] text-white/45">© {new Date().getFullYear()} Meridian. Banking data is accessed only through authorised channels.</p>
      </section>
      <section className="flex items-center justify-center px-4 py-10 sm:px-8">
        <div className="w-full max-w-[420px]">
          <div className="lg:hidden mb-8 flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-700 text-white"><Activity className="h-4 w-4" /></div>
            <span className="text-lg font-semibold">Meridian</span>
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink">{title}</h1>
          <p className="mt-1.5 text-sm text-muted">{subtitle}</p>
          <div className="mt-7">{children}</div>
        </div>
      </section>
    </div>
  );
}
