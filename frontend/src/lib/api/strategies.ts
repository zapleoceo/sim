import type { ListResponse, Strategy, StrategyIn } from '../models';
import { http } from './http';

export const strategiesApi = {
  list: () => http.get<ListResponse<Strategy>>('/api/strategies').then((r) => r.items),
  create: (body: StrategyIn) => http.post<Strategy>('/api/strategies', body),
  update: (id: number, body: StrategyIn) => http.put<Strategy>(`/api/strategies/${id}`, body),
  remove: (id: number) => http.delete(`/api/strategies/${id}`),
};
