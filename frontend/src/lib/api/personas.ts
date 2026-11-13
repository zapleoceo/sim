import type {
  BrokerModel, ListResponse, MediaKind, Persona, PersonaIn, PersonaMedia, PersonaPrompt, PreviewAttachment, PreviewContext, PreviewResponse, PreviewTurn, VideoNoteQueued,
} from '../models';
import { http } from './http';

export const personasApi = {
  list: () => http.get<ListResponse<Persona>>('/api/personas').then((r) => r.items),
  create: (body: PersonaIn) => http.post<Persona>('/api/personas', body),
  update: (id: number, body: PersonaIn) => http.put<Persona>(`/api/personas/${id}`, body),
  remove: (id: number) => http.delete(`/api/personas/${id}`),
  /** Silent: the preview chat shows the error inline, next to the message. */
  preview: (id: number, message: string, history: PreviewTurn[], voiceInput = false, context?: PreviewContext) =>
    http.post<PreviewResponse>(
      `/api/personas/${id}/preview`,
      { message, history, voice_input: voiceInput, ...context },
      { silent: true },
    ),
  /** A recorded voice note (`voice`) or an attached file → text the persona reads. */
  attachment: (id: number, file: Blob, filename: string, voice = false) => {
    const form = new FormData();
    form.append('file', file, filename);
    form.append('voice', String(voice));
    return http.post<PreviewAttachment>(`/api/personas/${id}/preview/attachment`, form, { silent: true });
  },
  /** The persona's full system prompt (saved settings), without an appended identity block. */
  prompt: (id: number) => http.get<PersonaPrompt>(`/api/personas/${id}/prompt`),
  /** Silent: an unavailable Broker must not toast on every page load. */
  models: () => http.get<ListResponse<BrokerModel>>('/api/models', { silent: true }).then((r) => r.items),

  /** Owner: queue an AI video note; it goes only to the account from the «Кружочки ИИ» setting. */
  videoNote: (id: number, prompt: string) =>
    http.post<VideoNoteQueued>(`/api/personas/${id}/video-note`, { prompt }, { silent: true }),

  // ---- media gallery ---
  media: {
    list: (personaId: number, kind?: MediaKind) =>
      http.get<ListResponse<PersonaMedia>>(`/api/personas/${personaId}/media`, { params: { kind: kind ?? '' } }).then((r) => r.items),
    upload: (personaId: number, file: File, kind: MediaKind, caption = '', tags = '') => {
      const form = new FormData();
      form.append('file', file);
      form.append('kind', kind);
      form.append('caption', caption);
      form.append('tags', tags);
      return http.post<PersonaMedia>(`/api/personas/${personaId}/media`, form);
    },
    remove: (personaId: number, mediaId: number) =>
      http.delete(`/api/personas/${personaId}/media/${mediaId}`),
  },
};
