import { daysAgo, formatDayMonth, formatInt, formatMoney, toIsoDate } from '@/lib/format';
import type { AnalyticsSummary, ChannelKind, CurrencyTotal } from '@/lib/models';
import { CHANNEL_LABELS, STAGES } from '@/lib/stages';

export interface Kpi { label: string; value: string; hint?: string }
export interface Period { from: string; to: string }
export interface BarDatum { label: string; value: number; color: string }
export interface DailyPoint { day: string; contacts: number; registrations: number; payments: number }
export interface ChannelRow { kind: ChannelKind; label: string; contacts: string; registrations: string; payments: string }

export const PRESETS = [7, 30, 90] as const;

/** Last `days` days including today, as ISO dates. */
export function presetPeriod(days: number, now = new Date()): Period {
  return { from: toIsoDate(daysAgo(days - 1, now)), to: toIsoDate(now) };
}

export function periodValid(p: Period): boolean {
  return Boolean(p.from && p.to && p.from <= p.to);
}

/** Keep partner currencies explicit, including non-ISO labels and a missing currency. */
export function formatCurrencyTotals(buckets: CurrencyTotal[] | null | undefined): string {
  if (!buckets) return 'Сводка по валютам недоступна';
  const number = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return buckets.length
    ? buckets.map((b) => `${number.format(b.total)} ${b.currency ?? 'без валюты (непроверенная сумма)'}`).join(' · ')
    : 'Нет сумм';
}

const percentFmt = new Intl.NumberFormat('ru-RU', { style: 'percent', maximumFractionDigits: 1 });

export function formatPercent(v: number): string {
  return percentFmt.format(Number.isFinite(v) ? v : 0);
}

export function buildKpis(d: AnalyticsSummary): Kpi[] {
  return [
    { label: 'Контакты', value: formatInt(d.contacts), hint: `новых: ${d.new_contacts}, ответили: ${d.replied}` },
    { label: 'Регистрации', value: formatInt(d.registrations) },
    { label: 'Оплаты', value: formatInt(d.payments) },
    { label: 'Выручка', value: formatCurrencyTotals(d.revenue_by_currency) },
    { label: 'Выплаты партнёра', value: formatCurrencyTotals(d.partner_payouts_by_currency) },
    { label: 'Расход на LLM', value: formatMoney(d.llm.cost_usd, 'USD', 2), hint: `${d.llm.calls} вызовов` },
    {
      label: 'Контролер ответов',
      value: formatInt(d.guard.ok + d.guard.rewritten + d.guard.blocked),
      hint: `ок: ${d.guard.ok}, исправлено: ${d.guard.rewritten}, заблокировано: ${d.guard.blocked}`,
    },
    { label: 'Контакт → регистрация', value: formatPercent(d.conversion.contact_to_registration) },
    { label: 'Регистрация → оплата', value: formatPercent(d.conversion.registration_to_payment) },
  ];
}

export function stageBars(d: AnalyticsSummary): BarDatum[] {
  return STAGES.map((s) => ({ label: s.label, value: d.by_stage[s.id] ?? 0, color: s.color }));
}

export function dailyPoints(d: AnalyticsSummary): DailyPoint[] {
  return d.daily.map((x) => ({
    day: formatDayMonth(x.date),
    contacts: x.contacts,
    registrations: x.registrations,
    payments: x.payments,
  }));
}

export const DAILY_SERIES = [
  { key: 'contacts', name: 'Контакты', color: 'var(--stage-2)' },
  { key: 'registrations', name: 'Регистрации', color: 'var(--stage-4)' },
  { key: 'payments', name: 'Оплаты', color: 'var(--stage-7)' },
] as const;

export function channelRows(d: AnalyticsSummary): ChannelRow[] {
  return d.by_channel.map((c) => ({
    kind: c.kind,
    label: CHANNEL_LABELS[c.kind] ?? c.kind,
    contacts: formatInt(c.contacts),
    registrations: formatInt(c.registrations),
    payments: formatInt(c.payments),
  }));
}
