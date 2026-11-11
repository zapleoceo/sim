import type { AdminCreate, AdminPatch, AdminUser, ListResponse } from '../models';
import { http } from './http';

export const adminsApi = {
  list: () => http.get<ListResponse<AdminUser>>('/api/admins').then((r) => r.items),
  create: (body: AdminCreate) => http.post<AdminUser>('/api/admins', body),
  update: (id: number, body: AdminPatch) => http.patch<AdminUser>(`/api/admins/${id}`, body),
  remove: (id: number) => http.delete(`/api/admins/${id}`),
};
