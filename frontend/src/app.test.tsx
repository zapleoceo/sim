import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { fakeApi, reply } from '@/test/fake-api';
import { renderApp, testUser } from '@/test/render-app';
import { titleFor } from './router';

/** Empty but valid answers for every list the pages read on open. */
const EMPTY_LISTS = {
  'GET /api/dialogs': { items: [], total: 0 },
  'GET /api/personas': { items: [] },
  'GET /api/channels': { items: [] },
  'GET /api/clients': { items: [], total: 0 },
  'GET /api/admins': { items: [] },
  'GET /api/studio': { photo_url: null, video_url: null, models: [], text_available: false, media_available: false, detail: '' },
};

describe('app shell and guards', () => {
  it('sends a signed-out visitor to /login with returnUrl', async () => {
    fakeApi({ 'GET /api/auth/me': reply(401), 'GET /api/auth/config': { bot_username: '' } });
    const { router } = await renderApp('/clients', { user: null });
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'));
    expect(router.state.location.search).toEqual({ returnUrl: '/clients' });
  });

  it('lands an owner on the studio and an operator on the inbox', async () => {
    fakeApi(EMPTY_LISTS);
    const owner = await renderApp('/');
    await waitFor(() => expect(owner.router.state.location.pathname).toBe('/studio'));
    owner.unmount();
    const operator = await renderApp('/', { user: testUser('operator') });
    await waitFor(() => expect(operator.router.state.location.pathname).toBe('/inbox'));
  });

  it('keeps operators out of owner and superadmin pages', async () => {
    fakeApi(EMPTY_LISTS);
    const a = await renderApp('/personas', { user: testUser('operator') });
    await waitFor(() => expect(a.router.state.location.pathname).toBe('/inbox'));
    a.unmount();
    const b = await renderApp('/admins', { user: testUser('owner') });
    await waitFor(() => expect(b.router.state.location.pathname).toBe('/inbox'));
  });

  it('shows navigation by role, with the active section marked', async () => {
    fakeApi(EMPTY_LISTS);
    await renderApp('/inbox', { user: testUser('operator') });
    const nav = screen.getByRole('navigation', { name: 'Разделы' });
    expect(within(nav).getByRole('link', { name: /Диалоги/ })).toHaveAttribute('aria-current', 'page');
    expect(within(nav).queryByRole('link', { name: /Персонажи/ })).toBeNull();
    expect(within(nav).queryByRole('link', { name: /Администраторы/ })).toBeNull();
  });

  it('redirects unknown paths to the landing page', async () => {
    fakeApi(EMPTY_LISTS);
    const { router } = await renderApp('/no-such-page', { user: testUser('operator') });
    await waitFor(() => expect(router.state.location.pathname).toBe('/inbox'));
  });

  it('logs out from the user menu', async () => {
    const log = fakeApi({ ...EMPTY_LISTS, 'POST /api/auth/logout': reply(204), 'GET /api/auth/config': { bot_username: '' } });
    const { router } = await renderApp('/inbox', { user: testUser('operator') });
    const user = userEvent.setup();
    const aside = screen.getByRole('complementary', { name: 'Основная навигация' });
    await user.click(within(aside).getByRole('button', { name: /: меню$/ }));
    await user.click(await screen.findByRole('menuitem', { name: /Выйти/ }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'));
    expect(log.some((r) => r.method === 'POST' && r.path === '/api/auth/logout')).toBe(true);
  });

  it('phone bottom bar has five slots; «Ещё» opens every other section', async () => {
    fakeApi(EMPTY_LISTS);
    await renderApp('/inbox');
    const bar = screen.getByRole('navigation', { name: 'Быстрая навигация' });
    expect(within(bar).getAllByRole('link').map((a) => a.textContent)).toEqual(['Студия', 'Диалоги', 'Клиенты', 'Воронка']);
    expect(within(bar).getByRole('link', { name: 'Диалоги' })).toHaveAttribute('aria-current', 'page');
    expect(bar.children).toHaveLength(5);
    // The bar leaves the screen while the phone keyboard is open, and its icons are 20 px.
    expect(bar).toHaveClass('kb:hidden', 'md:hidden');
    expect(bar.querySelectorAll('svg.size-\\[20px\\]')).toHaveLength(5);

    await userEvent.click(within(bar).getByRole('button', { name: 'Ещё' }));
    const sheet = await screen.findByRole('dialog');
    for (const name of ['Аналитика', 'Активность', 'Персонажи', 'Общие правила', 'Каналы']) {
      expect(within(sheet).getByRole('link', { name })).toBeInTheDocument();
    }
  });

  it('page titles follow the section', () => {
    expect(titleFor('/clients/5')).toBe('Клиенты — Sim');
    expect(titleFor('/')).toBe('Sim — админ-панель');
  });
});
