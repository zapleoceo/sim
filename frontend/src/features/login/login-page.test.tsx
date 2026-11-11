import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ApiError } from '@/lib/api/http';
import { authStore } from '@/lib/auth';
import type { TelegramWidgetUser } from '@/lib/models';
import { fakeApi, reply } from '@/test/fake-api';
import { renderApp, testUser } from '@/test/render-app';
import { TELEGRAM_WIDGET_SRC, loginFailure } from './login-page';

const TG_USER: TelegramWidgetUser = { id: 42, first_name: 'D', auth_date: 1, hash: 'h' };

function widgetCallback(): (u: TelegramWidgetUser) => void {
  const cb = (window as unknown as { onTelegramAuth?: (u: TelegramWidgetUser) => void }).onTelegramAuth;
  if (!cb) throw new Error('widget not mounted');
  return cb;
}

describe('loginFailure', () => {
  it('maps 403 to the Telegram ID, 401 and others to messages', () => {
    expect(loginFailure(new ApiError(403, { detail: 'x', telegram_id: 77 }, '/x'))).toEqual({ deniedId: 77, error: '' });
    expect(loginFailure(new ApiError(401, null, '/x')).error).toMatch(/Не удалось подтвердить вход/);
    expect(loginFailure(new ApiError(0, null, '/x')).error).toBe('Нет связи с сервером');
  });
});

describe('LoginPage', () => {
  it('mounts the Telegram widget for the configured bot', async () => {
    fakeApi({ 'GET /api/auth/config': { bot_username: 'sim_login_bot' } });
    await renderApp('/login', { user: null });
    await waitFor(() => expect(document.querySelector(`script[src="${TELEGRAM_WIDGET_SRC}"]`)).not.toBeNull());
    const script = document.querySelector('script[data-telegram-login]');
    expect(script?.getAttribute('data-telegram-login')).toBe('sim_login_bot');
  });

  it('explains how to configure sign-in when the bot is not set', async () => {
    fakeApi({ 'GET /api/auth/config': { bot_username: '' } });
    await renderApp('/login', { user: null });
    expect(await screen.findByText('Вход через Telegram не настроен')).toBeInTheDocument();
  });

  it('shows the Telegram ID with a copy button when access is denied', async () => {
    fakeApi({
      'GET /api/auth/config': { bot_username: 'b' },
      'POST /api/auth/telegram': reply(403, { detail: 'denied', telegram_id: 4242 }),
    });
    // user-event installs its own clipboard stub on setup(), so ours goes in afterwards.
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    await renderApp('/login', { user: null });
    await waitFor(() => expect(document.querySelector('script[data-telegram-login]')).not.toBeNull());
    await act(async () => widgetCallback()(TG_USER));
    expect(await screen.findByText('4242')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Скопировать ID/ }));
    expect(writeText).toHaveBeenCalledWith('4242');
  });

  it('signs in and returns to the requested page', async () => {
    fakeApi({
      'GET /api/auth/config': { bot_username: 'b' },
      'POST /api/auth/telegram': { user: testUser('operator'), csrf_token: 'c' },
      'GET /api/dialogs': { items: [], total: 0 },
      'GET /api/personas': { items: [] },
    });
    const { router } = await renderApp('/login?returnUrl=%2Finbox', { user: null });
    await waitFor(() => expect(document.querySelector('script[data-telegram-login]')).not.toBeNull());
    await act(async () => widgetCallback()(TG_USER));
    await waitFor(() => expect(router.state.location.pathname).toBe('/inbox'));
    expect(authStore.get().csrf).toBe('c');
  });
});
