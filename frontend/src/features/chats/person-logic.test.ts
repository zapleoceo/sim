import { describe, expect, it } from 'vitest';
import type { ChatRow, PersonChat } from '@/lib/types';
import { isPersonOf, needsOperator, personChats, personMessengers, replyChatId, unreadTotal } from './person-logic';

const sibling = (id: number, kind: string, lastClientAt: string | null, patch: Partial<PersonChat> = {}): PersonChat => ({
  id, channel: { id: id * 10, kind, title: '' }, unread_count: 1, last_message_at: '2026-11-28T10:00:00Z',
  last_client_at: lastClientAt, ai_enabled: true, needs_operator: false, ...patch,
});

const row = (patch: Partial<ChatRow> = {}): ChatRow => ({
  id: 7, client: { id: 1, name: 'Дима', username: null, ai_enabled: true }, persona: null,
  channel: { id: 70, kind: 'whatsapp', title: '' }, ai_enabled: true, attention_instant: false, no_refusals: false,
  calls_out: true, needs_operator: false, unread_count: 2,
  persona_status: { code: 'idle', tone: 'ok', title: 'Всё отвечено', detail: '', until: null, local_until: null },
  last_message_at: '2026-11-28T11:00:00Z', last_message: null, ...patch,
});

describe('one person in many messengers', () => {
  const both = row({
    chats: [
      sibling(7, 'whatsapp', '2026-11-28T09:00:00Z'),
      sibling(1, 'telegram_user', '2026-11-28T10:30:00Z', { needs_operator: true }),
    ],
    unread_total: 5,
  });

  it('answers where the client wrote last and lists the messengers most recent first', () => {
    expect(replyChatId(both)).toBe(1);
    expect(personMessengers(both)).toEqual(['whatsapp', 'telegram_user']);
    expect(unreadTotal(both)).toBe(5);
    expect(needsOperator(both)).toBe(true);
    expect(isPersonOf(both, 1)).toBe(true);
    expect(isPersonOf(both, 3)).toBe(false);
  });

  it('a row without chats is a person with one chat', () => {
    const single = row();
    expect(personChats(single).map((c) => c.id)).toEqual([7]);
    expect(replyChatId(single)).toBe(7);
    expect(unreadTotal(single)).toBe(2);
    expect(personMessengers(single)).toEqual(['whatsapp']);
  });
});
