import type { AnalyticsSpend, AnalyticsSummary, PartnerImportResult } from '../models';
import { http } from './http';

export const analyticsApi = {
  summary: (from: string, to: string, personaId: number | null) =>
    http.get<AnalyticsSummary>('/api/analytics/summary', { params: { from, to, persona_id: personaId } }),
  /** Silent: Broker being down shows inside the card, not as a toast. */
  spend: (from: string, to: string) =>
    http.get<AnalyticsSpend>('/api/analytics/spend', { params: { from, to }, silent: true }),
  importPartnerCsv: (file: File) => {
    const form = new FormData();
    form.append('file', file, file.name);
    return http.post<PartnerImportResult>('/api/partner/import', form);
  },
};
