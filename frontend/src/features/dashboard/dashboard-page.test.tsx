import { waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { fakeApi } from '@/test/fake-api';
import { renderApp, screen } from '@/test/render-app';
import { summary } from './dashboard-fixtures';

// Charts need real layout; their data mapping is covered in dashboard-logic.test.ts.
vi.mock('./dashboard-charts', () => ({ StageBarChart: () => null, DailyLineChart: () => null }));

describe('DashboardPage', () => {
  it('loads the last 30 days and shows KPIs and the channel table', async () => {
    const log = fakeApi({ 'GET /api/personas': { items: [] }, 'GET /api/analytics/summary': summary });
    await renderApp('/dashboard');

    const kpis = await screen.findByRole('region', { name: 'Ключевые показатели' });
    expect(kpis).toHaveTextContent('Регистрации');
    expect(kpis).toHaveTextContent('новых: 40, ответили: 75');
    expect(screen.getByText('WhatsApp')).toBeInTheDocument();

    const req = log.find((r) => r.path === '/api/analytics/summary')!;
    const from = new Date(req.query.get('from')!);
    const to = new Date(req.query.get('to')!);
    expect(Math.round((to.getTime() - from.getTime()) / 86_400_000)).toBe(29);
    expect(req.query.has('persona_id')).toBe(false);
  });

  it('shows the money spent from Broker with the caps and a breakdown', async () => {
    fakeApi({
      'GET /api/personas': { items: [] },
      'GET /api/analytics/summary': summary,
      'GET /api/analytics/spend': {
        available: true, from: '2026-11-01', to: '2026-12-01', currency: 'USD',
        total: { calls: 15, cost_usd: 0.029, tokens_in: 0, tokens_out: 0 },
        by_capability: [{ capability: 'speech', calls: 15, cost_usd: 0.029, tokens_in: 0, tokens_out: 0 }],
        by_day: [],
        limits: { daily_cap_usd: 5, monthly_cap_usd: 50, today_usd: 0.01, month_usd: 0.029 },
      },
    });
    await renderApp('/dashboard');
    expect(await screen.findByLabelText('Потрачено за период')).toHaveTextContent('0,0290');
    expect(screen.getByText('Голосовые (синтез речи)')).toBeInTheDocument();
    expect(screen.getByRole('meter', { name: 'Сегодня: доля лимита' })).toBeInTheDocument();
  });

  it('says why spend is missing when Broker does not answer', async () => {
    fakeApi({
      'GET /api/personas': { items: [] },
      'GET /api/analytics/summary': summary,
      'GET /api/analytics/spend': { available: false, error: 'usage: HTTP 404' },
    });
    await renderApp('/dashboard');
    expect(await screen.findByText(/Broker не отдал расходы: usage: HTTP 404/)).toBeInTheDocument();
  });

  it('requests a 7-day period from the preset', async () => {
    const log = fakeApi({ 'GET /api/personas': { items: [] }, 'GET /api/analytics/summary': summary });
    const user = userEvent.setup();
    await renderApp('/dashboard');
    await screen.findByRole('region', { name: 'Ключевые показатели' });

    await user.click(screen.getByRole('button', { name: '7 дней' }));
    await waitFor(() => {
      const last = log.filter((r) => r.path === '/api/analytics/summary').at(-1)!;
      const days = (new Date(last.query.get('to')!).getTime() - new Date(last.query.get('from')!).getTime()) / 86_400_000;
      expect(Math.round(days)).toBe(6);
    });
  });
});
