import userEvent from '@testing-library/user-event';
import { waitFor } from '@testing-library/react';
import type { ClientCard } from '@/lib/models';
import { fakeApi, reply } from '@/test/fake-api';
import { renderApp, screen } from '@/test/render-app';

const card: ClientCard = {
  id: 7,
  display_name: 'Анна Клиентова',
  avatar_url: '',
  persona_id: 1,
  stage: 'engaged',
  status: 'active',
  next_step: '',
  next_step_at: null,
  notes: 'Любит короткие ответы',
  needs: ['доход'],
  channels: [{ kind: 'telegram', dialog_id: 1 }],
  last_contact_at: null,
  created_at: '2026-11-01T10:00:00Z',
  events: [],
  partner_events: [],
};

const common = {
  'GET /api/clients/7/messages': { items: [], total: 0, limit: 25, offset: 0 },
  'GET /api/channels': { items: [] },
  'GET /api/personas': { items: [] },
};

describe('ClientPage', () => {
  it('shows «not found» for a missing client', async () => {
    fakeApi({ ...common, 'GET /api/clients/99': reply(404, { detail: 'not found' }) });
    await renderApp('/clients/99');
    expect(await screen.findByText('Клиент не найден')).toBeInTheDocument();
  });

  it('saves notes only after an edit and moves the stage', async () => {
    const log = fakeApi({
      ...common,
      'GET /api/clients/7': card,
      'PATCH /api/clients/7': (req) => ({ ...card, ...(req.body as object) }),
      'POST /api/clients/7/stage': { ...card, stage: 'interested' },
    });
    await renderApp('/clients/7');
    const save = await screen.findByRole('button', { name: 'Сохранить' });
    expect(save).toBeDisabled();
    expect(screen.getByText('доход')).toBeInTheDocument();

    const notes = screen.getByLabelText('Заметки');
    await userEvent.clear(notes);
    await userEvent.type(notes, 'Перезвонить');
    expect(save).toBeEnabled();
    await userEvent.click(save);
    await waitFor(() =>
      expect(log.find((r) => r.method === 'PATCH')?.body).toMatchObject({ notes: 'Перезвонить', next_step_at: null }),
    );

    await userEvent.click(screen.getByRole('button', { name: /Интерес/ }));
    await waitFor(() => expect(log.find((r) => r.method === 'POST' && r.path === '/api/clients/7/stage')?.body).toEqual({ stage: 'interested' }));
  });

  it('does not call the API for a partner-only stage', async () => {
    const log = fakeApi({ ...common, 'GET /api/clients/7': card });
    await renderApp('/clients/7');
    await userEvent.click(await screen.findByRole('button', { name: /Оплата/ }));
    expect(log.some((r) => r.path === '/api/clients/7/stage')).toBe(false);
  });
});
