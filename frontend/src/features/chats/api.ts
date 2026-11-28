import type { QueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { qk } from '@/lib/query';
import type {
  ChatDetail, ChatRow, ChatSource, Contact, ContactCreate, Items, PersonaStatus, ReplyRun, TimelineItem,
} from '@/lib/types';

export const CHATS_PAGE = 50;
export const TIMELINE_PAGE = 50;

/** The chat header switches and their endpoints. */
export type ChatFlag = 'attention_instant' | 'no_refusals' | 'calls_out';
const FLAG_PATHS: Record<ChatFlag, string> = {
  attention_instant: 'attention-instant',
  no_refusals: 'no-refusals',
  calls_out: 'calls-out',
};

export const chatsApi = {
  /** `personaId` 0 = the chats of every persona; `source` '' = every messenger. */
  list: (q: string, before?: string | null, personaId = 0, source = '') =>
    api.get<Items<ChatRow>>('/chats', {
      params: { q, limit: CHATS_PAGE, before, persona_id: personaId || null, channel: source || null },
    }),
  /** Messengers that have chats, with chat and unread counters (the source filter). */
  sources: (personaId = 0) => api.get<Items<ChatSource>>('/chats/sources', { params: { persona_id: personaId || null } }),
  get: (id: number) => api.get<ChatDetail>(`/chats/${id}`),
  /** «Статус Марины» of one chat, team-only (docs/attention.md). */
  status: (id: number) => api.get<PersonaStatus>(`/chats/${id}/status`),
  /** Newest first; `beforeId` pages back in time. */
  timeline: (id: number, beforeId?: number | null) =>
    api.get<Items<TimelineItem>>(`/chats/${id}/timeline`, { params: { before_id: beforeId, limit: TIMELINE_PAGE } }),
  sendText: (id: number, text: string) => api.post<TimelineItem>(`/chats/${id}/messages`, { text }),
  sendFile: (id: number, file: File, caption: string) => {
    const form = new FormData();
    form.set('file', file);
    form.set('caption', caption);
    return api.post<TimelineItem>(`/chats/${id}/messages`, form);
  },
  setAi: (id: number, enabled: boolean) => api.post<ChatRow>(`/chats/${id}/ai`, { enabled }),
  /** A chat header switch: «Отвечать сразу» (docs/attention.md), «Без отказов» (docs/writing.md)
   * or «Звонит сама» (docs/calls.md). */
  setFlag: (id: number, flag: ChatFlag, enabled: boolean) =>
    api.post<ChatRow>(`/chats/${id}/${FLAG_PATHS[flag]}`, { enabled }),
  /** Planned contacts of the chat, newest first (≤50). */
  contacts: (id: number) => api.get<Items<Contact>>(`/chats/${id}/contacts`),
  planContact: (id: number, body: ContactCreate) => api.post<Contact>(`/chats/${id}/contacts`, body),
  cancelContact: (id: number, contactId: number) => api.post<Contact>(`/chats/${id}/contacts/${contactId}/cancel`),
  markRead: (id: number) => api.post<void>(`/chats/${id}/read`),
  addNote: (id: number, text: string) => api.post<TimelineItem>(`/chats/${id}/notes`, { text }),
  operatorDone: (id: number) => api.post<ChatRow>(`/chats/${id}/operator-done`),
  replyRun: (id: number) => api.get<ReplyRun>(`/reply-runs/${id}`),
};

/**
 * After the operator changed a chat (AI, a header switch, «Оператор разобрался»): re-read its
 * header, «Статус Марины», timeline and the chat lists, without waiting for the live event.
 */
export function invalidateChat(queryClient: QueryClient, chatId: number): void {
  for (const queryKey of [qk.chat(chatId), qk.chatStatus(chatId), qk.timeline(chatId), qk.chatLists]) {
    void queryClient.invalidateQueries({ queryKey });
  }
}
