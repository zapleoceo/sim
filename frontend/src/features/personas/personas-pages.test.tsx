import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { EMPTY_PERSONA } from './persona-schema';
import type { Persona } from '@/lib/models';
import { fakeApi, reply } from '@/test/fake-api';
import { CSRF, renderApp, testUser } from '@/test/render-app';

const MARINA: Persona = {
  ...EMPTY_PERSONA,
  id: 1,
  name: 'Марина',
  age: 34,
  description: 'Тёплая, энергичная',
  model: 'gpt-x',
  created_at: '2026-11-01T10:00:00Z',
};

function backend(extra: Record<string, unknown> = {}) {
  return fakeApi({
    'GET /api/personas': { items: [MARINA] },
    'GET /api/strategies': { items: [] },
    'GET /api/models': { items: [{ id: 'gpt-x', name: 'GPT X', subscription: true, available: true }] },
    ...extra,
  });
}

describe('personas pages', () => {
  it('lists personas with status and model', async () => {
    backend();
    await renderApp('/personas');
    expect(await screen.findByText('Марина, 34')).toBeInTheDocument();
    expect(screen.getByText('Активен')).toBeInTheDocument();
    expect(screen.getByText('gpt-x')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Новый персонаж/ })).toHaveAttribute('href', '/personas/new');
  });

  it('sends operators away from personas', async () => {
    backend({ 'GET /api/dialogs': { items: [], total: 0 } });
    const { router } = await renderApp('/personas', { user: testUser('operator') });
    await waitFor(() => expect(router.state.location.pathname).toBe('/inbox'));
  });

  it('edits a persona: PUT with CSRF, toast, preview shown', async () => {
    const log = backend({ 'PUT /api/personas/1': (r: { body: unknown }) => ({ ...MARINA, ...(r.body as object) }) });
    await renderApp('/personas/1');
    const name = await screen.findByLabelText('Имя');
    expect(screen.getByRole('heading', { name: 'Проверить персонажа' })).toBeInTheDocument();
    // All fields are open (no «Дополнительно»); the API no longer has disclosure_text.
    expect(screen.getByLabelText('ID голоса')).toBeVisible();
    expect(screen.queryByText('Дополнительно')).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/ты бот/)).not.toBeInTheDocument();

    await userEvent.clear(name);
    await userEvent.type(name, 'Марина Светлова');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(log.some((r) => r.method === 'PUT')).toBe(true));
    const put = log.find((r) => r.method === 'PUT');
    expect(put?.headers['X-CSRF-Token']).toBe(CSRF);
    expect((put?.body as { name: string }).name).toBe('Марина Светлова');
    expect(await screen.findByText('Персонаж сохранён')).toBeInTheDocument();
  });

  it('blocks saving an empty new persona and shows field errors', async () => {
    const log = backend();
    await renderApp('/personas/new');
    await userEvent.click(await screen.findByRole('button', { name: 'Сохранить' }));
    expect(await screen.findByText('Укажите имя')).toBeInTheDocument();
    expect(screen.getByText('Опишите персонажа')).toBeInTheDocument();
    expect(log.some((r) => r.method === 'POST')).toBe(false);
    expect(screen.queryByRole('heading', { name: 'Проверить персонажа' })).not.toBeInTheDocument();
  });

  it('creates a persona and moves to its page', async () => {
    backend({ 'POST /api/personas': { ...MARINA, id: 7, name: 'Анна' } });
    const { router } = await renderApp('/personas/new');
    await userEvent.type(await screen.findByLabelText('Имя'), 'Анна');
    await userEvent.type(screen.getByLabelText('Описание персоны'), 'Спокойная');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/personas/7'));
  });

  it('says when the persona does not exist (Broker models unavailable is not fatal)', async () => {
    backend({ 'GET /api/models': reply(502) });
    await renderApp('/personas/99');
    expect(await screen.findByText('Персонаж не найден')).toBeInTheDocument();
  });

  it('deletes after confirmation', async () => {
    const log = backend({ 'DELETE /api/personas/1': reply(204) });
    const { router } = await renderApp('/personas/1');
    await userEvent.click(await screen.findByRole('button', { name: 'Удалить' }));
    await userEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Удалить' }));
    await waitFor(() => expect(log.some((r) => r.method === 'DELETE' && r.path === '/api/personas/1')).toBe(true));
    await waitFor(() => expect(router.state.location.pathname).toBe('/personas'));
  });
});
