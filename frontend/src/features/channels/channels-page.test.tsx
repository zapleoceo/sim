import { waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Channel, Persona } from '@/lib/models';
import { fakeApi } from '@/test/fake-api';
import { CSRF, renderApp, screen } from '@/test/render-app';
import { channelVm } from './channels-page';

const persona: Persona = {
  id: 3, name: 'Марина', age: 30, gender: 'female', avatar_url: '', description: 'd', prohibitions: '', language: 'ru',
  model: '', voice_id: '', voice_mode: 'off', voice_style: '', voice_ambience: 'off', strategy_id: null, is_active: true, created_at: '2026-01-01T00:00:00Z', birth_date: null, timezone: 'Europe/Kyiv',
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

  it('reconnects with the saved credentials without typing them again', async () => {
    const tgUser: Channel = {
      id: 6, kind: 'telegram_user', name: 'Marina', persona_id: 3, status: 'challenge', account_label: null,
      last_error: null, last_activity_at: null, has_saved_credentials: true,
    };
    const log = fakeApi({
      'GET /api/channels': { items: [tgUser] },
      'GET /api/personas': { items: [persona] },
      'GET /api/channels/6/credentials': { fields: { api_id: '12345', phone: '+380501234567', method: 'code' }, saved_secrets: ['api_hash'] },
      'POST /api/channels/6/connect': { status: 'challenge', need: 'code', message: 'Код отправлен в приложение Telegram' },
    });
    const user = userEvent.setup();
    await renderApp('/channels');
    await user.click(within(await screen.findByRole('article', { name: 'Marina' })).getByRole('button', { name: 'Подключить' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('status')).toHaveTextContent('Данные прошлого подключения сохранены');
    await waitFor(() => expect(within(dialog).getByLabelText('api_id')).toHaveValue('12345'));
    expect(within(dialog).getByLabelText('Телефон аккаунта')).toHaveValue('+380501234567');
    // the secret never comes back to the browser: the field stays empty, marked as saved
    expect(within(dialog).getByLabelText('api_hash')).toHaveValue('');
    expect(within(dialog).getByLabelText('api_hash')).toHaveAttribute('placeholder', 'сохранено, оставьте пустым');
    await user.click(within(dialog).getByRole('button', { name: 'Подключить' }));
    expect(await within(dialog).findByLabelText('Код')).toBeInTheDocument();
    expect(log.find((r) => r.path === '/api/channels/6/connect')!.body).toEqual({ api_id: '12345', api_hash: '', phone: '+380501234567', method: 'code' });
  });

  it('connects a Telegram account by QR and asks for the 2FA password after the scan', async () => {
    const tgUser: Channel = {
      id: 7, kind: 'telegram_user', name: 'Eva', persona_id: 3, status: 'new', account_label: null,
      last_error: null, last_activity_at: null,
    };
    let polls = 0;
    const log = fakeApi({
      'GET /api/channels': { items: [tgUser] },
      'GET /api/personas': { items: [persona] },
      'POST /api/channels/7/connect': { status: 'pending', qr_data_uri: 'data:image/svg+xml,qr1' },
      'GET /api/channels/7/state': () => (++polls === 1
        ? { status: 'pending', qr_data_uri: 'data:image/svg+xml,qr2' }
        : { status: 'challenge', need: '2fa' }),
    });
    const user = userEvent.setup();
    await renderApp('/channels');
    await user.click(within(await screen.findByRole('article', { name: 'Eva' })).getByRole('button', { name: 'Подключить' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('radio', { name: /По QR-коду/ })).toHaveAttribute('aria-checked', 'true');
    expect(within(dialog).queryByLabelText('Телефон аккаунта')).not.toBeInTheDocument();
    await user.type(within(dialog).getByLabelText('api_id'), '12345');
    await user.type(within(dialog).getByLabelText('api_hash'), 'a'.repeat(32));
    await user.click(within(dialog).getByRole('button', { name: 'Подключить' }));
    expect(await within(dialog).findByRole('img', { name: /QR-код для привязки/ })).toHaveAttribute('src', 'data:image/svg+xml,qr1');
    expect(log.find((r) => r.path === '/api/channels/7/connect')!.body).toEqual({ api_id: '12345', api_hash: 'a'.repeat(32), method: 'qr' });
    expect(await within(dialog).findByLabelText('Пароль 2FA', {}, { timeout: 8000 })).toBeInTheDocument();
  }, 15000);
});
