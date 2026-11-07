import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { ActivityPage, LlmActivity, LlmActivityFilter, MessageActivity, MessageActivityFilter } from '../activity.models';
import { DEFAULT_PAGE, PageRequest, toParams } from './http-params';

@Injectable({ providedIn: 'root' })
export class ActivityApi {
  private readonly http = inject(HttpClient);

  messages(filter: MessageActivityFilter, page: PageRequest = DEFAULT_PAGE, clientId?: number): Observable<ActivityPage<MessageActivity>> {
    const url = clientId === undefined ? '/api/activity/messages' : `/api/clients/${clientId}/messages`;
    return this.http.get<ActivityPage<MessageActivity>>(url, {
      params: toParams({ ...filter, q: filter.q?.trim(), ...page }), headers: { 'X-Silent': '1' },
    });
  }

  llm(filter: LlmActivityFilter, page: PageRequest = DEFAULT_PAGE): Observable<ActivityPage<LlmActivity>> {
    let params = toParams({ ...filter, q: filter.q?.trim(), ...page });
    // Shared helper deliberately omits false for older checkbox filters; here false is an exact outcome.
    if (filter.has_error === false) params = params.set('has_error', 'false');
    return this.http.get<ActivityPage<LlmActivity>>('/api/activity/llm', { params, headers: { 'X-Silent': '1' } });
  }
}
