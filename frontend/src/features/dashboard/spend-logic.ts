import type { AnalyticsSpend } from '@/lib/models';

/** What each Broker capability is in SIM terms. */
export const CAPABILITY_LABELS: Record<string, string> = {
  speech: 'Голосовые (синтез речи)',
  'chat:sales': 'Ответы персонажей',
  structured: 'Охранник',
  prefilter: 'Охранник (запасной маршрут)',
  transcription: 'Распознавание голосовых',
  vision: 'Распознавание картинок',
  'image:generate': 'Студия: картинки',
  'image:edit': 'Студия: правка картинок',
};

export interface SpendRow { capability: string; label: string; calls: number; cost_usd: number }
export interface CapLine { label: string; used: number; cap: number | null }

export function spendRows(s: Extract<AnalyticsSpend, { available: true }>): SpendRow[] {
  return s.by_capability.map((c) => ({
    capability: c.capability,
    label: CAPABILITY_LABELS[c.capability] ?? c.capability,
    calls: c.calls,
    cost_usd: c.cost_usd,
  }));
}

/** Today's and this month's spend against the project caps set in Broker (null cap = no limit). */
export function capLines(s: Extract<AnalyticsSpend, { available: true }>): CapLine[] {
  return [
    { label: 'Сегодня', used: s.limits.today_usd, cap: s.limits.daily_cap_usd },
    { label: 'Этот месяц', used: s.limits.month_usd, cap: s.limits.monthly_cap_usd },
  ];
}

/** Share of the cap used, 0..1; null when there is no cap or it is zero. */
export function capShare(line: CapLine): number | null {
  if (line.cap === null || line.cap <= 0) return null;
  return Math.min(1, line.used / line.cap);
}
