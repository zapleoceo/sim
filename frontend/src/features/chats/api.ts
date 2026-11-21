import { api } from '@/lib/api';
import type { ChatDetail, ChatRow, Items, ReplyRun, TimelineItem } from '@/lib/types';

export const CHATS_PAGE = 50;
export const TIMELINE_PAGE = 50;

export const chatsApi = {
  list: (q: string, before?: string | null) =>
    api.get<Items<ChatRow>>('/chats', { params: { q, limit: CHATS_PAGE, before } }),
  get: (id: number) => api.get<ChatDetail>(`/chats/${id}`),
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
  markRead: (id: number) => api.post<void>(`/chats/${id}/read`),
  addNote: (id: number, text: string) => api.post<TimelineItem>(`/chats/${id}/notes`, { text }),
  operatorDone: (id: number) => api.post<ChatRow>(`/chats/${id}/operator-done`),
  replyRun: (id: number) => api.get<ReplyRun>(`/reply-runs/${id}`),
};
