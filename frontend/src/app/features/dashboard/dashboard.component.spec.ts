import { formatCurrencyTotals } from './dashboard.component';

describe('partner currency totals', () => {
  it('shows every currency explicitly with cents and no conversion', () => {
    const value = formatCurrencyTotals([{ currency: 'EUR', total: 10.25 }, { currency: 'USD', total: 20.5 }]);
    expect(value).toContain('10,25 EUR');
    expect(value).toContain('20,50 USD');
  });

  it('does not assume a currency for missing codes or empty periods', () => {
    expect(formatCurrencyTotals([{ currency: null, total: 7 }])).toContain('7,00 без валюты (непроверенная сумма)');
    expect(formatCurrencyTotals([])).toBe('Нет сумм');
    expect(formatCurrencyTotals([{ currency: 'USD', total: 0 }])).toContain('0,00 USD');
  });

  it('renders custom currency labels without Intl currency validation', () => {
    expect(formatCurrencyTotals([{ currency: 'NOT-ISO', total: 2 }])).toContain('2,00 NOT-ISO');
  });

  it('reports older server responses as unavailable rather than assuming USD or zero', () => {
    expect(formatCurrencyTotals(undefined)).toBe('Сводка по валютам недоступна');
    expect(formatCurrencyTotals(null)).toBe('Сводка по валютам недоступна');
  });
});
