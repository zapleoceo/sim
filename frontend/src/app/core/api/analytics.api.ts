import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { AnalyticsSummary, PartnerImportResult } from '../models';
import { toParams } from './http-params';

@Injectable({ providedIn: 'root' })
export class AnalyticsApi {
  private readonly http = inject(HttpClient);

  summary(from: string, to: string, personaId: number | null): Observable<AnalyticsSummary> {
    return this.http.get<AnalyticsSummary>('/api/analytics/summary', {
      params: toParams({ from, to, persona_id: personaId }),
    });
  }
  importPartnerCsv(file: File): Observable<PartnerImportResult> {
    const form = new FormData();
    form.append('file', file, file.name);
    return this.http.post<PartnerImportResult>('/api/partner/import', form);
  }
}
