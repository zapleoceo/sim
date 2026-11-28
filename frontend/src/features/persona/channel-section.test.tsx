import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Channel, ChannelKindInfo, LoginResponse, LoginView } from '@/lib/types';
import { fakeApi } from '@/test/fake-api';
import { renderWithApp } from '@/test/render';
import { availability, CHANNEL_KINDS } from './channel-kinds';
import { channelStatus, codeLabel, inProgress, instagramRequest, loginRequest, needsRelogin, shouldPoll, EMPTY_INSTAGRAM_FORM } from './channel-logic';
import { ChannelSection } from './channel-section';
import { PersonaIdProvider } from './persona-scope';

const CHANNEL: Channel = {
  id: 4, persona_id: 2, kind: 'telegram_user', title: 'Оля', status: 'new', account_label: '', last_error: null,
  last_activity_at: null,
};
const IG: Channel = { ...CHANNEL, id: 7, kind: 'instagram' };
const WA: Channel = { ...CHANNEL, id: 9, kind: 'whatsapp' };
const ALL_KINDS: ChannelKindInfo[] = [
  { kind: 'telegram_user', label: 'Telegram', available: true, reason: '' },
  { kind: 'instagram', label: 'Instagram', available: true, reason: '' },
  { kind: 'whatsapp', label: 'WhatsApp', available: true, reason: '' },
];

const step = (login: Partial<LoginView>, channel: Partial<Channel> = {}, base: Channel = CHANNEL): LoginResponse => ({
  channel: { ...base, ...channel },
  login: { state: 'qr', message: 'Отсканируйте QR', qr: 'data:image/svg+xml;base64,QQ==', detail: null, ...login },
});

async function render(): Promise<void> {
  await renderWithApp(
    <PersonaIdProvider id={2}>
      <ChannelSection pollMs={10} />
    </PersonaIdProvider>,
  );
}

async function pick(kind: 'Telegram' | 'Instagram' | 'WhatsApp'): Promise<void> {
  await userEvent.click(await screen.findByRole('button', { name: 'Подключить' }));
  const menu = screen.getByRole('group', { name: 'Какой мессенджер подключить' });
  await userEvent.click(within(menu).getByRole('button', { name: new RegExp(kind) }));
}

async function fillAndStart(): Promise<void> {
  await pick('Telegram');
  await userEvent.type(screen.getByLabelText('api_id'), '12345');
  await userEvent.type(screen.getByLabelText('api_hash'), '0123456789abcdef0123456789abcdef');
  await userEvent.click(screen.getByRole('button', { name: 'Показать QR-код' }));
}

describe('ChannelSection: Telegram QR connect', () => {
  it('shows the QR, polls it and finishes connected', async () => {
    let polls = 0;
    const log = fakeApi({
      'GET /api/personas/2/channels': () => ({ items: polls > 1 ? [{ ...CHANNEL, status: 'connected', account_label: '@olya' }] : [], kinds: ALL_KINDS }),
      'POST /api/personas/2/channels/telegram/login': step({}),
      'GET /api/personas/2/channels/4/login': () => {
        polls += 1;
        return polls < 2 ? step({ qr: 'data:image/svg+xml;base64,Qg==' }) : step({ state: 'connected', qr: null }, { status: 'connected', account_label: '@olya' });
      },
    });
    await render();
    expect(await screen.findByText('Канал не подключён')).toBeInTheDocument();
    await fillAndStart();

    const qr = await screen.findByRole('img', { name: 'QR-код для входа в Telegram' });
    expect(qr).toHaveAttribute('src', expect.stringContaining('data:image/svg+xml'));
    const post = log.find((r) => r.method === 'POST');
    expect(post?.body).toEqual({ api_id: 12345, api_hash: '0123456789abcdef0123456789abcdef', proxy: '' });

    expect(await screen.findByText('@olya')).toBeInTheDocument();          // the list re-read
    expect(screen.queryByRole('region', { name: 'Подключение Telegram' })).not.toBeInTheDocument();
    expect(polls).toBe(2);
  });

  it('asks for the 2FA password and retries a wrong one', async () => {
    let tries = 0;
    const log = fakeApi({
      'GET /api/personas/2/channels': { items: [] },
      'POST /api/personas/2/channels/telegram/login': step({ state: 'password', qr: null, message: 'Введите пароль' }),
      'POST /api/personas/2/channels/4/login/password': () => {
        tries += 1;
        return tries === 1
          ? step({ state: 'password', qr: null, message: 'Неверный пароль двухфакторной защиты' })
          : step({ state: 'connected', qr: null }, { status: 'connected' });
      },
    });
    await render();
    await fillAndStart();
    const password = await screen.findByLabelText('Пароль двухфакторной защиты');
    await userEvent.type(password, 'wrong');
    await userEvent.click(screen.getByRole('button', { name: 'Войти' }));
    expect(await screen.findByText('Неверный пароль двухфакторной защиты')).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('Пароль двухфакторной защиты'), 'right');
    await userEvent.click(screen.getByRole('button', { name: 'Войти' }));
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Подключение Telegram' })).not.toBeInTheDocument());
    const bodies = log.filter((r) => r.path.endsWith('/password')).map((r) => r.body);
    expect(bodies).toEqual([{ password: 'wrong' }, { password: 'right' }]);
  });

  it('shows an error with «Попробовать снова» and validates the form first', async () => {
    const log = fakeApi({
      'GET /api/personas/2/channels': { items: [] },
      'POST /api/personas/2/channels/telegram/login': step({ state: 'error', qr: null, message: 'Неверная пара api_id / api_hash' }),
    });
    await render();
    await pick('Telegram');
    await userEvent.click(screen.getByRole('button', { name: 'Показать QR-код' }));
    expect(await screen.findByText(/api_id — целое число/)).toBeInTheDocument();
    expect(log.filter((r) => r.method === 'POST')).toHaveLength(0);

    await userEvent.type(screen.getByLabelText('api_id'), '12345');
    await userEvent.type(screen.getByLabelText('api_hash'), '0123456789abcdef0123456789abcdef');
    await userEvent.click(screen.getByRole('button', { name: 'Показать QR-код' }));
    expect(await screen.findByText('Неверная пара api_id / api_hash')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Попробовать снова' }));
    expect(screen.getByRole('button', { name: 'Показать QR-код' })).toBeInTheDocument();
  });

  it('disables a connected channel after confirmation', async () => {
    const log = fakeApi({
      'GET /api/personas/2/channels': { items: [{ ...CHANNEL, status: 'connected', account_label: '@olya' }] },
      'POST /api/personas/2/channels/4/disable': { ...CHANNEL, status: 'disabled' },
    });
    await render();
    const row = (await screen.findByText('@olya')).closest('li') as HTMLElement;
    expect(within(row).getByText('подключён')).toBeInTheDocument();
    expect(within(row).getByRole('img', { name: 'Telegram' })).toBeInTheDocument();
    await userEvent.click(within(row).getByRole('button', { name: 'Отключить' }));
    const confirm = await screen.findByRole('alertdialog');
    await userEvent.click(within(confirm).getByRole('button', { name: 'Отключить' }));
    await waitFor(() => expect(log.some((r) => r.path.endsWith('/disable'))).toBe(true));
  });
});

describe('ChannelSection: the «Подключить» menu', () => {
  it('offers every messenger and says why one is not available', async () => {
    fakeApi({
      'GET /api/personas/2/channels': {
        items: [],
        kinds: [ALL_KINDS[0], ALL_KINDS[1], { ...ALL_KINDS[2], available: false, reason: 'Сервис WhatsApp не запущен на сервере' }],
      },
    });
    await render();
    await userEvent.click(await screen.findByRole('button', { name: 'Подключить' }));
    const menu = screen.getByRole('group', { name: 'Какой мессенджер подключить' });
    expect(within(menu).getAllByRole('button').map((b) => b.textContent)).toEqual([
      expect.stringContaining('Telegram'), expect.stringContaining('Instagram'), expect.stringContaining('WhatsApp'),
    ]);
    const whatsapp = within(menu).getByRole('button', { name: /WhatsApp/ });
    expect(whatsapp).toBeDisabled();
    expect(whatsapp).toHaveTextContent('Сервис WhatsApp не запущен на сервере');
    await userEvent.click(within(menu).getByRole('button', { name: /Instagram/ }));
    expect(screen.getByRole('region', { name: 'Подключение Instagram' })).toBeInTheDocument();
  });

  it('shows «нужен повторный вход» with «Войти заново» that opens the right form', async () => {
    fakeApi({
      'GET /api/personas/2/channels': { items: [{ ...IG, status: 'challenge', account_label: '@olya.ig', last_error: 'checkpoint' }], kinds: ALL_KINDS },
    });
    await render();
    const row = (await screen.findByText('@olya.ig')).closest('li') as HTMLElement;
    expect(within(row).getByText('нужен повторный вход')).toBeInTheDocument();
    expect(within(row).getByRole('img', { name: 'Instagram' })).toBeInTheDocument();
    await userEvent.click(within(row).getByRole('button', { name: 'Войти заново' }));
    expect(screen.getByRole('region', { name: 'Подключение Instagram' })).toBeInTheDocument();
  });
});

describe('ChannelSection: Instagram', () => {
  it('validates the form: the proxy is optional, but a typed one must be a proxy', async () => {
    const log = fakeApi({ 'GET /api/personas/2/channels': { items: [], kinds: ALL_KINDS } });
    await render();
    await pick('Instagram');
    await userEvent.type(screen.getByLabelText('Логин Instagram'), 'olya.ig');
    await userEvent.type(screen.getByLabelText('Пароль'), 'secret');
    await userEvent.type(screen.getByLabelText('Прокси (необязательно)'), 'ftp://host');
    await userEvent.click(screen.getByRole('button', { name: 'Войти' }));
    expect(await screen.findByText(/Прокси вида socks5/)).toBeInTheDocument();
    expect(log.filter((r) => r.method === 'POST')).toHaveLength(0);
  });

  it('logs in with a 2FA code', async () => {
    const log = fakeApi({
      'GET /api/personas/2/channels': { items: [], kinds: ALL_KINDS },
      'POST /api/personas/2/channels/instagram/login': step({ state: 'code', qr: null, detail: 'totp', message: 'Введите код' }, {}, IG),
      'POST /api/personas/2/channels/7/login/code': step({ state: 'connected', qr: null }, { status: 'connected', account_label: '@olya.ig' }, IG),
    });
    await render();
    await pick('Instagram');
    await userEvent.type(screen.getByLabelText('Логин Instagram'), '@olya.ig');
    await userEvent.type(screen.getByLabelText('Пароль'), 'secret');
    await userEvent.type(screen.getByLabelText('Прокси (необязательно)'), 'socks5://u:p@1.2.3.4:1080');
    await userEvent.click(screen.getByRole('button', { name: 'Войти' }));
    await userEvent.type(await screen.findByLabelText('Код из приложения-аутентификатора'), '123 456');
    await userEvent.click(screen.getByRole('button', { name: 'Подтвердить' }));
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Подключение Instagram' })).not.toBeInTheDocument());
    const posts = log.filter((r) => r.method === 'POST').map((r) => r.body);
    expect(posts).toEqual([
      { proxy: 'socks5://u:p@1.2.3.4:1080', username: 'olya.ig', password: 'secret' },
      { code: '123 456' },
    ]);
  });

  it('waits for the approval in the app and retries by itself', async () => {
    let polls = 0;
    fakeApi({
      'GET /api/personas/2/channels': { items: [], kinds: ALL_KINDS },
      'POST /api/personas/2/channels/instagram/login': step({ state: 'device', qr: null, message: 'Подтвердите вход в приложении Instagram' }, {}, IG),
      'GET /api/personas/2/channels/7/login': () => {
        polls += 1;
        return polls < 2 ? step({ state: 'device', qr: null, message: 'Подтвердите вход в приложении Instagram' }, {}, IG)
          : step({ state: 'connected', qr: null }, { status: 'connected' }, IG);
      },
    });
    await render();
    await pick('Instagram');
    await userEvent.click(screen.getByRole('button', { name: 'Вход по sessionid' }));
    await userEvent.type(screen.getByLabelText('sessionid'), '123456%3Aabc%3A1');
    await userEvent.type(screen.getByLabelText('Прокси (необязательно)'), 'socks5://u:p@1.2.3.4:1080');
    await userEvent.click(screen.getByRole('button', { name: 'Войти' }));
    expect(await screen.findByText('Подтвердите вход в приложении Instagram')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Я подтвердил' })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Подключение Instagram' })).not.toBeInTheDocument());
    expect(polls).toBe(2);
  });

  it('shows «Instagram проверяет вход…» and re-reads until Instagram asks for a code', async () => {
    // instagrapi 3.2's login takes up to minutes (prod, 2026-11-28: the request hung).
    let polls = 0;
    fakeApi({
      'GET /api/personas/2/channels': { items: [], kinds: ALL_KINDS },
      'POST /api/personas/2/channels/instagram/login': step({ state: 'wait', qr: null, message: 'Instagram проверяет вход…' }, {}, IG),
      'GET /api/personas/2/channels/7/login': () => {
        polls += 1;
        return polls < 2 ? step({ state: 'wait', qr: null, message: 'Instagram проверяет вход…' }, {}, IG)
          : step({ state: 'code', qr: null, detail: 'code', message: 'Введите код' }, {}, IG);
      },
    });
    await render();
    await pick('Instagram');
    await userEvent.type(screen.getByLabelText('Логин Instagram'), 'olya.ig');
    await userEvent.type(screen.getByLabelText('Пароль'), 'secret');
    await userEvent.click(screen.getByRole('button', { name: 'Войти' }));
    expect(await screen.findByText('Instagram проверяет вход…')).toBeInTheDocument();
    expect(await screen.findByLabelText('Код подтверждения входа')).toBeInTheDocument();
    expect(polls).toBe(2);
  });
});

describe('ChannelSection: WhatsApp QR', () => {
  it('shows the QR, waits for the phone and finishes connected', async () => {
    let polls = 0;
    const log = fakeApi({
      'GET /api/personas/2/channels': { items: [], kinds: ALL_KINDS },
      'POST /api/personas/2/channels/whatsapp/login': step({ qr: 'data:image/png;base64,QQ==' }, { status: 'pending' }, WA),
      'GET /api/personas/2/channels/9/login': () => {
        polls += 1;
        return polls < 3 ? step({ qr: 'data:image/png;base64,Qg==' }, { status: 'pending' }, WA)
          : step({ state: 'connected', qr: null }, { status: 'connected', account_label: '+79001234567' }, WA);
      },
    });
    await render();
    await pick('WhatsApp');
    await userEvent.click(screen.getByRole('button', { name: 'Показать QR-код' }));
    const qr = await screen.findByRole('img', { name: 'QR-код для входа в WhatsApp' });
    expect(qr).toHaveAttribute('src', expect.stringContaining('data:image/png'));
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Подключение WhatsApp' })).not.toBeInTheDocument());
    expect(polls).toBe(3);
    expect(log.find((r) => r.method === 'POST')?.body).toEqual({ phone: '' });
  });

  it('cancels a pairing: the instance is dropped', async () => {
    const log = fakeApi({
      'GET /api/personas/2/channels': { items: [], kinds: ALL_KINDS },
      'POST /api/personas/2/channels/whatsapp/login': step({}, { status: 'pending' }, WA),
      'GET /api/personas/2/channels/9/login': step({}, { status: 'pending' }, WA),
      'DELETE /api/personas/2/channels/9/login': null,
    });
    await render();
    await pick('WhatsApp');
    await userEvent.click(screen.getByRole('button', { name: 'Показать QR-код' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Отменить подключение' }));
    await waitFor(() => expect(log.some((r) => r.method === 'DELETE')).toBe(true));
    expect(screen.queryByRole('region', { name: 'Подключение WhatsApp' })).not.toBeInTheDocument();
  });
});

describe('channel logic', () => {
  it('polls only while a QR may be scanned, the app approval is awaited or Instagram still answers', () => {
    expect(shouldPoll('qr')).toBe(true);
    expect(shouldPoll('device')).toBe(true);
    expect(shouldPoll('wait')).toBe(true);
    for (const state of ['password', 'code', 'challenge', 'manual', 'connected', 'error', 'none', undefined] as const) {
      expect(shouldPoll(state)).toBe(false);
    }
    expect(inProgress('code')).toBe(true);
    expect(inProgress('error')).toBe(false);
  });

  it('checks the Telegram login form', () => {
    expect(loginRequest({ api_id: '12', api_hash: 'abcdef0123', proxy: '' }).body).toEqual({ api_id: 12, api_hash: 'abcdef0123', proxy: '' });
    expect(loginRequest({ api_id: 'x', api_hash: 'zz', proxy: 'ftp://h' }).errors).toHaveLength(3);
    expect(loginRequest({ api_id: '1', api_hash: 'abcdef0123', proxy: 'socks5://u:p@1.2.3.4:1080' }).errors).toEqual([]);
  });

  it('checks the Instagram form and sends only the chosen way in', () => {
    const proxy = 'socks5://u:p@1.2.3.4:1080';
    // No proxy: the login goes from the server's IP (the owner, 2026-11-28).
    expect(instagramRequest({ ...EMPTY_INSTAGRAM_FORM, username: 'olya', password: 'p' }))
      .toEqual({ errors: [], body: { proxy: '', username: 'olya', password: 'p' } });
    expect(instagramRequest({ ...EMPTY_INSTAGRAM_FORM, proxy, username: 'bad name!', password: '' }).errors).toHaveLength(2);
    expect(instagramRequest({ ...EMPTY_INSTAGRAM_FORM, proxy, method: 'sessionid', sessionid: '42%3Aabc', username: 'x' }).body)
      .toEqual({ proxy, sessionid: '42%3Aabc' });
    expect(instagramRequest({ ...EMPTY_INSTAGRAM_FORM, proxy, method: 'sessionid', sessionid: 'nope' }).errors).toHaveLength(1);
    expect(instagramRequest({ ...EMPTY_INSTAGRAM_FORM, proxy, method: 'settings', settings_json: '{"uuids": {}}' }).errors).toHaveLength(1);
    expect(instagramRequest({ ...EMPTY_INSTAGRAM_FORM, proxy, method: 'settings', settings_json: '{"authorization_data": {}}' }).body)
      .toEqual({ proxy, settings_json: '{"authorization_data": {}}' });
  });

  it('names the code, the statuses and the kinds', () => {
    expect(codeLabel('code', 'totp')).toBe('Код из приложения-аутентификатора');
    expect(codeLabel('challenge', 'email')).toBe('Код из письма Instagram');
    expect(codeLabel('code', 'code')).toBe('Код подтверждения входа');     // a Bloks (CAA) login
    expect(channelStatus('challenge')).toEqual({ label: 'нужен повторный вход', variant: 'destructive' });
    expect(channelStatus('pending').label).toBe('идёт вход');
    expect(needsRelogin('challenge') && !needsRelogin('connected')).toBe(true);
    expect(CHANNEL_KINDS.map((k) => k.kind)).toEqual(['telegram_user', 'instagram', 'whatsapp']);
    expect(availability('whatsapp', undefined)).toEqual({ available: true, reason: '' });
    expect(availability('whatsapp', [{ ...ALL_KINDS[2], available: false, reason: 'нет' }]).available).toBe(false);
  });
});
