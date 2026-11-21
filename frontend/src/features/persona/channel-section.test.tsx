import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Channel, LoginResponse, LoginView } from '@/lib/types';
import { fakeApi } from '@/test/fake-api';
import { renderWithApp } from '@/test/render';
import { loginRequest, shouldPoll } from './channel-logic';
import { ChannelSection } from './channel-section';
import { PersonaIdProvider } from './persona-scope';

const CHANNEL: Channel = {
  id: 4, persona_id: 2, kind: 'telegram_user', title: 'Оля', status: 'new', account_label: '', last_error: null,
  last_activity_at: null,
};

const step = (login: Partial<LoginView>, channel: Partial<Channel> = {}): LoginResponse => ({
  channel: { ...CHANNEL, ...channel },
  login: { state: 'qr', message: 'Отсканируйте QR', qr: 'data:image/svg+xml;base64,QQ==', ...login },
});

async function render(): Promise<void> {
  await renderWithApp(
    <PersonaIdProvider id={2}>
      <ChannelSection pollMs={10} />
    </PersonaIdProvider>,
  );
}

async function fillAndStart(): Promise<void> {
  await userEvent.click(await screen.findByRole('button', { name: 'Подключить Telegram' }));
  await userEvent.type(screen.getByLabelText('api_id'), '12345');
  await userEvent.type(screen.getByLabelText('api_hash'), '0123456789abcdef0123456789abcdef');
  await userEvent.click(screen.getByRole('button', { name: 'Показать QR-код' }));
}

describe('ChannelSection: QR connect', () => {
  it('shows the QR, polls it and finishes connected', async () => {
    let polls = 0;
    const log = fakeApi({
      'GET /api/personas/2/channels': () => ({ items: polls > 1 ? [{ ...CHANNEL, status: 'connected', account_label: '@olya' }] : [] }),
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
    await userEvent.click(await screen.findByRole('button', { name: 'Подключить Telegram' }));
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
    await userEvent.click(within(row).getByRole('button', { name: 'Отключить' }));
    const confirm = await screen.findByRole('alertdialog');
    await userEvent.click(within(confirm).getByRole('button', { name: 'Отключить' }));
    await waitFor(() => expect(log.some((r) => r.path.endsWith('/disable'))).toBe(true));
  });
});

describe('channel logic', () => {
  it('polls only while the QR may be scanned', () => {
    expect(shouldPoll('qr')).toBe(true);
    for (const state of ['password', 'connected', 'error', 'none', undefined] as const) expect(shouldPoll(state)).toBe(false);
  });

  it('checks the login form', () => {
    expect(loginRequest({ api_id: '12', api_hash: 'abcdef0123', proxy: '' }).body).toEqual({ api_id: 12, api_hash: 'abcdef0123', proxy: '' });
    expect(loginRequest({ api_id: 'x', api_hash: 'zz', proxy: 'ftp://h' }).errors).toHaveLength(3);
    expect(loginRequest({ api_id: '1', api_hash: 'abcdef0123', proxy: 'socks5://u:p@1.2.3.4:1080' }).errors).toEqual([]);
  });
});
