import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';
import { ListResponse, Strategy, StrategyIn } from '../models';

@Injectable({ providedIn: 'root' })
export class StrategiesApi {
  private readonly http = inject(HttpClient);

  list(): Observable<Strategy[]> {
    return this.http.get<ListResponse<Strategy>>('/api/strategies').pipe(map((r) => r.items));
  }
  create(body: StrategyIn): Observable<Strategy> {
    return this.http.post<Strategy>('/api/strategies', body);
  }
  update(id: number, body: StrategyIn): Observable<Strategy> {
    return this.http.put<Strategy>(`/api/strategies/${id}`, body);
  }
  remove(id: number): Observable<void> {
    return this.http.delete<void>(`/api/strategies/${id}`);
  }
}
