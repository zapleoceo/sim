import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { Dialog, DialogDetail, DialogFilter, DialogRow, ListResponse, Message } from '../models';
import { DEFAULT_PAGE, PageRequest, toParams } from './http-params';

@Injectable({ providedIn: 'root' })
export class DialogsApi {
  private readonly http = inject(HttpClient);

  list(filter: DialogFilter, silent = false, page: PageRequest = DEFAULT_PAGE): Observable<ListResponse<DialogRow>> {
    return this.http.get<ListResponse<DialogRow>>('/api/dialogs', {
      params: toParams({ ...filter, ...page }),
      headers: silent ? { 'X-Silent': '1' } : {},
    });
  }
  get(id: number, silent = false): Observable<DialogDetail> {
    return this.http.get<DialogDetail>(`/api/dialogs/${id}`, { headers: silent ? { 'X-Silent': '1' } : {} });
  }
  send(id: number, text: string): Observable<Message> {
    return this.http.post<Message>(`/api/dialogs/${id}/messages`, { text });
  }
  setAiPaused(id: number, paused: boolean): Observable<Dialog> {
    return this.http.post<Dialog>(`/api/dialogs/${id}/ai`, { paused });
  }
  /** Оператор разобрался: снять отметку «нужен оператор» (пауза ИИ не меняется). */
  resolve(id: number): Observable<Dialog> {
    return this.http.post<Dialog>(`/api/dialogs/${id}/resolve`, {});
  }
  markRead(id: number): Observable<void> {
    return this.http.post<void>(`/api/dialogs/${id}/read`, {}, { headers: { 'X-Silent': '1' } });
  }
}
