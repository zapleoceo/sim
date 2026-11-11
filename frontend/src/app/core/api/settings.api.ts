import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';
import { AppSettingItem, ListResponse } from '../models';

@Injectable({ providedIn: 'root' })
export class SettingsApi {
  private readonly http = inject(HttpClient);

  list(): Observable<AppSettingItem[]> {
    return this.http.get<ListResponse<AppSettingItem>>('/api/settings').pipe(map((r) => r.items));
  }
  save(key: string, value: string | number): Observable<AppSettingItem> {
    return this.http.put<AppSettingItem>(`/api/settings/${key}`, { value });
  }
  /** Вернуть значение по умолчанию из кода. */
  reset(key: string): Observable<AppSettingItem> {
    return this.http.delete<AppSettingItem>(`/api/settings/${key}`);
  }
}
