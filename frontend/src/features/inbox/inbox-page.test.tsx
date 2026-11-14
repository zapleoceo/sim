import userEvent from '@testing-library/user-event';
import { waitFor, within } from '@testing-library/react';
import type { Client, DialogDetail, DialogRow } from '@/lib/models';
import { fakeApi, type FakeReply } from '@/test/fake-api';
import { CSRF, renderApp, screen, testUser } from '@/test/render-app';
import { PHONE_WIDTH, stubViewportWidth } from '@/test/viewport';

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

function api(extra: Record<string, FakeReply> = {}) {
  return fakeApi({
    'GET /api/personas': { items: [{ id: 1, name: 'Марина', is_active: true }] },
    'GET /api/channels': { items: [{ id: 4, kind: 'telegram', name: 'Марина TG',
      persona_id: 1, status: 'active', account_label: '@marina', last_error: null, last_activity_at: null }] },
    'GET /api/dialogs': { items: [row], total: 1 },
    'GET /api/simulations': { items: [{ id: 2, admin: { id: 1, display_name: 'Админ' },
      persona: { id: 1, name: 'Марина' }, title: 'Пробный разговор', message_count: 1,
      stage: 'first_contact', status: 'active', created_at: '2026-11-11T09:00:00Z',
      last_active_at: '2026-11-11T10:00:00Z', closed_at: null }], total: 1 },
    'GET /api/simulations/2': { id: 2, admin: { id: 1, display_name: 'Админ' },
      persona: { id: 1, name: 'Марина' }, title: 'Пробный разговор', message_count: 1,
      stage: 'first_contact', status: 'active', created_at: '2026-11-11T09:00:00Z',
      last_active_at: '2026-11-11T10:00:00Z', closed_at: null, needs: [], next_step: '', notes: '', can_continue: true, pending: false,
      messages: [{ id: 5, role: 'client', text: 'Тестовое приветствие', model: null,
        created_at: '2026-11-11T10:00:00Z', guard: null }] },
    'GET /api/dialogs/1': detail,
    'POST /api/simulations/2/messages': { pending: true },
    'POST /api/simulations': { id: 3, admin: { id: 1, display_name: 'Админ' },
      persona: { id: 1, name: 'Марина' }, title: 'Новый тест', message_count: 0,
      stage: 'first_contact', status: 'active', created_at: '2026-11-11T10:00:00Z',
      last_active_at: '2026-11-11T10:00:00Z', closed_at: null, needs: [], next_step: '',
      notes: '', can_continue: true, pending: false, messages: [] },
    'POST /api/dialogs/1/read': null,
    'POST /api/dialogs/1/messages': (req) => ({
      id: 12,
      direction: 'out',
      author: 'operator',
      text: (req.body as { text: string }).text,
      created_at: '2026-11-11T09:05:00Z',
      delivery: 'queued',
    }),
    ...extra,
  });
}

describe('InboxPage', () => {
  it('shows marked test chats without opening a real channel', async () => {
    api();
    await renderApp('/inbox?d=-2');
    expect(await screen.findByText('Тестовое приветствие')).toBeInTheDocument();
    expect(screen.getByText(/Это симуляция: сообщения остаются здесь/)).toBeInTheDocument();
    expect(screen.getByText('Тест')).toBeInTheDocument();
  });
  it('continues a test chat through the isolated simulation queue', async () => {
    const log = api();
    await renderApp('/inbox?d=-2');
    const box = await screen.findByRole('textbox', { name: 'Сообщение тестовому клиенту' });
    await userEvent.type(box, 'Проверка ответа');
    await userEvent.click(screen.getByRole('button', { name: 'Продолжить тест' }));
    await waitFor(() => {
      const sent = log.find((r) => r.method === 'POST' && r.path === '/api/simulations/2/messages');
      expect(sent?.body).toMatchObject({ text: 'Проверка ответа' });
      expect(typeof (sent?.body as { request_id?: string })?.request_id).toBe('string');
      expect(sent?.headers['X-CSRF-Token']).toBe(CSRF);
    });
  });

  it('lists dialogs with unread count and the needs-operator badge', async () => {
    api();
    await renderApp('/inbox');
    expect(await screen.findByText('Анна Клиентова')).toBeInTheDocument();
    expect(screen.getByLabelText('2 непрочитанных')).toBeInTheDocument();
    expect(screen.getByText('нужен оператор')).toBeInTheDocument();
    expect(screen.getByText(/Требуется ответ менеджера/)).toBeInTheDocument();
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

describe('InboxPage: ИИ на паузе', () => {
  const waiting = [
    { id: 11, direction: 'in', author: 'client', text: 'Пришли фото', created_at: '2026-11-11T09:00:00Z', delivery: 'sent' },
    { id: 12, direction: 'in', author: 'client', text: 'Пришли кружочек', created_at: '2026-11-11T09:01:00Z', delivery: 'sent' },
  ] as DialogDetail['messages'];
  const pausedDetail = (paused: boolean): DialogDetail => ({
    ...detail,
    dialog: { ...detail.dialog, ai_paused: paused, needs_operator: false,
      unanswered: { count: 2, since: '2026-11-11T09:00:00Z', message_ids: [11, 12] } },
    messages: waiting,
  });

  /** The server state: paused until POST /ai {paused: false}, which reports the recovery. */
  function pausedApi(status: 'queued' | 'stale' = 'queued') {
    let paused = true;
    return api({
      'GET /api/dialogs/1': () => pausedDetail(paused),
      'POST /api/dialogs/1/ai': (req) => {
        paused = (req.body as { paused: boolean }).paused;
        return { id: 1, ai_paused: paused, needs_operator: false, recovery: { status, messages: 2 } };
      },
    });
  }

  it('shows that the AI is off, the unanswered messages and the note in the log', async () => {
    pausedApi();
    await renderApp('/inbox?d=1');
    const banner = await screen.findByRole('status', { name: 'ИИ выключен' });
    expect(banner).toHaveTextContent('ИИ выключен. Персонаж не отвечает в этом диалоге. Без ответа: 2 сообщения клиента.');
    expect(within(banner).getByRole('button', { name: 'Включить ИИ' })).toBeInTheDocument();
    expect(within(screen.getByRole('log', { name: 'Сообщения' }))
      .getByText('Ответ не создан: диалог на паузе ИИ · без ответа: 2')).toBeInTheDocument();
    expect(screen.getByRole('switch')).not.toBeChecked();
  });

  it('«Включить ИИ» lifts the pause through the same toggle and reports the queued reply', async () => {
    const log = pausedApi();
    await renderApp('/inbox?d=1');
    await userEvent.click(await screen.findByRole('button', { name: 'Включить ИИ' }));
    await waitFor(() => {
      const sent = log.find((r) => r.method === 'POST' && r.path === '/api/dialogs/1/ai');
      expect(sent?.body).toEqual({ paused: false });
      expect(sent?.headers['X-CSRF-Token']).toBe(CSRF);
    });
    expect(await screen.findByText('ИИ включён. Персонаж ответит одним сообщением, без ответа было: 2 сообщения.')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('status', { name: 'ИИ выключен' })).not.toBeInTheDocument());
    expect(screen.getByRole('switch')).toBeChecked();
    expect(screen.queryByText(/Ответ не создан/)).not.toBeInTheDocument();
  });

  it('says that stale messages are left to the operator', async () => {
    pausedApi('stale');
    await renderApp('/inbox?d=1');
    await userEvent.click(await screen.findByRole('button', { name: 'Включить ИИ' }));
    expect(await screen.findByText(/они устарели: персонаж сам не ответит, ответьте вручную/)).toBeInTheDocument();
  });

  it('has no pause banner while the AI is on', async () => {
    api();
    await renderApp('/inbox?d=1');
    await screen.findByRole('textbox', { name: 'Сообщение клиенту' });
    expect(screen.queryByRole('status', { name: 'ИИ выключен' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Включить ИИ' })).not.toBeInTheDocument();
  });
});

describe('InboxPage «Очистить чат»', () => {
  /** The server state: the dialog has its history until the DELETE. */
  function clearableApi() {
    let cleared = false;
    const emptied = (): DialogDetail => ({ ...detail, dialog: { ...detail.dialog, needs_operator: false }, messages: [] });
    return api({
      'GET /api/dialogs/1': () => (cleared ? emptied() : detail),
      'DELETE /api/dialogs/1/messages': () => {
        cleared = true;
        return { ...emptied(), deleted_messages: 1 };
      },
    });
  }

  it('asks first, then wipes the history with CSRF and shows the empty dialog without a reload', async () => {
    const log = clearableApi();
    await renderApp('/inbox?d=1');
    const chatLog = await screen.findByRole('log', { name: 'Сообщения' });
    expect(within(chatLog).getByText('Здравствуйте')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Очистить чат' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(dialog).toHaveTextContent('без возможности восстановления');
    expect(dialog).toHaveTextContent(/В Telegram .*сообщения останутся/);
    await userEvent.click(within(dialog).getByRole('button', { name: 'Очистить чат' }));

    await waitFor(() => {
      const sent = log.find((r) => r.method === 'DELETE' && r.path === '/api/dialogs/1/messages');
      expect(sent?.headers['X-CSRF-Token']).toBe(CSRF);
    });
    expect(await screen.findByText('Сообщений пока нет')).toBeInTheDocument();
    expect(within(screen.getByRole('log', { name: 'Сообщения' })).queryByText('Здравствуйте')).not.toBeInTheDocument();
  });

  it('does nothing when the owner cancels', async () => {
    const log = clearableApi();
    await renderApp('/inbox?d=1');
    await userEvent.click(await screen.findByRole('button', { name: 'Очистить чат' }));
    await userEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Отмена' }));
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
    expect(log.some((r) => r.method === 'DELETE')).toBe(false);
    expect(within(screen.getByRole('log', { name: 'Сообщения' })).getByText('Здравствуйте')).toBeInTheDocument();
  });

  it('is not shown to an operator', async () => {
    clearableApi();
    await renderApp('/inbox?d=1', { user: testUser('operator') });
    await screen.findByRole('textbox', { name: 'Сообщение клиенту' });
    expect(screen.queryByRole('button', { name: 'Очистить чат' })).not.toBeInTheDocument();
  });
});

describe('InboxPage on a phone (380 px)', () => {
  const MENU = 'Действия с диалогом';

  async function openDialog(extra: Record<string, FakeReply> = {}, role: 'owner' | 'operator' = 'owner') {
    stubViewportWidth(PHONE_WIDTH);
    const log = api(extra);
    await renderApp('/inbox?d=1', { user: testUser(role) });
    await screen.findByRole('textbox', { name: 'Сообщение клиенту' });
    return log;
  }

  it('keeps the header to one row: back, name, the AI switch and «⋮»', async () => {
    await openDialog();
    const header = screen.getByRole('button', { name: 'Назад к списку' }).closest('header') as HTMLElement;
    expect(within(header).getByText('Анна Клиентова')).toHaveClass('truncate');
    expect(within(header).getByRole('switch', { name: 'ИИ' })).toBeChecked();
    expect(within(header).getByRole('button', { name: MENU })).toBeInTheDocument();
    // Everything else is inside the menu, not in the header row or under it.
    expect(within(header).getAllByRole('button')).toHaveLength(2);
    expect(screen.queryByRole('button', { name: 'Обработано' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Очистить чат' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Лента клиента/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  });

  it('«⋮» opens the status badges, the stage and every action of the desktop header', async () => {
    await openDialog();
    await userEvent.click(screen.getByRole('button', { name: MENU }));
    const menu = await screen.findByRole('menu');
    expect(within(menu).getByText('Активен')).toBeInTheDocument();
    expect(within(menu).getByText('нужен оператор')).toBeInTheDocument();
    expect(within(menu).getByRole('img', { name: 'Telegram (бот)' })).toBeInTheDocument();
    expect(within(menu).getByRole('menuitem', { name: 'Обработано' })).toBeInTheDocument();
    expect(within(menu).getByRole('menuitem', { name: /Лента клиента/ })).toHaveAttribute('href', '/clients/7#conversation');
    expect(within(menu).getByRole('menuitem', { name: 'Карточка клиента' })).toHaveAttribute('href', '/clients/7');
    expect(within(menu).getByRole('menuitem', { name: 'Очистить чат' })).toBeInTheDocument();
    expect(within(menu).getByRole('menuitemradio', { name: 'Вовлечён' })).toBeChecked();
    expect(within(menu).getByRole('menuitemradio', { name: 'Интерес' })).not.toBeChecked();
    // Partner-only stages cannot be set by hand.
    expect(within(menu).queryByRole('menuitemradio', { name: 'Оплата' })).not.toBeInTheDocument();
  });

  it('«Обработано» from the menu clears the operator flag', async () => {
    const log = await openDialog({ 'POST /api/dialogs/1/resolve': { ...detail.dialog, needs_operator: false } });
    await userEvent.click(screen.getByRole('button', { name: MENU }));
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Обработано' }));
    await waitFor(() => {
      const sent = log.find((r) => r.method === 'POST' && r.path === '/api/dialogs/1/resolve');
      expect(sent?.headers['X-CSRF-Token']).toBe(CSRF);
    });
    await waitFor(() => expect(screen.queryByText(/Требуется ответ менеджера/)).not.toBeInTheDocument());
  });

  it('changes the stage from the menu', async () => {
    const log = await openDialog({ 'POST /api/clients/7/stage': { ...client, stage: 'interested' } });
    await userEvent.click(screen.getByRole('button', { name: MENU }));
    await userEvent.click(await screen.findByRole('menuitemradio', { name: 'Интерес' }));
    await waitFor(() => {
      const sent = log.find((r) => r.method === 'POST' && r.path === '/api/clients/7/stage');
      expect(sent?.body).toMatchObject({ stage: 'interested' });
    });
  });

  it('«Очистить чат» from the menu still asks for confirmation', async () => {
    const log = await openDialog();
    await userEvent.click(screen.getByRole('button', { name: MENU }));
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Очистить чат' }));
    expect(await screen.findByRole('alertdialog')).toHaveTextContent('без возможности восстановления');
    expect(log.some((r) => r.method === 'DELETE')).toBe(false);
  });

  it('hides «Очистить чат» from an operator', async () => {
    await openDialog({}, 'operator');
    await userEvent.click(screen.getByRole('button', { name: MENU }));
    const menu = await screen.findByRole('menu');
    expect(within(menu).getByRole('menuitem', { name: 'Карточка клиента' })).toBeInTheDocument();
    expect(within(menu).queryByRole('menuitem', { name: 'Очистить чат' })).not.toBeInTheDocument();
  });

  it('shows a short operator banner that can be closed', async () => {
    await openDialog();
    expect(screen.getByText('Требуется ответ менеджера. ИИ остановлен.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Скрыть уведомление' }));
    expect(screen.queryByText(/Требуется ответ менеджера/)).not.toBeInTheDocument();
    // The flag is still reachable: the menu keeps the badge and the action.
    await userEvent.click(screen.getByRole('button', { name: MENU }));
    expect(within(await screen.findByRole('menu')).getByText('нужен оператор')).toBeInTheDocument();
  });

  it('keeps the composer and sizes the screen from the visible viewport', async () => {
    await openDialog();
    expect(screen.getByRole('button', { name: 'Прикрепить файл' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Отправить' })).toBeInTheDocument();
    const fit = document.querySelector<HTMLElement>('[data-fit-viewport]');
    expect(fit).toContainElement(screen.getByRole('textbox', { name: 'Сообщение клиенту' }));
    expect(fit?.className).toContain('var(--app-vh,100dvh)');
    expect(fit?.className).toContain('var(--bottom-nav)');
  });

  it('gives a test chat a way back to the list', async () => {
    stubViewportWidth(PHONE_WIDTH);
    api();
    const { router } = await renderApp('/inbox?d=-2');
    await userEvent.click(await screen.findByRole('button', { name: 'Назад к списку' }));
    await waitFor(() => expect(router.state.location.search).not.toHaveProperty('d'));
  });
});

describe('InboxPage on a desktop', () => {
  it('keeps the inline header: no «⋮», the full banner and the client feed link', async () => {
    api();
    await renderApp('/inbox?d=1');
    await screen.findByRole('textbox', { name: 'Сообщение клиенту' });
    expect(screen.queryByRole('button', { name: 'Действия с диалогом' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Обработано' })).toBeInTheDocument();
    expect(screen.getByText(/ИИ остановлен; проверьте переписку/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Лента клиента/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Скрыть уведомление' })).not.toBeInTheDocument();
  });
});
