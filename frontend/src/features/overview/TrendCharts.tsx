import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { TrendPoint } from '@/api/types';
import { compactInr, shortDate } from '@/lib/format';

const rgb = (name: string) => `rgb(var(--${name}))`;
const axisTick = { fill: rgb('text-muted'), fontSize: 12 };
const tooltipStyle = {
  backgroundColor: rgb('surface'),
  border: `1px solid ${rgb('border')}`,
  borderRadius: 8,
  color: rgb('text'),
  fontSize: 12,
};

/** Screen-reader text alternative: latest value and min-max over the period. */
function summarize(data: TrendPoint[], label: string, fmt: (n: number) => string): string {
  if (data.length === 0) return `No ${label} data.`;
  const values = data.map((p) => p.value);
  const last = data[data.length - 1];
  return `${label}: latest ${fmt(last.value)} on ${shortDate(last.date)}; range ${fmt(Math.min(...values))} to ${fmt(Math.max(...values))} from ${shortDate(data[0].date)} to ${shortDate(last.date)}.`;
}

export function ExposureTrendChart({ data }: { data: TrendPoint[] }) {
  return (
    <>
      <p className="sr-only">{summarize(data, 'Exposure', compactInr)}</p>
      <div role="img" aria-label="Area chart of exposure over time" className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={rgb('border')} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="date" tickFormatter={shortDate} tick={axisTick} stroke={rgb('border')} minTickGap={24} />
          <YAxis tickFormatter={compactInr} tick={axisTick} stroke={rgb('border')} width={56} />
          <Tooltip
            contentStyle={tooltipStyle}
            labelFormatter={(l) => shortDate(String(l))}
            formatter={(v) => [compactInr(Number(v)), 'Exposure']}
          />
          <Area type="monotone" dataKey="value" stroke={rgb('primary')} fill={rgb('primary')} fillOpacity={0.2} strokeWidth={2} />
        </AreaChart>
      </ResponsiveContainer>
      </div>
    </>
  );
}

export function CasesTrendChart({ data }: { data: TrendPoint[] }) {
  return (
    <>
      <p className="sr-only">{summarize(data, 'Open risk cases', String)}</p>
      <div role="img" aria-label="Bar chart of open risk cases over time" className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={rgb('border')} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="date" tickFormatter={shortDate} tick={axisTick} stroke={rgb('border')} minTickGap={24} />
          <YAxis allowDecimals={false} tick={axisTick} stroke={rgb('border')} width={32} />
          <Tooltip
            contentStyle={tooltipStyle}
            cursor={{ fill: rgb('surface-2') }}
            labelFormatter={(l) => shortDate(String(l))}
            formatter={(v) => [String(v), 'Cases']}
          />
          <Bar dataKey="value" fill={rgb('warning')} radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
      </div>
    </>
  );
}
