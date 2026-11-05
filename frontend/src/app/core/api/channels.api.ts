import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';
import { Channel, ChannelIn, ConnectState, ListResponse } from '../models';

export interface ConnectBody {
  bot_token?: string;
  username?: string;
  password?: string;
  proxy?: string;
}

@Injectable({ providedIn: 'root' })
export class ChannelsApi {
  private readonly http = inject(HttpClient);

  list(): Observable<Channel[]> {
    return this.http.get<ListResponse<Channel>>('/api/channels').pipe(map((r) => r.items));
  }
  create(body: ChannelIn): Observable<Channel> {
    return this.http.post<Channel>('/api/channels', body);
  }
  remove(id: number): Observable<void> {
    return this.http.delete<void>(`/api/channels/${id}`);
  }
  connect(id: number, body: ConnectBody): Observable<ConnectState> {
    return this.http.post<ConnectState>(`/api/channels/${id}/connect`, body);
  }
  verify(id: number, code: string): Observable<ConnectState> {
    return this.http.post<ConnectState>(`/api/channels/${id}/verify`, { code });
  }
  state(id: number): Observable<ConnectState> {
    return this.http.get<ConnectState>(`/api/channels/${id}/state`, { headers: { 'X-Silent': '1' } });
  }
}
