import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { EMPTY_PERSONA } from './persona-schema';
import type { Persona } from '@/lib/models';
import { fakeApi, reply, type FakeReply } from '@/test/fake-api';
import { renderApp } from '@/test/render-app';

const MARINA: Persona = {
  ...EMPTY_PERSONA,
  id: 1,
  name: 'Марина',
  description: 'Тёплая',
  created_at: '2026-11-01T10:00:00Z',
};

function backend(videoNote: FakeReply) {
  return fakeApi({
    'GET /api/personas': { items: [MARINA] },
    'GET /api/strategies': { items: [] },
    'GET /api/models': { items: [] },
    'GET /api/personas/1/media': { items: [] },
    'GET /api/personas/1/prompt': { system: 'test', fixed: '' },
    'POST /api/personas/1/video-note': videoNote,
  });
}

describe('AI video note', () => {
  it('lets the owner request a note and posts the prompt', async () => {
    const log = backend(reply(202, { status: 'queued', cached: false, cache_key: 'k', dialog_id: 5 }));
    await renderApp('/personas/1');
    expect(await screen.findByText('Кружочек ИИ')).toBeInTheDocument();
    const button = screen.getByRole('button', { name: 'Создать и отправить' });
    expect(button).toBeDisabled();

    await userEvent.type(screen.getByLabelText('Описание кружочка'), '  Марина машет рукой ');
    expect(button).toBeEnabled();
    await userEvent.click(button);

    await waitFor(() => {
      const sent = log.find((r) => r.method === 'POST' && r.path === '/api/personas/1/video-note');
      expect(sent?.body).toEqual({ prompt: 'Марина машет рукой' });
    });
  });

  it('says when the same note comes from the cache', async () => {
    backend(reply(202, { status: 'queued', cached: true, cache_key: 'k', dialog_id: 5 }));
    await renderApp('/personas/1');
    await userEvent.type(await screen.findByLabelText('Описание кружочка'), 'Марина машет рукой');
    await userEvent.click(screen.getByRole('button', { name: 'Создать и отправить' }));
    expect(await screen.findByText('Такой кружочек уже есть: отправляется из кеша')).toBeInTheDocument();
  });
});
