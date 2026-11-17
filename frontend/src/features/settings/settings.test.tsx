import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { qk } from '@/lib/query';
import type { AppSettingItem } from '@/lib/models';
import { fakeApi, reply } from '@/test/fake-api';
import { CSRF, renderApp, testUser } from '@/test/render-app';
import { groupSettings, parseSettingValue, pickSettings, replaceSetting, settingFieldKey, settingHint } from './settings-logic';

function item(patch: Partial<AppSettingItem>): AppSettingItem {
  return {
    key: 'k', group: 'Промпт', label: 'Поле', help: '', kind: 'text', min: null, max: null, value: 'v', default: 'v',
    is_default: true, updated_at: null, updated_by_id: null, ...patch,
  };
}

const STYLE = item({ key: 'style', group: 'Стиль', label: 'Манера письма', value: 'коротко', default: 'коротко' });
const GUARD = item({
  key: 'guard_prompt', group: 'Охранник', label: 'Промпт охранника', value: 'свой', default: 'исходный', is_default: false,
  updated_at: '2026-11-11T10:00:00Z',
});
const DELAY = item({ key: 'delay', group: 'Стиль', label: 'Пауза, с', kind: 'int', min: 0, max: 600, value: 30, default: 30 });

describe('settings logic', () => {
  it('groups in API order', () => {
    expect(groupSettings([STYLE, GUARD, DELAY]).map((g) => [g.name, g.items.map((i) => i.key)])).toEqual([
      ['Стиль', ['style', 'delay']],
      ['Охранник', ['guard_prompt']],
    ]);
  });

  it('picks only the given keys in the given order', () => {
    expect(pickSettings([STYLE, GUARD, DELAY], ['delay', 'missing', 'style']).map((i) => i.key)).toEqual(['delay', 'style']);
    expect(pickSettings([STYLE, DELAY]).map((i) => i.key)).toEqual(['style', 'delay']);
  });

  it('parses integers within range and keeps text as is', () => {
    expect(parseSettingValue(DELAY, ' 45 ')).toEqual({ ok: true, value: 45 });
    expect(parseSettingValue(DELAY, '4.5')).toEqual({ ok: false, error: 'Нужно целое число' });
    expect(parseSettingValue(DELAY, '')).toEqual({ ok: false, error: 'Нужно целое число' });
    expect(parseSettingValue(DELAY, '601')).toEqual({ ok: false, error: 'Не больше 600' });
    expect(parseSettingValue(DELAY, '-1')).toEqual({ ok: false, error: 'Не меньше 0' });
    expect(parseSettingValue(STYLE, ' пробелы ')).toEqual({ ok: true, value: ' пробелы ' });
  });

  it('accepts a list of numeric Telegram ids, also an empty one', () => {
    const IDS = item({ key: 'instant_reply_telegram_ids', kind: 'ids', value: '', default: '' });
    expect(parseSettingValue(IDS, '169510539, 42')).toEqual({ ok: true, value: '169510539, 42' });
    expect(parseSettingValue(IDS, '')).toEqual({ ok: true, value: '' });
    expect(parseSettingValue(IDS, '@dima')).toEqual({ ok: false, error: 'Только числовые Telegram id через запятую' });
  });

  it('hint shows the range and replace keeps order', () => {
    expect(settingHint({ ...DELAY, help: 'Задержка' })).toBe('Задержка (0–600)');
    expect(settingHint(STYLE)).toBe('');
    expect(replaceSetting([STYLE, DELAY], { ...STYLE, value: 'x' }).map((i) => i.value)).toEqual(['x', 30]);
    expect(settingFieldKey(GUARD)).toBe('guard_prompt');
    expect(settingFieldKey(STYLE)).toBe('style');
  });
});

async function edit(label: string) {
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: 'Изменить: ' + label }));
  return screen.getByLabelText(label);
}

describe('SettingsPage', () => {
  it('keeps a pending save locked across navigation until its receipt', async () => {
    let finish!: (value: AppSettingItem) => void;
    const response = new Promise<AppSettingItem>((resolve) => { finish = resolve; });
    const log = fakeApi({ 'GET /api/settings': { items: [STYLE, GUARD] }, 'PUT /api/settings/style': () => response });
    const app = await renderApp('/settings');
    const input = await edit('Манера письма');
    const user = userEvent.setup();
    await user.type(input, ' ещё');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));
    await act(() => app.router.navigate({ to: '/agents' }));
    await screen.findByRole('heading', { name: 'Помощники' });
    await act(() => app.router.history.back());
    expect(await screen.findByLabelText('Манера письма')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
    expect(log.filter((r) => r.method === 'PUT')).toHaveLength(1);
    await act(async () => { finish({ ...STYLE, value: 'коротко ещё' }); await response; });
    await waitFor(() => expect(screen.queryByText('Не сохранено')).not.toBeInTheDocument());
    expect(await edit('Манера письма')).toHaveValue('коротко ещё');
  });

  it('opens instructions intentionally, saves with CSRF, then hides clean save actions', async () => {
    const log = fakeApi({ 'GET /api/settings': { items: [STYLE, GUARD, DELAY] },
      'PUT /api/settings/style': (r) => ({ ...STYLE, value: (r.body as { value: string }).value, is_default: false }) });
    await renderApp('/settings');
    expect(await screen.findByRole('button', { name: 'Изменить: Манера письма' })).toBeInTheDocument();
    expect(screen.queryByLabelText('Манера письма')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Сохранить' })).not.toBeInTheDocument();
    const input = await edit('Манера письма');
    const user = userEvent.setup();
    await user.clear(input); await user.type(input, 'очень коротко');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(screen.queryByText('Не сохранено')).not.toBeInTheDocument());
    expect(log.find((r) => r.method === 'PUT')?.body).toEqual({ value: 'очень коротко' });
    expect(log.find((r) => r.method === 'PUT')?.headers['X-CSRF-Token']).toBe(CSRF);
    await user.click(screen.getByRole('button', { name: 'Свернуть' }));
    expect(await edit('Манера письма')).toHaveValue('очень коротко');
    expect(screen.queryByLabelText('Промпт охранника')).not.toBeInTheDocument();
  });

  it('reset requires confirmation and cancel leaves the draft untouched', async () => {
    const log = fakeApi({ 'GET /api/settings': { items: [GUARD] },
      'DELETE /api/settings/guard_prompt': { ...GUARD, value: 'исходный', is_default: true } });
    await renderApp('/agents');
    const input = await edit('Промпт охранника');
    const user = userEvent.setup();
    await user.clear(input); await user.type(input, 'черновик');
    await user.click(screen.getByRole('button', { name: 'Вернуть по умолчанию' }));
    expect(log.some((r) => r.method === 'DELETE')).toBe(false);
    await user.click(screen.getByRole('button', { name: 'Отмена' }));
    expect(input).toHaveValue('черновик');
    await user.click(screen.getByRole('button', { name: 'Вернуть по умолчанию' }));
    await user.click(screen.getByRole('button', { name: 'Сбросить настройку' }));
    await waitFor(() => expect(input).toHaveValue('исходный'));
    expect(log.filter((r) => r.method === 'DELETE')).toHaveLength(1);
  });

  it('shows helper roles, current model and read-only fixed rules without capability controls', async () => {
    const model = item({ key: 'guard_model', group: 'Охранник', label: 'Модель', kind: 'model', value: 'synthetic', default: '' });
    const hidden = item({ key: 'guard_capability', group: 'Охранник', label: 'Маршрут Broker' });
    const passport = item({ key: 'passport_prompt', group: 'Паспортистка', label: 'Инструкция паспортистки' });
    const summary = item({ key: 'summary_prompt', group: 'Саммари диалога', label: 'Инструкция саммари' });
    fakeApi({ 'GET /api/settings': { items: [STYLE, GUARD, model, hidden, passport, summary], fixed: { guard: 'Фиксированная честность', persona: '' } },
      'GET /api/models': { items: [{ id: 'synthetic', name: 'Synthetic model', available: true }] } });
    await renderApp('/agents');
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Модель' })).toHaveTextContent('Synthetic model'));
    expect(screen.getByText(/Проверяет ответ перед отправкой/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Изменить: Инструкция паспортистки' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Изменить: Инструкция саммари' })).toBeInTheDocument();
    expect(screen.queryByLabelText('Маршрут Broker')).not.toBeInTheDocument();
    expect(screen.queryByText('Манера письма')).not.toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByText('Обязательные правила проверки · только чтение'));
    expect(screen.getByText('Фиксированная честность')).toBeVisible();
    expect(screen.getByText(/не редактируется/)).toBeInTheDocument();
  });

  it('keeps drafts over route changes, Back/Forward and server refresh, confirms overwrite', async () => {
    const log = fakeApi({ 'GET /api/settings': { items: [STYLE, GUARD] },
      'PUT /api/settings/style': (r) => ({ ...STYLE, value: (r.body as { value: string }).value }) });
    const app = await renderApp('/settings');
    const input = await edit('Манера письма');
    const user = userEvent.setup();
    await user.clear(input); await user.type(input, 'мой черновик');
    await act(() => app.router.navigate({ to: '/agents' }));
    await screen.findByRole('heading', { name: 'Помощники' });
    await act(() => app.router.history.back());
    expect(await screen.findByLabelText('Манера письма')).toHaveValue('мой черновик');
    await act(() => app.router.history.forward());
    await screen.findByRole('heading', { name: 'Помощники' });
    await act(() => app.router.history.back());
    await screen.findByLabelText('Манера письма');
    act(() => { app.router.options.context!.queryClient.setQueryData(qk.settings, [{ ...STYLE, value: 'другой текст', updated_at: '2026-11-17T00:00:00Z' }, GUARD]); });
    expect(screen.getByLabelText('Манера письма')).toHaveValue('мой черновик');
    expect(await screen.findByText(/На сервере появилось другое значение/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));
    expect(log.some((r) => r.method === 'PUT')).toBe(false);
    await user.click(screen.getByRole('button', { name: 'Отмена' }));
    await user.click(screen.getByRole('button', { name: 'Загрузить сохранённое' }));
    expect(await edit('Манера письма')).toHaveValue('другой текст');
  });

  it('deep links open the editor and hash navigation preserves edits', async () => {
    fakeApi({ 'GET /api/settings': { items: [STYLE, item({ key: 'other', group: 'Другое', label: 'Другое поле' })] } });
    const app = await renderApp('/settings#setting-style');
    const input = await screen.findByLabelText('Манера письма');
    const user = userEvent.setup();
    await user.type(input, ' ещё');
    await user.selectOptions(screen.getByLabelText('Раздел настроек'), 'group-другое');
    await waitFor(() => expect(app.router.state.location.hash).toBe('group-другое'));
    expect(input).toHaveValue('коротко ещё');
    expect(document.getElementById('setting-style')).toContainElement(input);
  });

  it('retains failed saves and warns on closing the tab, with explicit discard', async () => {
    fakeApi({ 'GET /api/settings': { items: [STYLE] }, 'PUT /api/settings/style': reply(500) });
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    await renderApp('/settings');
    const input = await edit('Манера письма');
    const user = userEvent.setup();
    await user.type(input, ' ещё');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));
    expect(await screen.findByText(/Не удалось сохранить/)).toBeInTheDocument();
    expect(input).toHaveValue('коротко ещё');
    const before = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(before); expect(before.defaultPrevented).toBe(true);
    await user.click(screen.getByRole('button', { name: 'Отменить правки' }));
    expect(input).toHaveValue('коротко');
  });

  it('validates integers without calling the API', async () => {
    const log = fakeApi({ 'GET /api/settings': { items: [DELAY] } });
    await renderApp('/settings');
    const user = userEvent.setup();
    const input = await screen.findByLabelText('Пауза, с');
    await user.clear(input); await user.type(input, 'abc');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));
    expect(await screen.findByText('Нужно целое число')).toBeInTheDocument();
    expect(log.some((r) => r.method === 'PUT')).toBe(false);
  });

  it('keeps the old guard link and owner permissions', async () => {
    fakeApi({ 'GET /api/settings': { items: [GUARD] }, 'GET /api/dialogs': { items: [], total: 0 }, 'GET /api/personas': { items: [] } });
    const owner = await renderApp('/guard');
    expect(await screen.findByRole('heading', { name: 'Помощники' })).toBeInTheDocument();
    owner.unmount();
    const operator = await renderApp('/settings', { user: testUser('operator') });
    await waitFor(() => expect(operator.router.state.location.pathname).toBe('/inbox'));
  });

  it('offers retry on failure and a clear empty state', async () => {
    fakeApi({ 'GET /api/settings': reply(500) });
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const failed = await renderApp('/settings');
    expect(await screen.findByRole('button', { name: 'Повторить' })).toBeInTheDocument();
    failed.unmount();
    fakeApi({ 'GET /api/settings': { items: [] } });
    await renderApp('/settings');
    expect(await screen.findByText('Нет доступных настроек.')).toBeInTheDocument();
  });
});
