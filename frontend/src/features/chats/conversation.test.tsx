import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { ChatDetail, Passport } from '@/lib/types';
import { fakeApi, type FakeReply } from '@/test/fake-api';
import { renderWithApp } from '@/test/render';
import { Conversation } from './conversation';

const PASSPORT: Passport = {
  text: '', name: '', age: null, gender: '', city: '', country: '', timezone: '', occupation: '', stage: 'new',
  temperature: 0, next_step: '', updated_at: null, locked_fields: [],
};

const CHAT: ChatDetail = {
  id: 7,
  client: {
    id: 1, persona_id: 1, name: 'Дима', username: 'dima', phone: null, timezone: null, notes: '', ai_enabled: true,
    passport: PASSPORT, created_at: '2026-11-01T10:00:00Z', last_seen_at: null,
  },
  persona: { id: 1, name: 'Марина', avatar_media_id: null },
  ai_enabled: true, attention_instant: false, no_refusals: false, needs_operator: true, unread_count: 0,
  persona_status: { code: 'answered', tone: 'ok', title: 'Всё отвечено', detail: '', until: null, local_until: null },
  last_message_at: null, last_message: null,
};

function backend(extra: Record<string, FakeReply> = {}) {
  return fakeApi({
    'GET /api/chats/7': CHAT,
    'GET /api/chats/7/status': CHAT.persona_status,
    'GET /api/chats/7/timeline': { items: [] },
    ...extra,
  });
}

describe('Conversation header', () => {
  it('on a phone keeps the flags, the note and «Оператор разобрался» in one menu', async () => {
    const log = backend({
      'POST /api/chats/7/attention-instant': { ...CHAT, attention_instant: true },
      'POST /api/chats/7/operator-done': { ...CHAT, needs_operator: false },
    });
    await renderWithApp(<Conversation chatId={7} />);

    await userEvent.click(await screen.findByRole('button', { name: 'Ещё действия с чатом' }));
    expect(await screen.findByRole('menuitem', { name: 'Заметка' })).toBeInTheDocument();
    expect(screen.getByRole('menuitemcheckbox', { name: 'Без отказов' })).not.toBeChecked();
    await userEvent.click(screen.getByRole('menuitemcheckbox', { name: 'Отвечать сразу' }));
    await waitFor(() => expect(log.find((r) => r.method === 'POST')).toMatchObject({
      path: '/api/chats/7/attention-instant', body: { enabled: true },
    }));

    await userEvent.click(screen.getByRole('button', { name: 'Ещё действия с чатом' }));
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Оператор разобрался' }));
    await waitFor(() => expect(log.some((r) => r.method === 'POST' && r.path === '/api/chats/7/operator-done')).toBe(true));
  });

  it('«ИИ в чате» shows the value being saved', async () => {
    let release: () => void = () => undefined;
    backend({
      'POST /api/chats/7/ai': () => new Promise((resolve) => {
        release = () => resolve({ ...CHAT, ai_enabled: false });
      }),
    });
    await renderWithApp(<Conversation chatId={7} />);
    const ai = await screen.findByRole('switch', { name: 'ИИ в чате' });
    expect(ai).toBeChecked();
    await userEvent.click(ai);
    await waitFor(() => expect(ai).not.toBeChecked());
    expect(ai).toBeDisabled();
    release();
    await waitFor(() => expect(ai).toBeEnabled());
  });
});
