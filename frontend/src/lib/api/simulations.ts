import type { ListResponse, SimBranchDetail, SimBranchRow } from '../models';
import { DEFAULT_PAGE, http, type PageRequest } from './http';

export const simulationsApi = {
  list: (personaId: number | null = null, page: PageRequest = DEFAULT_PAGE) =>
    http.get<ListResponse<SimBranchRow>>('/api/simulations', { params: { persona_id: personaId, ...page } }),
  get: (id: number) => http.get<SimBranchDetail>(`/api/simulations/${id}`),
};
