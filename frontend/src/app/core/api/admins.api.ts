import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';
import { AdminCreate, AdminPatch, AdminUser, ListResponse } from '../models';

@Injectable({ providedIn: 'root' })
export class AdminsApi {
  private readonly http = inject(HttpClient);

  list(): Observable<AdminUser[]> {
    return this.http.get<ListResponse<AdminUser>>('/api/admins').pipe(map((r) => r.items));
  }
  create(body: AdminCreate): Observable<AdminUser> {
    return this.http.post<AdminUser>('/api/admins', body);
  }
  update(id: number, body: AdminPatch): Observable<AdminUser> {
    return this.http.patch<AdminUser>(`/api/admins/${id}`, body);
  }
  remove(id: number): Observable<void> {
    return this.http.delete<void>(`/api/admins/${id}`);
  }
}
