import { vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { EMPTY_PERSONA } from './persona-schema';
import type { Persona, Strategy } from '@/lib/models';
import { fakeApi, reply } from '@/test/fake-api';
import { blockButton, openLongText, toggleBlock, typeLongText } from '@/test/persona-blocks';
import { CSRF, renderApp, testUser } from '@/test/render-app';

const MARINA: Persona = {
  ...EMPTY_PERSONA,
  id: 1,
  name: 'Марина',
  agent_prompt: 'Ты Марина: тёплая, энергичная.',
  model: 'gpt-x',
  created_at: '2026-11-01T10:00:00Z',
};

function backend(extra: Record<string, unknown> = {}) {
  return fakeApi({
    'GET /api/personas': { items: [MARINA] },
    'GET /api/strategies': { items: [] },
    'GET /api/models': { items: [{ id: 'gpt-x', name: 'GPT X', subscription: true, available: true }] },
    'GET /api/settings': { items: [], fixed: { persona: '', guard: '' } },
    ...extra,
  });
}

describe('personas pages', () => {
  it('shows independent route choices with honest disclosure and reserve limits', async () => {
    backend();
    await renderApp('/personas/1');
    await screen.findByLabelText('Имя');
    await toggleBlock('Модель и стратегия');
    expect(screen.getByText('Вопросы об ИИ')).toBeVisible();
    expect(screen.getByText('Подписочный резерв')).toBeVisible();
    expect(screen.getByText(/Правдивое раскрытие ИИ и контролёр действуют/)).toBeVisible();
    expect(screen.getByText(/Не используется при ожидании, неизвестном исходе/)).toBeVisible();
  });
  it('lists personas by name and status only: the prompt and the model live in the card', async () => {
    backend();
    await renderApp('/personas');
    expect(await screen.findByText('Марина')).toBeInTheDocument();
    expect(screen.getByText('Активен')).toBeInTheDocument();
    expect(screen.queryByText(MARINA.agent_prompt)).not.toBeInTheDocument();
    expect(screen.queryByText('gpt-x')).not.toBeInTheDocument();
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
    // Every field is on the page, in its block (no «Дополнительно»); the API no longer has disclosure_text.
    expect(screen.getByText('Голосовые ответы')).not.toBeVisible();
    await toggleBlock('Голос и звонки');
    expect(screen.getByText('Голосовые ответы')).toBeVisible();
    expect(screen.getByText('Голос (Google Gemini)')).toBeVisible();
    expect(screen.queryByText('Дополнительно')).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/ты бот/)).not.toBeInTheDocument();

    await userEvent.clear(name);
    await userEvent.type(name, 'Марина Светлова');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить персонажа' }));

    await waitFor(() => expect(log.some((r) => r.method === 'PUT')).toBe(true));
    const put = log.find((r) => r.method === 'PUT');
    expect(put?.headers['X-CSRF-Token']).toBe(CSRF);
    expect((put?.body as { name: string }).name).toBe('Марина Светлова');
    expect(await screen.findByText('Персонаж сохранён')).toBeInTheDocument();
  });

  it('uses only the queued preview after saving the persona', async () => {
    const persona = { ...MARINA, preview_async_enabled: true };
    const fixed = '1795171200000-9d80c89d-438e-4eef-8848-7bfb39a89af3';
    const path = `/api/personas/1/preview-operations/${fixed}`;
    const log = backend({
      'GET /api/personas': { items: [persona] },
      'PUT /api/personas/1': persona,
      [`POST ${path}`]: reply(202, { id: fixed }),
      [`GET ${path}`]: { status: 'complete', history: [], message: 'Привет',
        result: { reply: 'Ответ из очереди', model: 'fake', latency_ms: 42 } },
    });
    const clock = vi.spyOn(Date, 'now').mockReturnValue(1795171200000);
    const uuid = vi.spyOn(crypto, 'randomUUID').mockReturnValue('9d80c89d-438e-4eef-8848-7bfb39a89af3');
    try {
      await renderApp('/personas/1');
      await userEvent.click(await screen.findByRole('button', { name: 'Сохранить персонажа' }));
      expect((await screen.findAllByText('Персонаж сохранён')).length).toBeGreaterThan(0);
      const mode = screen.getByRole('combobox', { name: 'Режим предпросмотра' });
      expect(mode).toHaveValue('voice');
      await userEvent.selectOptions(mode, 'queued');
      await userEvent.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'Привет{enter}');
      expect(await screen.findByText('Ответ из очереди')).toBeInTheDocument();
      expect(log.filter((entry) => entry.method === 'POST').map((entry) => entry.path)).toEqual([path]);
    } finally {
      clock.mockRestore();
      uuid.mockRestore();
    }
  });

  it('blocks saving an empty new persona and shows field errors', async () => {
    const log = backend();
    await renderApp('/personas/new');
    await userEvent.click(await screen.findByRole('button', { name: 'Сохранить персонажа' }));
    expect(await screen.findByText('Укажите имя')).toBeVisible();
    expect(screen.getByText('Напишите промпт')).toBeVisible();
    expect(log.some((r) => r.method === 'POST')).toBe(false);
    expect(screen.queryByRole('heading', { name: 'Проверить персонажа' })).not.toBeInTheDocument();
  });

  it('creates a persona and moves to its page', async () => {
    backend({ 'POST /api/personas': { ...MARINA, id: 7, name: 'Анна' } });
    const { router } = await renderApp('/personas/new');
    await userEvent.type(await screen.findByLabelText('Имя'), 'Анна');
    await userEvent.type(screen.getByRole('textbox', { name: 'Промпт' }), 'Ты Анна.');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить персонажа' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/personas/7'));
  });

  it('shows every field of the persona and saves gender, language with the rest', async () => {
    const log = backend({ 'PUT /api/personas/1': (r: { body: unknown }) => ({ ...MARINA, ...(r.body as object) }) });
    await renderApp('/personas/1');
    const profile = within(await screen.findByRole('region', { name: 'Профиль' }));
    expect(profile.getByText('Пол')).toBeVisible();
    expect(profile.getByLabelText('Часовой пояс')).toHaveValue('Europe/Kyiv');
    await toggleBlock('Голос и звонки');
    await userEvent.clear(screen.getByLabelText('Язык'));
    await userEvent.type(screen.getByLabelText('Язык'), 'uk');
    await toggleBlock('Распорядок');
    expect(screen.getByRole('button', { name: 'Редактировать: Распорядок недели' })).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить персонажа' }));
    await waitFor(() => expect(log.some((r) => r.method === 'PUT')).toBe(true));
    expect(log.find((r) => r.method === 'PUT')?.body).toMatchObject({ language: 'uk', gender: 'female' });
  });

  it('counts the age from the birth date', async () => {
    const birth = new Date();
    birth.setFullYear(birth.getFullYear() - 40);
    birth.setDate(birth.getDate() - 2);
    const iso = `${birth.getFullYear()}-${String(birth.getMonth() + 1).padStart(2, '0')}-${String(birth.getDate()).padStart(2, '0')}`;
    backend({ 'GET /api/personas': { items: [{ ...MARINA, birth_date: iso }] } });
    await renderApp('/personas/1');
    expect(await screen.findByText(/Сейчас ей 40\./)).toBeInTheDocument();
  });

  it('opens without waiting for the Broker models, the strategies or the settings', async () => {
    const never = (): Promise<never> => new Promise(() => undefined);
    backend({ 'GET /api/models': never, 'GET /api/strategies': never, 'GET /api/settings': never });
    await renderApp('/personas/1');
    expect(await screen.findByLabelText('Имя')).toHaveValue('Марина');
    expect(await blockButton('Фото и кружочки')).toHaveAttribute('aria-expanded', 'false');
  });

  it('says where the settings of her photos and video notes live, without a shared-instructions block', async () => {
    backend({ 'GET /api/personas/1/media': { items: [] } });
    await renderApp('/personas/1');
    await toggleBlock('Фото и кружочки');
    const media = within(screen.getByRole('region', { name: 'Фото и кружочки' }));
    expect(media.getByText(/в «Общих правилах»/)).toBeVisible();
    expect(document.querySelector('a[href="#shared"]')).toBeNull();
    expect(screen.getByText(/Идёт в описание кадра для её фото и кружочков/)).toBeInTheDocument();
  });

  it('edits the scenario of the persona strategy in place', async () => {
    const STRATEGY: Strategy = {
      id: 3, name: 'RCIPM', goal: '', prompt: 'Сначала человек.', offer: 'курс', partner_name: '', registration_url: '',
      stage_playbook: {}, rules: [], followup_hours: [24], created_at: '2026-11-01T10:00:00Z',
    };
    const log = backend({
      'GET /api/personas': { items: [{ ...MARINA, strategy_id: 3 }] },
      'GET /api/strategies': { items: [STRATEGY] },
      'PUT /api/strategies/3': (r: { body: unknown }) => ({ ...STRATEGY, ...(r.body as object) }),
    });
    await renderApp('/personas/1');
    await toggleBlock('Модель и стратегия');
    const saveScenario = await screen.findByRole('button', { name: 'Сохранить сценарий' });
    expect(saveScenario).toBeDisabled();
    expect(await openLongText('Сценарий стратегии «RCIPM»')).toHaveValue('Сначала человек.');
    await userEvent.keyboard('{Escape}');
    await typeLongText('Сценарий стратегии «RCIPM»', ' Потом оффер.');
    await userEvent.click(saveScenario);
    await waitFor(() => expect(log.find((r) => r.method === 'PUT')?.body).toEqual({ name: 'RCIPM', prompt: 'Сначала человек. Потом оффер.' }));
    expect(log.some((r) => r.method === 'PUT' && r.path === '/api/personas/1')).toBe(false);   // the persona form was not submitted
    expect(screen.getByRole('link', { name: /в карточке стратегии/ })).toHaveAttribute('href', '/strategies/3');
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
