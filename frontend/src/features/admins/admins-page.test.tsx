import { waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { AdminUser } from '@/lib/models';
import { fakeApi } from '@/test/fake-api';
import { renderApp, screen, testUser } from '@/test/render-app';
import { adminName, adminTargetError, parseAdminTarget } from './admins-logic';

const me: AdminUser = { ...testUser('superadmin'), is_active: true, created_at: '2026-01-01T00:00:00Z' };
const op: AdminUser = {
  id: 2, role: 'operator', telegram_id: null, tg_username: 'olga', display_name: '', photo_url: '', is_active: false,
  created_at: '2026-01-01T00:00:00Z',
};

describe('admins logic', () => {
  it('parses a numeric id or a username', () => {
    expect(parseAdminTarget(' 123456 ')).toEqual({ telegram_id: 123456 });
    expect(parseAdminTarget('@olga_k')).toEqual({ tg_username: 'olga_k' });
    expect(parseAdminTarget('olga_k')).toEqual({ tg_username: 'olga_k' });
  });

  it('validates the target', () => {
    expect(adminTargetError('')).toMatch(/Укажите/);
    expect(adminTargetError('@ab')).toMatch(/3–32/);
    expect(adminTargetError('@olga')).toBe('');
    expect(adminTargetError('42')).toBe('');
  });

  it('names an admin by display name, username or id', () => {
    expect(adminName(op)).toBe('olga');
    expect(adminName({ ...op, tg_username: null, telegram_id: 7 })).toBe('7');
  });
});

describe('AdminsPage', () => {
  it('lists admins and protects the current user', async () => {
    fakeApi({ 'GET /api/admins': { items: [me, op] } });
    await renderApp('/admins', { user: testUser('superadmin') });

    const meRow = (await screen.findByText('(вы)')).closest('tr')!;
    expect(within(meRow).queryByRole('button', { name: /Удалить/ })).not.toBeInTheDocument();
    expect(within(meRow).getByRole('combobox', { name: /Роль/ })).toBeDisabled();
    const opRow = screen.getByRole('button', { name: 'Удалить olga' }).closest('tr')!;
    expect(opRow).toHaveClass('opacity-60');
    expect(within(opRow).getByRole('button', { name: 'Включить' })).toBeInTheDocument();
  });

  it('adds by telegram_id or tg_username', async () => {
    let nextId = 10;
    const log = fakeApi({
      'GET /api/admins': { items: [me] },
      'POST /api/admins': (req: { body: unknown }) => ({ ...op, id: nextId++, ...(req.body as object) }),
    });
    const user = userEvent.setup();
    await renderApp('/admins', { user: testUser('superadmin') });

    const input = await screen.findByLabelText('Telegram ID или @username');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));
    expect(await screen.findByText('Укажите Telegram ID или @username')).toBeInTheDocument();

    await user.type(input, '555');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));
    await waitFor(() => expect(log.filter((r) => r.method === 'POST')).toHaveLength(1));
    expect(log.filter((r) => r.method === 'POST')[0].body).toEqual({ telegram_id: 555, role: 'operator' });

    await user.type(input, '@new_admin');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));
    await waitFor(() => expect(log.filter((r) => r.method === 'POST')).toHaveLength(2));
    expect(log.filter((r) => r.method === 'POST')[1].body).toEqual({ tg_username: 'new_admin', role: 'operator' });
  });

  it('is only for the superadmin', async () => {
    fakeApi({ 'GET /api/admins': { items: [] }, 'GET /api/dialogs': { items: [], total: 0 }, 'GET /api/personas': { items: [] } });
    const { router } = await renderApp('/admins', { user: testUser('owner') });
    await waitFor(() => expect(router.state.location.pathname).toBe('/inbox'));
  });
});
