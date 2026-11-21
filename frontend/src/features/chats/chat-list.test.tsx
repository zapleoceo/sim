import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { ChatRow, PersonaRow } from '@/lib/types';
import { fakeApi } from '@/test/fake-api';
import { renderWithApp } from '@/test/render';
import { ChatList } from './chat-list';

const row = (id: number, persona: { id: number; name: string }): ChatRow => ({
  id, client: { id, name: `Клиент ${id}`, username: null, ai_enabled: true },
  persona: { ...persona, avatar_media_id: null }, ai_enabled: true, attention_instant: false, no_refusals: false, needs_operator: false, unread_count: 0,
  persona_status: {
    code: 'reply_later', tone: 'wait', title: `Прочитала, ответит около 20:4${id}`, detail: 'после созвона',
    until: '2026-11-21T19:40:00Z', local_until: `20:4${id}`,
  },
  last_message_at: '2026-11-21T10:00:00Z', last_message: { kind: 'text', direction: 'in', author: 'client', text: 'привет' },
});

const persona = (id: number, name: string): PersonaRow => ({
  id, name, avatar_media_id: null, is_active: true, ai_enabled: true, leads_count: 1, channels: [],
});

describe('ChatList with many personas', () => {
  it('names the persona of each chat and filters by persona', async () => {
    const log = fakeApi({
      'GET /api/personas': { items: [persona(1, 'Марина'), persona(2, 'Оля')] },
      'GET /api/chats': (req) =>
        ({ items: req.query.get('persona_id') === '2' ? [row(2, { id: 2, name: 'Оля' })] : [row(1, { id: 1, name: 'Марина' }), row(2, { id: 2, name: 'Оля' })] }),
    });
    await renderWithApp(<ChatList activeId={null} />);

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
    await renderWithApp(<ChatList activeId={null} />);
    await screen.findByRole('list', { name: 'Чаты' });
    expect(screen.queryByRole('combobox', { name: 'Персонаж' })).not.toBeInTheDocument();
    expect(screen.queryByText('Марина')).not.toBeInTheDocument();
  });

  it('shows «Статус Марины» under the client name in every row', async () => {
    fakeApi({
      'GET /api/personas': { items: [persona(1, 'Марина')] },
      'GET /api/chats': { items: [row(1, { id: 1, name: 'Марина' }), row(2, { id: 1, name: 'Марина' })] },
    });
    await renderWithApp(<ChatList activeId={null} />);
    const list = await screen.findByRole('list', { name: 'Чаты' });
    const [first, second] = within(list).getAllByRole('listitem');
    expect(within(first).getByText('Прочитала, ответит около 20:41')).toBeInTheDocument();
    expect(within(second).getByText('Прочитала, ответит около 20:42').closest('[data-tone]')).toHaveAttribute('data-tone', 'wait');
  });
});
