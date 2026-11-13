import type { ListResponse, Strategy, StrategyIn } from '../models';
import { http } from './http';

export const strategiesApi = {
  list: () => http.get<ListResponse<Strategy>>('/api/strategies').then((r) => r.items),
  create: (body: StrategyIn) => http.post<Strategy>('/api/strategies', body),
  update: (id: number, body: StrategyIn) => http.put<Strategy>(`/api/strategies/${id}`, body),
  /** Only the scenario text: the API updates just the fields it is sent (the name is required). */
  savePrompt: (strategy: Pick<Strategy, 'id' | 'name'>, prompt: string) =>
    http.put<Strategy>(`/api/strategies/${strategy.id}`, { name: strategy.name, prompt }),
  remove: (id: number) => http.delete(`/api/strategies/${id}`),
};
