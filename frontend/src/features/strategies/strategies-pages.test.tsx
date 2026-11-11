import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Strategy } from '@/lib/models';
import { fakeApi } from '@/test/fake-api';
import { renderApp } from '@/test/render-app';

const RCIPM: Strategy = {
  id: 2, name: 'RCIPM', goal: 'Регистрация', prompt: '', offer: '', partner_name: 'Partner X', registration_url: '',
  stage_playbook: { legacy: 'keep' }, rules: ['не давить'], followup_hours: [24, 72], created_at: '',
};

describe('strategies pages', () => {
  it('lists strategies with the partner', async () => {
    fakeApi({ 'GET /api/strategies': { items: [RCIPM] } });
    await renderApp('/strategies');
    expect(await screen.findByText('RCIPM')).toBeInTheDocument();
    expect(screen.getByText('Partner X')).toBeInTheDocument();
  });

  it('shows three blocks and saves the scenario text and reminder days', async () => {
    const log = fakeApi({
      'GET /api/strategies': { items: [RCIPM] },
      'PUT /api/strategies/2': (r: { body: unknown }) => ({ ...RCIPM, ...(r.body as object) }),
    });
    await renderApp('/strategies/2');
    for (const block of ['Что продаём', 'Как вести разговор', 'Напоминания']) {
      expect(await screen.findByRole('region', { name: block })).toBeInTheDocument();
    }
    expect(screen.queryByRole('button', { name: 'Добавить правило' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('Через сколько дней')).toHaveValue('1, 3');

    await userEvent.type(screen.getByLabelText('Сценарий'), 'Сначала человек');
    await userEvent.clear(screen.getByLabelText('Через сколько дней'));
    await userEvent.type(screen.getByLabelText('Через сколько дней'), '1, 2, 5');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(log.some((r) => r.method === 'PUT')).toBe(true));
    const body = log.find((r) => r.method === 'PUT')?.body as Strategy;
    expect(body.prompt).toBe('Сначала человек');
    expect(body.followup_hours).toEqual([24, 48, 120]);
    expect(body.rules).toEqual(['не давить']);                 // passed through, not edited here
    expect(body.stage_playbook['legacy']).toBe('keep');
    expect(await screen.findByText('Стратегия сохранена')).toBeInTheDocument();
  });

  it('says when the strategy does not exist', async () => {
    fakeApi({ 'GET /api/strategies': { items: [] } });
    await renderApp('/strategies/5');
    expect(await screen.findByText('Стратегия не найдена')).toBeInTheDocument();
  });
});
