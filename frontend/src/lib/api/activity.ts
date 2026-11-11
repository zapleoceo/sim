import type { ActivityPage, LlmActivity, LlmActivityFilter, MessageActivity, MessageActivityFilter } from '../activity-models';
import type { TraceDetail, TraceLogPage } from '../trace-models';
import { DEFAULT_PAGE, http, type PageRequest, type QueryValue } from './http';

export const activityApi = {
  /** Messages across channels, or one client's feed when `clientId` is given. */
  messages: (filter: MessageActivityFilter, page: PageRequest = DEFAULT_PAGE, clientId?: number) =>
    http.get<ActivityPage<MessageActivity>>(
      clientId === undefined ? '/api/activity/messages' : `/api/clients/${clientId}/messages`,
      { params: { ...filter, q: filter.q?.trim(), ...page }, silent: true },
    ),
  llm: (filter: LlmActivityFilter, page: PageRequest = DEFAULT_PAGE) => {
    const params: Record<string, QueryValue> = { ...filter, q: filter.q?.trim(), ...page };
    // The query builder omits false for checkbox-style filters; here false is an exact outcome.
    if (filter.has_error === false) params.has_error = 'false';
    return http.get<ActivityPage<LlmActivity>>('/api/activity/llm', { params, silent: true });
  },
};

/** System trace log (owner only): server-side filters, sort and page; one trace end to end. */
export const traceApi = {
  log: (params: Record<string, QueryValue>) =>
    http.get<TraceLogPage>('/api/activity/log', { params, silent: true }),
  trace: (traceId: string) => http.get<TraceDetail>(`/api/activity/traces/${encodeURIComponent(traceId)}`, { silent: true }),
};
