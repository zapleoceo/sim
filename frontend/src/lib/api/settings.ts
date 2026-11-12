import type { AppSettingItem, FixedPrompts, ListResponse } from '../models';
import { http } from './http';

export const settingsApi = {
  list: () => http.get<ListResponse<AppSettingItem>>('/api/settings').then((r) => r.items),
  /** The only non-editable texts: the honesty rule added after the persona and guard prompts. */
  fixed: () =>
    http.get<{ fixed?: FixedPrompts }>('/api/settings').then((r) => r.fixed ?? { persona: '', guard: '' }),
  save: (key: string, value: string | number) =>
    http.put<AppSettingItem>(`/api/settings/${encodeURIComponent(key)}`, { value }),
  /** Back to the code default. */
  reset: (key: string) => http.delete<AppSettingItem>(`/api/settings/${encodeURIComponent(key)}`),
};
