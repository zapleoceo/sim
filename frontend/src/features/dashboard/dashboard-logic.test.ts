import { summary } from './dashboard-fixtures';
import {
  buildKpis, channelRows, dailyPoints, formatCurrencyTotals, periodValid, presetPeriod, stageBars,
} from './dashboard-logic';

describe('dashboard logic', () => {
  it('formats currency buckets explicitly', () => {
    expect(formatCurrencyTotals(null)).toBe('Сводка по валютам недоступна');
    expect(formatCurrencyTotals([])).toBe('Нет сумм');
    const text = formatCurrencyTotals([{ currency: 'USD', total: 1234.5 }, { currency: null, total: 10 }]);
    expect(text).toContain('USD');
    expect(text).toContain('без валюты (непроверенная сумма)');
    expect(text).toContain(' · ');
  });

  it('builds KPI cards including guard breakdown and conversions', () => {
    const kpis = buildKpis(summary);
    const byLabel = Object.fromEntries(kpis.map((k) => [k.label, k]));
    expect(byLabel['Контакты'].hint).toBe('новых: 40, ответили: 75');
    expect(byLabel['Запросы к ИИ'].value).toMatch(/410/);
    expect(byLabel['Контролер ответов'].value).toBe('13');
    expect(byLabel['Контролер ответов'].hint).toBe('ок: 10, исправлено: 2, заблокировано: 1');
    expect(byLabel['Регистрация → оплата'].value).toMatch(/25/);
    expect(byLabel['Выплаты партнёра'].value).toBe('Нет сумм');
  });

  it('maps stages, days and channels', () => {
    const bars = stageBars(summary);
    expect(bars).toHaveLength(7);
    expect(bars[0]).toMatchObject({ label: 'Первый контакт', value: 50 });
    expect(bars[2].value).toBe(0);
    expect(dailyPoints(summary)[0]).toEqual({ day: '08.10', contacts: 4, registrations: 1, payments: 0 });
    expect(channelRows(summary)[0]).toMatchObject({ label: 'WhatsApp', contacts: '70' });
  });

  it('computes preset periods and validates order', () => {
    const p = presetPeriod(7, new Date(2026, 10, 11));
    expect(p).toEqual({ from: '2026-11-05', to: '2026-11-11' });
    expect(periodValid(p)).toBe(true);
    expect(periodValid({ from: '2026-11-12', to: '2026-11-11' })).toBe(false);
    expect(periodValid({ from: '', to: '2026-11-11' })).toBe(false);
  });
});
