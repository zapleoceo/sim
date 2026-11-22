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
