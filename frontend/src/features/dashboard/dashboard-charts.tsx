import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { DAILY_SERIES, type BarDatum, type DailyPoint } from './dashboard-logic';

const axis = { fontSize: 12, fill: 'var(--muted-foreground)' };
const tooltipStyle = {
  background: 'var(--popover)',
  border: '1px solid var(--border)',
  borderRadius: 8,
  color: 'var(--popover-foreground)',
  fontSize: 12,
};

/** Funnel by stage: horizontal bars in stage colors. */
export function StageBarChart({ data }: { data: BarDatum[] }) {
  return (
    <div className="h-72" role="img" aria-label={`Воронка: ${data.map((d) => `${d.label} — ${d.value}`).join(', ')}`}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16, top: 4, bottom: 4 }}>
          <CartesianGrid horizontal={false} stroke="var(--border)" />
          <XAxis type="number" allowDecimals={false} tick={axis} axisLine={false} tickLine={false} />
          <YAxis type="category" dataKey="label" width={110} tick={axis} axisLine={false} tickLine={false} />
          <Tooltip cursor={{ fill: 'var(--muted)' }} contentStyle={tooltipStyle} />
          <Bar dataKey="value" name="Клиенты" radius={4} maxBarSize={22}>
            {data.map((d) => (
              <Cell key={d.label} fill={d.color} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Daily dynamics: contacts, registrations, payments. */
export function DailyLineChart({ data }: { data: DailyPoint[] }) {
  return (
    <div className="h-72" role="img" aria-label={`График по дням: ${DAILY_SERIES.map((s) => s.name).join(', ')}`}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ left: 0, right: 12, top: 4, bottom: 4 }}>
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis dataKey="day" tick={axis} axisLine={false} tickLine={false} minTickGap={16} />
          <YAxis allowDecimals={false} tick={axis} axisLine={false} tickLine={false} width={36} />
          <Tooltip contentStyle={tooltipStyle} />
          <Legend verticalAlign="top" align="left" iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
          {DAILY_SERIES.map((s) => (
            <Line key={s.key} type="monotone" dataKey={s.key} name={s.name} stroke={s.color} strokeWidth={2} dot={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
