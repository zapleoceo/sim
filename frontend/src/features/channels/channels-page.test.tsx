import { waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Channel, Persona } from '@/lib/models';
import { fakeApi } from '@/test/fake-api';
import { CSRF, renderApp, screen } from '@/test/render-app';
import { channelVm } from './channels-page';

const persona: Persona = {
  id: 3, name: 'Марина', age: 30, gender: 'female', avatar_url: '', description: 'd', prohibitions: '', language: 'ru',
  model: '', voice_id: '', voice_mode: 'off', voice_style: '', strategy_id: null, is_active: true, created_at: '2026-01-01T00:00:00Z',
};
const igChannel: Channel = {
  id: 4, kind: 'instagram', name: 'Insta', persona_id: 3, status: 'challenge', account_label: '@marina',
  last_error: 'ChallengeRequired: checkpoint', last_activity_at: null,
};

describe('channelVm', () => {
  it('maps status tone, persona and a humanized error', () => {
    const vm = channelVm(igChannel, [persona]);
    expect(vm).toMatchObject({ label: 'Нужно подтверждение', tone: 'warning', persona: 'Марина', kindLabel: 'Instagram' });
    expect(vm.error).toMatch(/Instagram просит подтвердить вход/);
    expect(channelVm({ ...igChannel, persona_id: 99 }, [persona]).persona).toBe('—');
  });
});

describe('ChannelsPage', () => {
  it('disables adding without personas', async () => {
    fakeApi({ 'GET /api/channels': { items: [] }, 'GET /api/personas': { items: [] } });
    await renderApp('/channels');
    expect(await screen.findByText(/Сначала создайте хотя бы одного персонажа/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Добавить канал' })).toBeDisabled();
  });

  it('creates and connects a Telegram bot through the wizard', async () => {
    const created: Channel = { ...igChannel, id: 5, kind: 'telegram', name: 'Telegram (бот)', status: 'new', last_error: null };
    const log = fakeApi({
      'GET /api/channels': { items: [] },
      'GET /api/personas': { items: [persona] },
      'POST /api/channels': created,
      'POST /api/channels/5/connect': { status: 'active' },
    });
    const user = userEvent.setup();
    await renderApp('/channels');

    await user.click(await screen.findByRole('button', { name: 'Добавить канал' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('radio', { name: /Telegram \(бот\)/ })).toHaveAttribute('aria-checked', 'true');
    await user.click(within(dialog).getByRole('button', { name: 'Далее' }));

    const token = await within(dialog).findByLabelText('Токен бота');
    await user.type(token, '123');
    await user.click(within(dialog).getByRole('button', { name: 'Подключить' }));
    expect(await within(dialog).findByText(/Токен выглядит как/)).toBeInTheDocument();

    await user.clear(token);
    await user.type(token, '123456:ABCDEFGHIJKLMNOPQRSTUVWX');
    await user.click(within(dialog).getByRole('button', { name: 'Подключить' }));
    expect(await within(dialog).findByText('Канал подключён')).toBeInTheDocument();

    const create = log.find((r) => r.method === 'POST' && r.path === '/api/channels')!;
    expect(create.body).toEqual({ kind: 'telegram', persona_id: 3, name: 'Telegram (бот)' });
    expect(create.headers['X-CSRF-Token']).toBe(CSRF);
    const connect = log.find((r) => r.path === '/api/channels/5/connect')!;
    expect(connect.body).toEqual({ bot_token: '123456:ABCDEFGHIJKLMNOPQRSTUVWX' });

    await user.click(within(dialog).getByRole('button', { name: 'Готово' }));
    await waitFor(() => expect(log.filter((r) => r.method === 'GET' && r.path === '/api/channels')).toHaveLength(2));
  });

  it('asks for the code when Instagram requests a challenge', async () => {
    const log = fakeApi({
      'GET /api/channels': { items: [igChannel] },
      'GET /api/personas': { items: [persona] },
      'POST /api/channels/4/connect': { status: 'challenge', need: 'challenge' },
      'POST /api/channels/4/verify': { status: 'active' },
    });
    const user = userEvent.setup();
    await renderApp('/channels');

    const card = await screen.findByRole('article', { name: 'Insta' });
    expect(within(card).getByRole('alert')).toHaveTextContent('Instagram просит подтвердить вход');
    await user.click(within(card).getByRole('button', { name: 'Подключить' }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Логин Instagram'), 'marina');
    await user.type(within(dialog).getByLabelText('Пароль'), ' pw ');
    await user.click(within(dialog).getByRole('button', { name: 'Подключить' }));

    await user.type(await within(dialog).findByLabelText('Код'), '123456');
    await user.click(within(dialog).getByRole('button', { name: 'Подтвердить' }));
    expect(await within(dialog).findByText('Канал подключён')).toBeInTheDocument();
    expect(log.find((r) => r.path === '/api/channels/4/connect')!.body).toEqual({ username: 'marina', password: ' pw ' });
    expect(log.find((r) => r.path === '/api/channels/4/verify')!.body).toEqual({ code: '123456' });
  });
});
