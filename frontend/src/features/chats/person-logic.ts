import type { ChatRow, PersonChat } from '@/lib/types';

/**
 * One person as one persona sees them: the client's chats with her in every messenger
 * (`ChatRow.chats`, docs/frontend.md «Одна лента на клиента»). Pure helpers over a chat row.
 */

/** Every chat of the person, most recent first; a row without `chats` is a person with one chat. */
export function personChats(row: ChatRow): PersonChat[] {
  if (row.chats?.length) return row.chats;
  return [{
    id: row.id, channel: row.channel ?? null, unread_count: row.unread_count, last_message_at: row.last_message_at,
    last_client_at: null, ai_enabled: row.ai_enabled, needs_operator: row.needs_operator,
  }];
}

/** The person's messengers, each once, most recent first. */
export function personMessengers(row: ChatRow): string[] {
  return [...new Set(personChats(row).map((c) => c.channel?.kind).filter((k): k is string => Boolean(k)))];
}

export function unreadTotal(row: ChatRow): number {
  return row.unread_total ?? row.unread_count;
}

export function needsOperator(row: ChatRow): boolean {
  return personChats(row).some((c) => c.needs_operator);
}

/**
 * The chat a click on the person opens and the operator answers in by default: where the client
 * wrote last; without that, the most recent chat.
 */
export function replyChatId(row: ChatRow): number {
  let best: PersonChat | null = null;
  for (const chat of personChats(row)) {
    if (chat.last_client_at && (!best?.last_client_at || chat.last_client_at > best.last_client_at)) best = chat;
  }
  return best?.id ?? row.id;
}

/** Is the open chat one of this person's chats (the row is marked current)? */
export function isPersonOf(row: ChatRow, chatId: number | undefined): boolean {
  return chatId !== undefined && personChats(row).some((c) => c.id === chatId);
}
