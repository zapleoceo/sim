import type { Dialog, DialogDetail, DialogFilter, DialogRow, ListResponse, Message } from '../models';
import { DEFAULT_PAGE, http, type PageRequest } from './http';

export const dialogsApi = {
  list: (filter: DialogFilter, page: PageRequest = DEFAULT_PAGE, silent = false) =>
    http.get<ListResponse<DialogRow>>('/api/dialogs', { params: { ...filter, ...page }, silent }),
  get: (id: number, silent = false) => http.get<DialogDetail>(`/api/dialogs/${id}`, { silent }),
  send: (id: number, text: string) => http.post<Message>(`/api/dialogs/${id}/messages`, { text }),
  setAiPaused: (id: number, paused: boolean) => http.post<Dialog>(`/api/dialogs/${id}/ai`, { paused }),
  /** Operator handled it: clears «needs operator» (the AI pause is unchanged). */
  resolve: (id: number) => http.post<Dialog>(`/api/dialogs/${id}/resolve`, {}),
  markRead: (id: number) => http.post<void>(`/api/dialogs/${id}/read`, {}, { silent: true }),
};
