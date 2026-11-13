import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { EMPTY_PERSONA } from './persona-schema';
import type { AppSettingItem, Persona, Strategy } from '@/lib/models';
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
  it('lists personas by name and status only: age, description and model live in the card', async () => {
    backend();
    await renderApp('/personas');
    expect(await screen.findByText('Марина')).toBeInTheDocument();
    expect(screen.getByText('Активен')).toBeInTheDocument();
    expect(screen.queryByText(/34/)).not.toBeInTheDocument();
    expect(screen.queryByText('Тёплая, энергичная')).not.toBeInTheDocument();
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
    // All fields are open (no «Дополнительно»); the API no longer has disclosure_text.
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

  it('blocks saving an empty new persona and shows field errors', async () => {
    const log = backend();
    await renderApp('/personas/new');
    await userEvent.click(await screen.findByRole('button', { name: 'Сохранить персонажа' }));
    expect(await screen.findByText('Укажите имя')).toBeInTheDocument();
    expect(screen.getByText('Опишите персонажа')).toBeInTheDocument();
    expect(log.some((r) => r.method === 'POST')).toBe(false);
    expect(screen.queryByRole('heading', { name: 'Проверить персонажа' })).not.toBeInTheDocument();
  });

  it('creates a persona and moves to its page', async () => {
    backend({ 'POST /api/personas': { ...MARINA, id: 7, name: 'Анна' } });
    const { router } = await renderApp('/personas/new');
    await userEvent.type(await screen.findByLabelText('Имя'), 'Анна');
    await userEvent.type(screen.getByLabelText('Промпт персонажа'), 'Спокойная');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить персонажа' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/personas/7'));
  });

  it('shows every field of the persona and saves gender, language and prohibitions with the rest', async () => {
    const log = backend({ 'PUT /api/personas/1': (r: { body: unknown }) => ({ ...MARINA, ...(r.body as object) }) });
    await renderApp('/personas/1');
    const profile = within(await screen.findByRole('region', { name: 'Профиль' }));
    expect(profile.getByText('Пол')).toBeVisible();
    expect(profile.getByLabelText('Часовой пояс')).toHaveValue('Europe/Kyiv');
    await userEvent.clear(profile.getByLabelText('Язык'));
    await userEvent.type(profile.getByLabelText('Язык'), 'uk');
    await userEvent.type(screen.getByLabelText('Запреты'), 'не обещать доход');
    expect(screen.getByLabelText('Распорядок недели')).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить персонажа' }));
    await waitFor(() => expect(log.some((r) => r.method === 'PUT')).toBe(true));
    expect(log.find((r) => r.method === 'PUT')?.body).toMatchObject({ language: 'uk', prohibitions: 'не обещать доход', gender: 'female' });
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
    expect(screen.getByRole('navigation', { name: 'Разделы персонажа' })).toHaveTextContent('Итоговый промпт');
  });

  it('shows the assembled prompt piece by piece: whose text it is and where to change it', async () => {
    const part = (key: string, source: string, field: string) => ({ key, title: `Кусок ${key}`, text: `текст ${key}`, source, field });
    backend({
      'GET /api/personas/1/prompt': {
        system: 'текст description\n\nтекст strategy', fixed: '', strategy_id: 3, context: '- Тебе 45',
        parts: [
          part('description', 'persona', 'description'), part('strategy', 'strategy', 'offer'),
          part('strategy_prompt', 'strategy', 'prompt'), part('output_contract', 'setting', 'output_contract'),
        ],
        rules: [
          { key: 'call', title: 'Правило звонков', text: 'ты ИИ-ассистент' },
          { key: 'video_note', title: 'Правило кружочков', text: 'Сгенерировано ИИ' },
        ],
      },
    });
    await renderApp('/personas/1');
    const section = within(await screen.findByRole('region', { name: 'Итоговый промпт' }));
    const piece = async (title: string) => within(await section.findByRole('listitem', { name: title }));

    const own = await piece('Кусок description');
    expect(own.getByText('текст description')).toBeInTheDocument();
    expect(own.getByText('Поле персонажа')).toBeInTheDocument();
    expect(own.getByRole('link', { name: 'Изменить' })).toHaveAttribute('href', '#persona-description');
    expect((await piece('Кусок strategy')).getByRole('link', { name: 'Изменить в стратегии' })).toHaveAttribute('href', '/strategies/3');
    expect((await piece('Кусок strategy_prompt')).getByRole('link', { name: 'Изменить' })).toHaveAttribute('href', '#strategy-prompt');
    const contract = await piece('Кусок output_contract');
    expect(contract.getByText('Общая настройка')).toBeInTheDocument();
    expect(contract.getByRole('link', { name: 'Изменить' })).toHaveAttribute('href', '#setting-output_contract');
    const context = await piece('Контекст разговора');
    expect(context.getByText('- Тебе 45')).toBeInTheDocument();
    expect(context.getByRole('link', { name: 'Дата рождения' })).toHaveAttribute('href', '#persona-birth_date');

    // the two rules that stay in the code: read-only, no field
    const fixed = within(section.getByRole('group', { name: 'Фиксированные правила' }));
    expect(fixed.getByText('Правило звонков')).toBeInTheDocument();
    expect(fixed.getByText('ты ИИ-ассистент')).toBeInTheDocument();
    expect(fixed.getByText('Правило кружочков')).toBeInTheDocument();
    expect(fixed.getAllByText(/не редактируется/)).toHaveLength(2);
    expect(fixed.queryByRole('textbox')).not.toBeInTheDocument();

    expect(section.queryByLabelText('Полный промпт')).not.toBeInTheDocument();
    await userEvent.click(section.getByRole('button', { name: /одним текстом/ }));
    expect(section.getByLabelText('Полный промпт')).toHaveTextContent('текст description текст strategy');
  });

  it('edits the shared instructions in place and refreshes the assembled prompt', async () => {
    const CONTRACT: AppSettingItem = {
      key: 'output_contract', group: 'Служебные промпты', label: 'Формат ответа персонажа', help: '', kind: 'text',
      min: null, max: null, value: 'JSON', default: 'JSON', is_default: true, updated_at: null, updated_by_id: null,
    };
    const log = backend({
      'GET /api/settings': { items: [CONTRACT], fixed: { persona: '', guard: 'контракт охранника' } },
      'PUT /api/settings/output_contract': (r: { body: unknown }) => ({ ...CONTRACT, ...(r.body as object), is_default: false }),
      'GET /api/personas/1/prompt': { system: '', fixed: '', strategy_id: 3, context: '', parts: [], rules: [] },
    });
    await renderApp('/personas/1');
    const shared = within(await screen.findByRole('region', { name: 'Общие инструкции' }));
    const field = await shared.findByLabelText('Формат ответа персонажа');
    expect(field.closest('#setting-output_contract')).not.toBeNull();
    expect(await shared.findByText('контракт охранника')).toBeInTheDocument();
    const loaded = (): number => log.filter((r) => r.path === '/api/personas/1/prompt').length;
    await waitFor(() => expect(loaded()).toBe(1));

    await userEvent.type(field, ' строго');
    await userEvent.click(within(shared.getByRole('group', { name: 'Ответ персонажа' })).getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(log.find((r) => r.method === 'PUT')?.body).toEqual({ value: 'JSON строго' }));
    await waitFor(() => expect(loaded()).toBe(2));
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
    const scenario = await screen.findByLabelText('Сценарий стратегии «RCIPM»');
    expect(scenario).toHaveValue('Сначала человек.');
    const saveScenario = screen.getByRole('button', { name: 'Сохранить сценарий' });
    expect(saveScenario).toBeDisabled();
    await userEvent.type(scenario, ' Потом оффер.');
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
