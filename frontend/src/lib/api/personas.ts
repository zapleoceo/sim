import type { BrokerModel, ListResponse, Persona, PersonaIn, PreviewResponse, PreviewTurn } from '../models';
import { http } from './http';

export const personasApi = {
  list: () => http.get<ListResponse<Persona>>('/api/personas').then((r) => r.items),
  create: (body: PersonaIn) => http.post<Persona>('/api/personas', body),
  update: (id: number, body: PersonaIn) => http.put<Persona>(`/api/personas/${id}`, body),
  remove: (id: number) => http.delete(`/api/personas/${id}`),
  /** Silent: the preview chat shows the error inline, next to the message. */
  preview: (id: number, message: string, history: PreviewTurn[]) =>
    http.post<PreviewResponse>(`/api/personas/${id}/preview`, { message, history }, { silent: true }),
  /** Silent: an unavailable Broker must not toast on every page load. */
  models: () => http.get<ListResponse<BrokerModel>>('/api/models', { silent: true }).then((r) => r.items),
};
