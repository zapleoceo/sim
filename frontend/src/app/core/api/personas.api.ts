import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';
import { BrokerModel, ListResponse, Persona, PersonaIn, PreviewResponse, PreviewTurn } from '../models';

@Injectable({ providedIn: 'root' })
export class PersonasApi {
  private readonly http = inject(HttpClient);

  list(): Observable<Persona[]> {
    return this.http.get<ListResponse<Persona>>('/api/personas').pipe(map((r) => r.items));
  }
  create(body: PersonaIn): Observable<Persona> {
    return this.http.post<Persona>('/api/personas', body);
  }
  update(id: number, body: PersonaIn): Observable<Persona> {
    return this.http.put<Persona>(`/api/personas/${id}`, body);
  }
  remove(id: number): Observable<void> {
    return this.http.delete<void>(`/api/personas/${id}`);
  }
  preview(id: number, message: string, history: PreviewTurn[]): Observable<PreviewResponse> {
    return this.http.post<PreviewResponse>(`/api/personas/${id}/preview`, { message, history });
  }
  models(): Observable<BrokerModel[]> {
    return this.http.get<ListResponse<BrokerModel>>('/api/models').pipe(map((r) => r.items));
  }
}
