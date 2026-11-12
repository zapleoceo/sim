import type {
  BrokerModel, ListResponse, Persona, PersonaIn, PersonaPrompt, PreviewAttachment, PreviewResponse, PreviewTurn,
} from '../models';
import { http } from './http';

export const personasApi = {
  list: () => http.get<ListResponse<Persona>>('/api/personas').then((r) => r.items),
  create: (body: PersonaIn) => http.post<Persona>('/api/personas', body),
  update: (id: number, body: PersonaIn) => http.put<Persona>(`/api/personas/${id}`, body),
  remove: (id: number) => http.delete(`/api/personas/${id}`),
  /** Silent: the preview chat shows the error inline, next to the message. */
  preview: (id: number, message: string, history: PreviewTurn[], voiceInput = false) =>
    http.post<PreviewResponse>(
      `/api/personas/${id}/preview`,
      { message, history, voice_input: voiceInput },
      { silent: true },
    ),
  /** A recorded voice note (`voice`) or an attached file → text the persona reads. */
  attachment: (id: number, file: Blob, filename: string, voice = false) => {
    const form = new FormData();
    form.append('file', file, filename);
    form.append('voice', String(voice));
    return http.post<PreviewAttachment>(`/api/personas/${id}/preview/attachment`, form, { silent: true });
  },
  /** The persona's full system prompt (saved settings), with the fixed honesty rule marked. */
  prompt: (id: number) => http.get<PersonaPrompt>(`/api/personas/${id}/prompt`),
  /** Silent: an unavailable Broker must not toast on every page load. */
  models: () => http.get<ListResponse<BrokerModel>>('/api/models', { silent: true }).then((r) => r.items),
};
