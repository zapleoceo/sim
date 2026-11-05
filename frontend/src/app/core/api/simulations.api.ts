import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ListResponse, SimBranchDetail, SimBranchRow } from '../models';
import { toParams } from './http-params';

@Injectable({ providedIn: 'root' })
export class SimulationsApi {
  private readonly http = inject(HttpClient);

  list(personaId: number | null = null, limit = 100): Observable<ListResponse<SimBranchRow>> {
    return this.http.get<ListResponse<SimBranchRow>>('/api/simulations', {
      params: toParams({ persona_id: personaId, limit }),
    });
  }
  get(id: number): Observable<SimBranchDetail> {
    return this.http.get<SimBranchDetail>(`/api/simulations/${id}`);
  }
}
