import type { ClearedDialog, Dialog, DialogDetail, DialogFilter, DialogRow, ListResponse, Message } from '../models';
import { DEFAULT_PAGE, http, type PageRequest } from './http';

export const dialogsApi = {
  list: (filter: DialogFilter, page: PageRequest = DEFAULT_PAGE, silent = false) =>
    http.get<ListResponse<DialogRow>>('/api/dialogs', { params: { ...filter, ...page }, silent }),
  get: (id: number, silent = false) => http.get<DialogDetail>(`/api/dialogs/${id}`, { silent }),
  send: (id: number, text: string, requestId?: string) =>
    http.post<Message>(`/api/dialogs/${id}/messages`, { text, request_id: requestId }),
  sendVoice: (id: number, audio: Blob, filename: string, requestId: string) => {
    const form = new FormData();
    form.append("file", audio, filename);
    form.append("request_id", requestId);
    return http.post<Message>(`/api/dialogs/${id}/voice`, form);
  },
  sendAttachment: (id: number, file: File, caption: string, requestId: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("caption", caption);
    form.append("request_id", requestId);
    return http.post<Message>(`/api/dialogs/${id}/attachments`, form);
  },
  setAiPaused: (id: number, paused: boolean) => http.post<Dialog>(`/api/dialogs/${id}/ai`, { paused }),
  /** Operator handled it: clears «needs operator» (the AI pause is unchanged). */
  resolve: (id: number) => http.post<Dialog>(`/api/dialogs/${id}/resolve`, {}),
  markRead: (id: number) => http.post<void>(`/api/dialogs/${id}/read`, {}, { silent: true }),
  /** Owner only, irreversible: wipes the dialog's history in SIM (not in the messenger). */
  clearChat: (id: number) => http.delete<ClearedDialog>(`/api/dialogs/${id}/messages`),
};
