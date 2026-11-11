import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { AppSettingItem } from '@/lib/models';
import { fakeApi, reply } from '@/test/fake-api';
import { CSRF, renderApp, testUser } from '@/test/render-app';
import { groupSettings, parseSettingValue, replaceSetting, settingHint } from './settings-logic';

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

  it('parses integers within range and keeps text as is', () => {
    expect(parseSettingValue(DELAY, ' 45 ')).toEqual({ ok: true, value: 45 });
    expect(parseSettingValue(DELAY, '4.5')).toEqual({ ok: false, error: 'Нужно целое число' });
    expect(parseSettingValue(DELAY, '')).toEqual({ ok: false, error: 'Нужно целое число' });
    expect(parseSettingValue(DELAY, '601')).toEqual({ ok: false, error: 'Не больше 600' });
    expect(parseSettingValue(DELAY, '-1')).toEqual({ ok: false, error: 'Не меньше 0' });
    expect(parseSettingValue(STYLE, ' пробелы ')).toEqual({ ok: true, value: ' пробелы ' });
  });

  it('hint shows the range and replace keeps order', () => {
    expect(settingHint({ ...DELAY, help: 'Задержка' })).toBe('Задержка (0–600)');
    expect(settingHint(STYLE)).toBe('');
    expect(replaceSetting([STYLE, DELAY], { ...STYLE, value: 'x' }).map((i) => i.value)).toEqual(['x', 30]);
  });
});

describe('SettingsPage', () => {
  it('shows every field open, saves with CSRF and resets to default', async () => {
    const log = fakeApi({
      'GET /api/settings': { items: [STYLE, GUARD, DELAY] },
      'PUT /api/settings/style': (r) => ({ ...STYLE, value: (r.body as { value: string }).value, is_default: false }),
    });
    await renderApp('/settings');
    const user = userEvent.setup();

    const style = await screen.findByLabelText('Манера письма');
    expect(screen.queryByLabelText('Промпт охранника')).not.toBeInTheDocument();   // the guard has its own page
    expect(screen.getByRole('region', { name: 'Стиль' })).toBeInTheDocument();

    const styleBlock = style.closest('div.flex.flex-col.gap-2') as HTMLElement;
    const saveStyle = within(styleBlock).getByRole('button', { name: 'Сохранить' });
    expect(saveStyle).toBeDisabled();
    await user.clear(style);
    await user.type(style, 'очень коротко');
    await user.click(saveStyle);
    await waitFor(() => expect(log.some((r) => r.method === 'PUT')).toBe(true));
    const put = log.find((r) => r.method === 'PUT');
    expect(put?.body).toEqual({ value: 'очень коротко' });
    expect(put?.headers['X-CSRF-Token']).toBe(CSRF);
  });

  it('the guard has its own page with only its settings, and resets to default', async () => {
    fakeApi({
      'GET /api/settings': { items: [STYLE, GUARD, DELAY] },
      'DELETE /api/settings/guard_prompt': { ...GUARD, value: 'исходный', is_default: true, updated_at: null },
    });
    await renderApp('/guard');
    const user = userEvent.setup();
    const prompt = await screen.findByLabelText('Промпт охранника');
    expect(prompt).toHaveValue('свой');
    expect(screen.queryByLabelText('Манера письма')).not.toBeInTheDocument();
    const guard = prompt.closest('div.flex.flex-col.gap-2') as HTMLElement;
    await user.click(within(guard).getByRole('button', { name: /Вернуть по умолчанию/ }));
    await waitFor(() => expect(screen.getByLabelText('Промпт охранника')).toHaveValue('исходный'));
  });

  it('rejects a non-integer before calling the API', async () => {
    const log = fakeApi({ 'GET /api/settings': { items: [DELAY] } });
    await renderApp('/settings');
    const user = userEvent.setup();
    const input = await screen.findByLabelText('Пауза, с');
    await user.clear(input);
    await user.type(input, 'abc');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));
    expect(await screen.findByText('Нужно целое число')).toBeInTheDocument();
    expect(log.some((r) => r.method === 'PUT')).toBe(false);
  });

  it('offers a retry when the list fails and is owner-only', async () => {
    fakeApi({ 'GET /api/settings': reply(500), 'GET /api/dialogs': { items: [], total: 0 }, 'GET /api/personas': { items: [] } });
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const owner = await renderApp('/settings');
    expect(await screen.findByRole('button', { name: 'Повторить' })).toBeInTheDocument();
    owner.unmount();
    const operator = await renderApp('/settings', { user: testUser('operator') });
    await waitFor(() => expect(operator.router.state.location.pathname).toBe('/inbox'));
  });
});
