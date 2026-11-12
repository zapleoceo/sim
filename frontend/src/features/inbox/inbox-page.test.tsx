import userEvent from '@testing-library/user-event';
import { waitFor } from '@testing-library/react';
import type { Client, DialogDetail, DialogRow } from '@/lib/models';
import { fakeApi } from '@/test/fake-api';
import { CSRF, renderApp, screen } from '@/test/render-app';

const client: Client = {
  id: 7,
  display_name: 'Анна Клиентова',
  avatar_url: '',
  persona_id: 1,
  stage: 'engaged',
  status: 'active',
  next_step: null,
  next_step_at: null,
  notes: null,
  needs: [],
  channels: [{ kind: 'telegram', dialog_id: 1 }],
  last_contact_at: null,
  created_at: '2026-11-01T10:00:00Z',
};

const row: DialogRow = {
  id: 1,
  channel_kind: 'telegram',
  persona: { id: 1, name: 'Марина', avatar_url: '' },
  client: { id: 7, display_name: 'Анна Клиентова', avatar_url: '', stage: 'engaged', status: 'active' },
  last_message: { text: 'Здравствуйте', direction: 'in', at: '2026-11-11T09:00:00Z' },
  unread: 2,
  ai_paused: false,
  needs_operator: true,
};

const detail: DialogDetail = {
  dialog: { id: 1, channel_kind: 'telegram', persona_id: 1, client_id: 7, ai_paused: false, needs_operator: true },
  client,
  messages: [
    { id: 11, direction: 'in', author: 'client', text: 'Здравствуйте', created_at: '2026-11-11T09:00:00Z', delivery: 'sent' },
  ],
};

function api() {
  return fakeApi({
    'GET /api/personas': { items: [{ id: 1, name: 'Марина' }] },
    'GET /api/dialogs': { items: [row], total: 1 },
    'GET /api/simulations': { items: [{ id: 2, admin: { id: 1, display_name: 'Админ' },
      persona: { id: 1, name: 'Марина' }, title: 'Пробный разговор', message_count: 1,
      stage: 'first_contact', status: 'active', created_at: '2026-11-11T09:00:00Z',
      last_active_at: '2026-11-11T10:00:00Z', closed_at: null }], total: 1 },
    'GET /api/simulations/2': { id: 2, admin: { id: 1, display_name: 'Админ' },
      persona: { id: 1, name: 'Марина' }, title: 'Пробный разговор', message_count: 1,
      stage: 'first_contact', status: 'active', created_at: '2026-11-11T09:00:00Z',
      last_active_at: '2026-11-11T10:00:00Z', closed_at: null, needs: [], next_step: '', notes: '',
      messages: [{ id: 5, role: 'client', text: 'Тестовое приветствие', model: null,
        created_at: '2026-11-11T10:00:00Z', guard: null }] },
    'GET /api/dialogs/1': detail,
    'POST /api/dialogs/1/read': null,
    'POST /api/dialogs/1/messages': (req) => ({
      id: 12,
      direction: 'out',
      author: 'operator',
      text: (req.body as { text: string }).text,
      created_at: '2026-11-11T09:05:00Z',
      delivery: 'queued',
    }),
  });
}

describe('InboxPage', () => {
  it('shows marked test chats without opening a real channel', async () => {
    api();
    await renderApp('/inbox?d=-2');
    expect(await screen.findByText('Тестовое приветствие')).toBeInTheDocument();
    expect(screen.getByText(/Это симуляция, не канал клиента/)).toBeInTheDocument();
    expect(screen.getByText('Тест')).toBeInTheDocument();
  });
  it('lists dialogs with unread count and the needs-operator badge', async () => {
    api();
    await renderApp('/inbox');
    expect(await screen.findByText('Анна Клиентова')).toBeInTheDocument();
    expect(screen.getByLabelText('2 непрочитанных')).toBeInTheDocument();
    expect(screen.getByText('нужен оператор')).toBeInTheDocument();
  });

  it('opens a dialog, marks it read and sends an operator reply with CSRF', async () => {
    const log = api();
    await renderApp('/inbox?d=1');
    const box = await screen.findByRole('textbox', { name: 'Сообщение клиенту' });
    await waitFor(() => expect(log.some((r) => r.method === 'POST' && r.path === '/api/dialogs/1/read')).toBe(true));

    await userEvent.type(box, 'Добрый день{Enter}');

    await waitFor(() => {
      const sent = log.find((r) => r.method === 'POST' && r.path === '/api/dialogs/1/messages');
      expect(sent?.body).toMatchObject({ text: 'Добрый день' });
      expect(typeof (sent?.body as { request_id?: string })?.request_id).toBe('string');
      expect(sent?.headers['X-CSRF-Token']).toBe(CSRF);
    });
    expect(await screen.findByText('Добрый день')).toBeInTheDocument();
    // Operator reply pauses the AI and clears «needs operator».
    await waitFor(() => expect(screen.getByRole('switch')).not.toBeChecked());
    expect(screen.queryByRole('button', { name: 'Обработано' })).not.toBeInTheDocument();
  });

  it('sends the unread-only filter to the API', async () => {
    const log = api();
    await renderApp('/inbox');
    await screen.findByText('Анна Клиентова');
    await userEvent.click(screen.getByRole('button', { name: 'Фильтры' }));
    await userEvent.click(await screen.findByRole('switch', { name: 'Только непрочитанные' }));
    await waitFor(() =>
      expect(log.some((r) => r.path === '/api/dialogs' && r.query.get('unread_only') === 'true')).toBe(true),
    );
  });
});
