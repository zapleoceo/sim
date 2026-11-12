import type { AnalyticsSpend } from '@/lib/models';
import { capLines, capShare, spendRows } from './spend-logic';

const spend: Extract<AnalyticsSpend, { available: true }> = {
  available: true, from: '2026-11-01', to: '2026-12-01', currency: 'USD',
  total: { calls: 3, cost_usd: 0.03, tokens_in: 0, tokens_out: 0 },
  by_capability: [
    { capability: 'speech', calls: 2, cost_usd: 0.03, tokens_in: 0, tokens_out: 0 },
    { capability: 'new:thing', calls: 1, cost_usd: 0, tokens_in: 0, tokens_out: 0 },
  ],
  by_day: [],
  limits: { daily_cap_usd: 5, monthly_cap_usd: null, today_usd: 4.8, month_usd: 12 },
};

describe('spend logic', () => {
  it('labels Broker capabilities in SIM terms and keeps unknown ones as is', () => {
    expect(spendRows(spend).map((r) => r.label)).toEqual(['Голосовые (синтез речи)', 'new:thing']);
  });

  it('shows today and the month against the caps; no cap means no meter', () => {
    const [today, month] = capLines(spend);
    expect(today).toEqual({ label: 'Сегодня', used: 4.8, cap: 5 });
    expect(capShare(today!)).toBeCloseTo(0.96);
    expect(capShare(month!)).toBeNull();
    expect(capShare({ label: 'x', used: 9, cap: 5 })).toBe(1);
    expect(capShare({ label: 'x', used: 0, cap: 0 })).toBeNull();
  });
});
