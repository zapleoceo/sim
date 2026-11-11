import type { ContentIn, ContentPost, ListResponse } from '../models';
import { http } from './http';

export const contentApi = {
  list: () => http.get<ListResponse<ContentPost>>('/api/content').then((r) => r.items),
  create: (body: ContentIn) => http.post<ContentPost>('/api/content', body),
  update: (id: number, body: ContentIn) => http.put<ContentPost>(`/api/content/${id}`, body),
  remove: (id: number) => http.delete(`/api/content/${id}`),
  approve: (id: number) => http.post<ContentPost>(`/api/content/${id}/approve`, {}),
  generateCaption: (id: number) => http.post<{ caption: string }>(`/api/content/${id}/generate-caption`, {}),
};
