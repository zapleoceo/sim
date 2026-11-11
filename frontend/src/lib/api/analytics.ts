import type { AnalyticsSummary, PartnerImportResult } from '../models';
import { http } from './http';

export const analyticsApi = {
  summary: (from: string, to: string, personaId: number | null) =>
    http.get<AnalyticsSummary>('/api/analytics/summary', { params: { from, to, persona_id: personaId } }),
  importPartnerCsv: (file: File) => {
    const form = new FormData();
    form.append('file', file, file.name);
    return http.post<PartnerImportResult>('/api/partner/import', form);
  },
};
