import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { useUi } from '@/stores/ui';
import { date as fmtDate, money } from '@/utils/format';

export const SERIES = { brand: '#2B296E', brandDark: '#9B98D3', olive: '#97AC3B', accent: '#0EA5E9', slate: '#94A3B8' };
export const CATEGORICAL = ['#2B296E', '#97AC3B', '#0EA5E9', '#8B5CF6', '#F59E0B', '#14B8A6', '#EC4899', '#64748B'];
export const CATEGORICAL_DARK = ['#9B98D3', '#A9BE4C', '#38BDF8', '#A78BFA', '#FBBF24', '#2DD4BF', '#F472B6', '#94A3B8'];

function useTheme() {
  const dark = useUi((s) => s.theme) === 'dark';
  return {
    dark,
    primary: dark ? SERIES.brandDark : SERIES.brand,
    grid: dark ? '#2A2C45' : '#E8E9F1',
    axis: dark ? '#8E91AE' : '#6B6F8A',
    palette: dark ? CATEGORICAL_DARK : CATEGORICAL,
    tooltip: { background: dark ? '#191B2E' : '#FFFFFF', border: `1px solid ${dark ? '#2C2E4A' : '#E4E6EF'}`,
      borderRadius: 10, fontSize: 12, boxShadow: '0 8px 24px -8px rgba(0,0,0,.2)' },
  };
}

const axisProps = (color: string) => ({ tick: { fill: color, fontSize: 11 }, tickLine: false, axisLine: false });

export function BalanceArea({ data, currency, height = 260 }: { data: { date: string; balance: number | null }[];
  currency: string; height?: number }) {
  const t = useTheme();
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="balFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={t.primary} stopOpacity={0.22} />
            <stop offset="100%" stopColor={t.primary} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} stroke={t.grid} />
        <XAxis dataKey="date" {...axisProps(t.axis)} minTickGap={36} tickFormatter={(v) => fmtDate(v, { day: '2-digit', month: 'short' })} />
        <YAxis {...axisProps(t.axis)} width={64} tickFormatter={(v) => money(v, undefined, { compact: true })} />
        <Tooltip contentStyle={t.tooltip} labelFormatter={(v) => fmtDate(String(v))}
          formatter={(v: number) => [money(v, currency), 'Balance']} />
        <Area type="monotone" dataKey="balance" stroke={t.primary} strokeWidth={2} fill="url(#balFill)" dot={false}
          activeDot={{ r: 4 }} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function FlowBars({ data, currency, height = 260, xKey = 'date', inKey = 'inflow', outKey = 'outflow' }: {
  data: Record<string, string | number | null>[]; currency: string; height?: number; xKey?: string; inKey?: string; outKey?: string }) {
  const t = useTheme();
  const isMonth = xKey === 'month';
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barGap={2}>
        <CartesianGrid vertical={false} stroke={t.grid} />
        <XAxis dataKey={xKey} {...axisProps(t.axis)} minTickGap={24}
          tickFormatter={(v) => isMonth ? fmtDate(`${v}-01`, { month: 'short', year: '2-digit' }) : fmtDate(v, { day: '2-digit', month: 'short' })} />
        <YAxis {...axisProps(t.axis)} width={64} tickFormatter={(v) => money(v, undefined, { compact: true })} />
        <Tooltip contentStyle={t.tooltip} cursor={{ fill: t.grid, opacity: 0.4 }}
          labelFormatter={(v) => isMonth ? fmtDate(`${v}-01`, { month: 'long', year: 'numeric' }) : fmtDate(String(v))}
          formatter={(v: number, name: string) => [money(v, currency), name]} />
        <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12, color: t.axis }} />
        <Bar dataKey={inKey} name="Money in" fill={SERIES.olive} radius={[4, 4, 0, 0]} maxBarSize={28} />
        <Bar dataKey={outKey} name="Money out" fill={t.primary} radius={[4, 4, 0, 0]} maxBarSize={28} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function Donut({ data, currency, height = 240 }: { data: { name: string; value: number; colour?: string }[];
  currency: string; height?: number }) {
  const t = useTheme();
  const total = data.reduce((s, d) => s + (d.value || 0), 0);
  return (
    <div className="flex flex-col sm:flex-row items-center gap-4">
      <div className="w-full sm:w-1/2" style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={data} dataKey="value" nameKey="name" innerRadius="62%" outerRadius="92%" paddingAngle={2}
              stroke="none">
              {data.map((d, i) => <Cell key={d.name} fill={d.colour ?? t.palette[i % t.palette.length]} />)}
            </Pie>
            <Tooltip contentStyle={t.tooltip} formatter={(v: number, n: string) => [money(v, currency), n]} />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <ul className="w-full sm:w-1/2 space-y-2 text-[13px]">
        {data.slice(0, 7).map((d, i) => (
          <li key={d.name} className="flex items-center gap-2 min-w-0">
            <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: d.colour ?? t.palette[i % t.palette.length] }} />
            <span className="truncate text-ink">{d.name}</span>
            <span className="ml-auto num text-muted">{total ? `${((d.value / total) * 100).toFixed(0)}%` : '—'}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function RateLine({ data, height = 220 }: { data: { week: string; match_rate: number | null; exceptions: number }[];
  height?: number }) {
  const t = useTheme();
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid vertical={false} stroke={t.grid} />
        <XAxis dataKey="week" {...axisProps(t.axis)} tickFormatter={(v) => fmtDate(v, { day: '2-digit', month: 'short' })} />
        <YAxis yAxisId="l" {...axisProps(t.axis)} width={40} domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
        <YAxis yAxisId="r" orientation="right" {...axisProps(t.axis)} width={32} allowDecimals={false} />
        <Tooltip contentStyle={t.tooltip} labelFormatter={(v) => `Week of ${fmtDate(String(v))}`}
          formatter={(v: number, n: string) => [n === 'Match rate' ? `${(v * 100).toFixed(1)}%` : v, n]} />
        <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12, color: t.axis }} />
        <Line yAxisId="l" type="monotone" dataKey="match_rate" name="Match rate" stroke={SERIES.olive} strokeWidth={2} dot={{ r: 3 }} />
        <Line yAxisId="r" type="monotone" dataKey="exceptions" name="Exceptions" stroke={SERIES.accent} strokeWidth={2} dot={{ r: 3 }} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function HBars({ data, currency, height = 260 }: { data: { name: string; value: number; colour?: string }[];
  currency: string; height?: number }) {
  const t = useTheme();
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, left: 4, bottom: 0 }}>
        <CartesianGrid horizontal={false} stroke={t.grid} />
        <XAxis type="number" {...axisProps(t.axis)} tickFormatter={(v) => money(v, undefined, { compact: true })} />
        <YAxis type="category" dataKey="name" {...axisProps(t.axis)} width={120} />
        <Tooltip contentStyle={t.tooltip} cursor={{ fill: t.grid, opacity: 0.4 }} formatter={(v: number) => [money(v, currency), 'Amount']} />
        <Bar dataKey="value" radius={[0, 4, 4, 0]} maxBarSize={18}>
          {data.map((d, i) => <Cell key={d.name} fill={d.colour ?? t.palette[i % t.palette.length]} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
