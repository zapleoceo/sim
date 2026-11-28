import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { ChatRow, PersonaRow } from '@/lib/types';
import { fakeApi } from '@/test/fake-api';
import { renderAtRoute } from '@/test/render';
import { parseChatsSearch } from '@/router';
import { ChatList } from './chat-list';

const row = (id: number, persona: { id: number; name: string }): ChatRow => ({
  id, client: { id, name: `Клиент ${id}`, username: null, ai_enabled: true },
  persona: { ...persona, avatar_media_id: null }, ai_enabled: true, attention_instant: false, no_refusals: false, calls_out: false, needs_operator: false, unread_count: 0,
  persona_status: {
    code: 'reply_later', tone: 'wait', title: `Прочитала, ответит около 20:4${id}`, detail: 'после созвона',
    until: '2026-11-21T19:40:00Z', local_until: `20:4${id}`,
  },
  last_message_at: '2026-11-21T10:00:00Z', last_message: { kind: 'text', direction: 'in', author: 'client', text: 'привет' },
});

const persona = (id: number, name: string): PersonaRow => ({
  id, name, avatar_media_id: null, is_active: true, ai_enabled: true, leads_count: 1, channels: [],
});

const renderChats = (url = '/chats') => renderAtRoute(<ChatList />, { path: '/chats', url, validateSearch: parseChatsSearch });

describe('ChatList filters in the address', () => {
  it('writes the search and the persona to the address and keeps them in every chat link', async () => {
    const log = fakeApi({
      'GET /api/personas': { items: [persona(1, 'Марина'), persona(2, 'Оля')] },
      'GET /api/chats': { items: [row(2, { id: 2, name: 'Оля' })] },
    });
    const { router } = await renderChats();
    await screen.findByRole('option', { name: 'Оля' });

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Персонаж' }), '2');
    await userEvent.type(screen.getByRole('searchbox', { name: 'Поиск по чатам' }), 'Клиент');
    await waitFor(() => expect(router.state.location.search).toEqual({ persona: 2, q: 'Клиент' }));
    await waitFor(() => expect(log.some((r) => r.path === '/api/chats' && r.query.get('q') === 'Клиент'
      && r.query.get('persona_id') === '2')).toBe(true));

    // Opening a chat (on a phone the list then hides) keeps the filters for «назад».
    const link = await within(await screen.findByRole('list', { name: 'Чаты' })).findByRole('link');
    const href = new URL(link.getAttribute('href') ?? '', 'http://localhost');
    expect(href.searchParams.get('id')).toBe('2');
    expect(href.searchParams.get('persona')).toBe('2');
    expect(href.searchParams.get('q')).toContain('Клиент');
  });

  it('opens with the filters of the address and marks the open chat', async () => {
    const log = fakeApi({
      'GET /api/personas': { items: [persona(1, 'Марина'), persona(2, 'Оля')] },
      'GET /api/chats': { items: [row(2, { id: 2, name: 'Оля' })] },
    });
    await renderChats('/chats?id=2&persona=2&q=anna');
    await screen.findByRole('option', { name: 'Оля' });
    expect(screen.getByRole('combobox', { name: 'Персонаж' })).toHaveValue('2');
    expect(screen.getByRole('searchbox', { name: 'Поиск по чатам' })).toHaveValue('anna');
    const list = await screen.findByRole('list', { name: 'Чаты' });
    // The open chat is marked (the router's Link marks it as the current page).
    expect(within(list).getByRole('link')).toHaveAttribute('aria-current');
    expect(log.some((r) => r.path === '/api/chats' && r.query.get('q') === 'anna' && r.query.get('persona_id') === '2')).toBe(true);
  });
});

describe('ChatList with many personas', () => {
  it('names the persona of each chat and filters by persona', async () => {
    const log = fakeApi({
      'GET /api/personas': { items: [persona(1, 'Марина'), persona(2, 'Оля')] },
      'GET /api/chats': (req) =>
        ({ items: req.query.get('persona_id') === '2' ? [row(2, { id: 2, name: 'Оля' })] : [row(1, { id: 1, name: 'Марина' }), row(2, { id: 2, name: 'Оля' })] }),
    });
    await renderChats();

    const list = await screen.findByRole('list', { name: 'Чаты' });
    await within(list).findByText('Марина');
    expect(within(list).getByText('Оля')).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Персонаж' }), '2');
    await waitFor(() => expect(log.some((r) => r.path === '/api/chats' && r.query.get('persona_id') === '2')).toBe(true));
    await waitFor(() => expect(within(screen.getByRole('list', { name: 'Чаты' })).getAllByRole('listitem')).toHaveLength(1));
    // One persona chosen: its name in every row would be noise.
    expect(within(screen.getByRole('list', { name: 'Чаты' })).queryByText('Оля')).not.toBeInTheDocument();
  });

  it('has no persona filter with a single persona', async () => {
    fakeApi({
      'GET /api/personas': { items: [persona(1, 'Марина')] },
      'GET /api/chats': { items: [row(1, { id: 1, name: 'Марина' })] },
    });
    await renderChats();
    await screen.findByRole('list', { name: 'Чаты' });
    expect(screen.queryByRole('combobox', { name: 'Персонаж' })).not.toBeInTheDocument();
    expect(screen.queryByText('Марина')).not.toBeInTheDocument();
  });

  it('marks each chat with the icon of its messenger', async () => {
    fakeApi({
      'GET /api/personas': { items: [persona(1, 'Марина')] },
      'GET /api/chats': {
        items: [
          { ...row(1, { id: 1, name: 'Марина' }), channel: { id: 3, kind: 'instagram', title: 'Марина' } },
          { ...row(2, { id: 1, name: 'Марина' }), channel: { id: 4, kind: 'whatsapp', title: 'Марина' } },
          row(3, { id: 1, name: 'Марина' }),
        ],
      },
    });
    await renderChats();
    const list = await screen.findByRole('list', { name: 'Чаты' });
    const [instagram, whatsapp, old] = within(list).getAllByRole('listitem');
    expect(within(instagram).getByRole('img', { name: 'Instagram' })).toBeInTheDocument();
    expect(within(whatsapp).getByRole('img', { name: 'WhatsApp' })).toBeInTheDocument();
    expect(within(old).queryByRole('img', { name: 'Telegram' })).not.toBeInTheDocument();
  });

  it('shows «Статус Марины» under the client name in every row', async () => {
    fakeApi({
      'GET /api/personas': { items: [persona(1, 'Марина')] },
      'GET /api/chats': { items: [row(1, { id: 1, name: 'Марина' }), row(2, { id: 1, name: 'Марина' })] },
    });
    await renderChats();
    const list = await screen.findByRole('list', { name: 'Чаты' });
    const [first, second] = within(list).getAllByRole('listitem');
    expect(within(first).getByText('Прочитала, ответит около 20:41')).toBeInTheDocument();
    expect(within(second).getByText('Прочитала, ответит около 20:42').closest('[data-tone]')).toHaveAttribute('data-tone', 'wait');
  });
});

describe('ChatList source filter', () => {
  it('filters by several messengers, shows their counters and keeps the choice in the address', async () => {
    const log = fakeApi({
      'GET /api/personas': { items: [persona(1, 'Марина')] },
      'GET /api/chats/sources': { items: [{ kind: 'telegram_user', chats: 6, unread: 0 }, { kind: 'whatsapp', chats: 1, unread: 3 }] },
      'GET /api/chats': { items: [{ ...row(7, { id: 1, name: 'Марина' }), channel: { id: 4, kind: 'whatsapp', title: 'WA' } }] },
    });
    const { router } = await renderChats();
    const filter = await screen.findByRole('group', { name: 'Источник' });
    expect(within(filter).getByRole('button', { name: 'Все' })).toHaveAttribute('aria-pressed', 'true');
    const whatsapp = within(filter).getByRole('button', { name: /WhatsApp/ });
    await within(whatsapp).findByLabelText('чатов: 1');
    expect(within(whatsapp).getByLabelText('непрочитанных: 3')).toBeInTheDocument();
    // Telegram has no unread: no blue dot.
    expect(within(within(filter).getByRole('button', { name: /Telegram/ })).queryByLabelText(/непрочитанных/)).toBeNull();

    await userEvent.click(within(filter).getByRole('button', { name: /WhatsApp/ }));
    await userEvent.click(within(filter).getByRole('button', { name: /Instagram/ }));
    await waitFor(() => expect(router.state.location.search).toEqual({ source: 'whatsapp,instagram' }));
    await waitFor(() => expect(log.some((r) => r.path === '/api/chats' && r.query.get('channel') === 'whatsapp,instagram')).toBe(true));
    expect(within(filter).getByRole('button', { name: /WhatsApp/ })).toHaveAttribute('aria-pressed', 'true');
    expect(within(filter).getByRole('button', { name: 'Все' })).toHaveAttribute('aria-pressed', 'false');

    // The chat link keeps the source for «назад».
    const link = await within(await screen.findByRole('list', { name: 'Чаты' })).findByRole('link');
    expect(new URL(link.getAttribute('href') ?? '', 'http://localhost').searchParams.get('source')).toBe('whatsapp,instagram');

    await userEvent.click(within(filter).getByRole('button', { name: 'Все' }));
    await waitFor(() => expect(router.state.location.search).toEqual({}));
  });

  it('opens with the source of the address', async () => {
    const log = fakeApi({
      'GET /api/personas': { items: [persona(1, 'Марина')] },
      'GET /api/chats': { items: [] },
    });
    await renderChats('/chats?source=instagram');
    expect(await screen.findByText('Чатов в Instagram пока нет')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Instagram/ })).toHaveAttribute('aria-pressed', 'true');
    expect(log.some((r) => r.path === '/api/chats' && r.query.get('channel') === 'instagram')).toBe(true);
  });

  it('an empty filtered list offers to reset the filters; an empty app — to connect a messenger', async () => {
    fakeApi({ 'GET /api/personas': { items: [persona(1, 'Марина')] }, 'GET /api/chats': { items: [] } });
    const { router } = await renderChats('/chats?source=instagram&q=anna');
    expect(await screen.findByText('Ничего не найдено по «anna» в Instagram')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Сбросить фильтры' }));
    await waitFor(() => expect(router.state.location.search).toEqual({}));
    expect(await screen.findByText('Чатов пока нет')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Подключить мессенджер' })).toHaveAttribute('href', '/personas');
  });
});

describe('ChatList as a full-width table', () => {
  const renderTable = (url = '/chats') =>
    renderAtRoute(<ChatList layout="table" />, { path: '/chats', url, validateSearch: parseChatsSearch });

  it('shows messenger, client, last message, status, AI and time in one row and opens the chat on click', async () => {
    fakeApi({
      'GET /api/personas': { items: [persona(1, 'Марина')] },
      'GET /api/chats': {
        items: [{
          ...row(7, { id: 1, name: 'Марина' }),
          client: { id: 7, name: 'Zaporozhets Dmitry', username: 'dima', ai_enabled: false },
          channel: { id: 4, kind: 'whatsapp', title: 'WA' },
          needs_operator: true, unread_count: 2,
        }],
      },
    });
    const { router } = await renderTable('/chats?source=whatsapp');
    const table = await screen.findByRole('table', { name: 'Чаты' });
    const [, dataRow] = within(table).getAllByRole('row');
    const cells = within(dataRow).getAllByRole('cell');
    expect(cells[0]).toHaveTextContent('WhatsApp');
    expect(cells[1]).toHaveTextContent('Zaporozhets Dmitry');
    expect(cells[1]).toHaveTextContent('@dima');
    expect(within(cells[5]).getByLabelText('Непрочитанных: 2')).toBeInTheDocument();
    expect(dataRow).toHaveAttribute('data-unread', 'true');
    expect(cells[2]).toHaveTextContent('привет');
    expect(cells[3]).toHaveTextContent('Прочитала, ответит около 20:47');
    expect(cells[4]).toHaveTextContent('ИИ выключен');
    expect(cells[4]).toHaveTextContent('нужен оператор');

    await userEvent.click(cells[2]);
    await waitFor(() => expect(router.state.location.search).toEqual({ id: 7, source: 'whatsapp' }));
  });
});

describe('ChatList: one row per person', () => {
  const person = (): ChatRow => ({
    ...row(7, { id: 1, name: 'Марина' }),
    channel: { id: 70, kind: 'whatsapp', title: '' },
    unread_count: 1,
    unread_total: 3,
    chats: [
      { id: 7, channel: { id: 70, kind: 'whatsapp', title: '' }, unread_count: 1, last_message_at: '2026-11-28T11:00:00Z',
        last_client_at: '2026-11-28T09:00:00Z', ai_enabled: true, needs_operator: false },
      { id: 3, channel: { id: 30, kind: 'telegram_user', title: '' }, unread_count: 2, last_message_at: '2026-11-28T10:00:00Z',
        last_client_at: '2026-11-28T10:30:00Z', ai_enabled: true, needs_operator: true },
    ],
  });

  it('a card shows every messenger, the total unread and opens the chat the client wrote in last', async () => {
    fakeApi({ 'GET /api/personas': { items: [persona(1, 'Марина')] }, 'GET /api/chats': { items: [person()] } });
    await renderChats('/chats?id=3');
    const list = await screen.findByRole('list', { name: 'Чаты' });
    const [card] = within(list).getAllByRole('listitem');
    expect(within(card).getAllByRole('img', { name: 'Telegram' }).length).toBeGreaterThan(0);
    expect(within(card).getAllByRole('img', { name: 'WhatsApp' }).length).toBeGreaterThan(0);
    expect(within(card).getByLabelText('Непрочитанных: 3')).toBeInTheDocument();
    expect(within(card).getByText('нужен оператор')).toBeInTheDocument();
    const link = within(card).getByRole('link');
    expect(new URL(link.getAttribute('href') ?? '', 'http://localhost').searchParams.get('id')).toBe('3');
    // The open chat (Telegram) is one of the person's: the card is the current one.
    expect(link).toHaveAttribute('aria-current');
  });

  it('a table row names both messengers and opens the reply chat', async () => {
    fakeApi({ 'GET /api/personas': { items: [persona(1, 'Марина')] }, 'GET /api/chats': { items: [person()] } });
    const { router } = await renderAtRoute(<ChatList layout="table" />, { path: '/chats', url: '/chats', validateSearch: parseChatsSearch });
    const table = await screen.findByRole('table', { name: 'Чаты' });
    const [, dataRow] = within(table).getAllByRole('row');
    expect(within(dataRow).getAllByRole('cell')[0]).toHaveTextContent('WhatsApp · Telegram');
    expect(within(dataRow).getByLabelText('Непрочитанных: 3')).toBeInTheDocument();
    await userEvent.click(within(dataRow).getAllByRole('cell')[2]);
    await waitFor(() => expect(router.state.location.search).toEqual({ id: 3 }));
  });
});
