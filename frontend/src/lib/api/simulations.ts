import type { ListResponse, SimBranchDetail, SimBranchRow } from '../models';
import { DEFAULT_PAGE, http, type PageRequest } from './http';

export const simulationsApi = {
  list: (personaId: number | null = null, page: PageRequest = DEFAULT_PAGE) =>
    http.get<ListResponse<SimBranchRow>>('/api/simulations', { params: { persona_id: personaId, ...page } }),
  get: (id: number) => http.get<SimBranchDetail>(`/api/simulations/${id}`),
  start: (personaId: number) => http.post<SimBranchDetail>('/api/simulations', { persona_id: personaId }),
  send: (id: number, text: string, requestId: string) =>
    http.post<{ pending: boolean }>(`/api/simulations/${id}/messages`, { text, request_id: requestId }),
};
