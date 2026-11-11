import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Strategy } from '@/lib/models';
import { fakeApi } from '@/test/fake-api';
import { renderApp } from '@/test/render-app';

const RCIPM: Strategy = {
  id: 2, name: 'RCIPM', goal: 'Регистрация', prompt: '', offer: '', partner_name: 'Partner X', registration_url: '',
  stage_playbook: { legacy: 'keep' }, rules: ['не давить'], followup_hours: [24, 72], reply_delay_s: [20, 90], created_at: '',
};

describe('strategies pages', () => {
  it('lists strategies with the partner', async () => {
    fakeApi({ 'GET /api/strategies': { items: [RCIPM] } });
    await renderApp('/strategies');
    expect(await screen.findByText('RCIPM')).toBeInTheDocument();
    expect(screen.getByText('Partner X')).toBeInTheDocument();
  });

  it('edits rules and saves the payload', async () => {
    const log = fakeApi({
      'GET /api/strategies': { items: [RCIPM] },
      'PUT /api/strategies/2': (r: { body: unknown }) => ({ ...RCIPM, ...(r.body as object) }),
    });
    await renderApp('/strategies/2');
    expect(await screen.findByDisplayValue('не давить')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Добавить правило' }));
    await userEvent.type(screen.getByLabelText('Правило 2'), 'быть вежливой');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(log.some((r) => r.method === 'PUT')).toBe(true));
    const body = log.find((r) => r.method === 'PUT')?.body as Strategy;
    expect(body.rules).toEqual(['не давить', 'быть вежливой']);
    expect(body.stage_playbook['legacy']).toBe('keep');
    expect(body.followup_hours).toEqual([24, 72]);
    expect(await screen.findByText('Стратегия сохранена')).toBeInTheDocument();
  });

  it('shows the delay range error and does not save', async () => {
    const log = fakeApi({ 'GET /api/strategies': { items: [RCIPM] } });
    await renderApp('/strategies/2');
    const min = await screen.findByLabelText('Задержка ответа от, с');
    await userEvent.clear(min);
    await userEvent.type(min, '500');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    expect(await screen.findByText('Минимальная задержка не может быть больше максимальной.')).toBeInTheDocument();
    expect(log.some((r) => r.method === 'PUT')).toBe(false);
  });

  it('says when the strategy does not exist', async () => {
    fakeApi({ 'GET /api/strategies': { items: [] } });
    await renderApp('/strategies/5');
    expect(await screen.findByText('Стратегия не найдена')).toBeInTheDocument();
  });
});
