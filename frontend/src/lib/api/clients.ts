import type { Client, ClientCard, ClientFilter, ClientPatch, FunnelResponse, ListResponse, StageChange } from '../models';
import { DEFAULT_PAGE, http, type PageRequest } from './http';

export const clientsApi = {
  list: (filter: Partial<ClientFilter>, page: PageRequest = DEFAULT_PAGE) =>
    http.get<ListResponse<Client>>('/api/clients', { params: { ...filter, ...page } }),
  /** Silent: the client page renders «not found» / retry itself. */
  get: (id: number) => http.get<ClientCard>(`/api/clients/${id}`, { silent: true }),
  patch: (id: number, body: ClientPatch) => http.patch<Client>(`/api/clients/${id}`, body),
  changeStage: (id: number, body: StageChange) => http.post<Client>(`/api/clients/${id}/stage`, body),
  funnel: (personaId: number | null) => http.get<FunnelResponse>('/api/funnel', { params: { persona_id: personaId } }),
};
