import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  Client, ClientCard, ClientFilter, ClientPatch, FunnelResponse, ListResponse, StageChange,
} from '../models';
import { DEFAULT_PAGE, PageRequest, toParams } from './http-params';

@Injectable({ providedIn: 'root' })
export class ClientsApi {
  private readonly http = inject(HttpClient);

  list(filter: Partial<ClientFilter>, page: PageRequest = DEFAULT_PAGE): Observable<ListResponse<Client>> {
    return this.http.get<ListResponse<Client>>('/api/clients', { params: toParams({ ...filter, ...page }) });
  }
  get(id: number): Observable<ClientCard> {
    return this.http.get<ClientCard>(`/api/clients/${id}`);
  }
  patch(id: number, body: ClientPatch): Observable<Client> {
    return this.http.patch<Client>(`/api/clients/${id}`, body);
  }
  changeStage(id: number, body: StageChange): Observable<Client> {
    return this.http.post<Client>(`/api/clients/${id}/stage`, body);
  }
  funnel(personaId: number | null): Observable<FunnelResponse> {
    return this.http.get<FunnelResponse>('/api/funnel', { params: toParams({ persona_id: personaId }) });
  }
}
