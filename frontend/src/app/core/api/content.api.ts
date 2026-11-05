import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';
import { ContentIn, ContentPost, ListResponse } from '../models';

@Injectable({ providedIn: 'root' })
export class ContentApi {
  private readonly http = inject(HttpClient);

  list(): Observable<ContentPost[]> {
    return this.http.get<ListResponse<ContentPost>>('/api/content').pipe(map((r) => r.items));
  }
  create(body: ContentIn): Observable<ContentPost> {
    return this.http.post<ContentPost>('/api/content', body);
  }
  update(id: number, body: ContentIn): Observable<ContentPost> {
    return this.http.put<ContentPost>(`/api/content/${id}`, body);
  }
  remove(id: number): Observable<void> {
    return this.http.delete<void>(`/api/content/${id}`);
  }
  approve(id: number): Observable<ContentPost> {
    return this.http.post<ContentPost>(`/api/content/${id}/approve`, {});
  }
  generateCaption(id: number): Observable<{ caption: string }> {
    return this.http.post<{ caption: string }>(`/api/content/${id}/generate-caption`, {});
  }
}
