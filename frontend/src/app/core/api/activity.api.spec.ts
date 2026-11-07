import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { ActivityApi } from './activity.api';

describe('ActivityApi contract', () => {
  let api: ActivityApi;
  let http: HttpTestingController;
  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    api = TestBed.inject(ActivityApi); http = TestBed.inject(HttpTestingController);
  });
  afterEach(() => http.verify());
  it('combines message filters and bounded pagination, trims search and omits blanks', () => {
    api.messages({ q: '  текст  ', channel_id: 4, author: 'operator', direction: 'out', delivery: 'failed', from: '2026-11-01', to: '2026-11-07', channel_kind: '' }, { limit: 25, offset: 50 }).subscribe();
    const req = http.expectOne((r) => r.url === '/api/activity/messages');
    expect(req.request.params.get('q')).toBe('текст');
    expect(req.request.params.get('channel_id')).toBe('4');
    expect(req.request.params.get('direction')).toBe('out');
    expect(req.request.params.get('author')).toBe('operator');
    expect(req.request.params.get('delivery')).toBe('failed');
    expect(req.request.params.get('from')).toBe('2026-11-01');
    expect(req.request.params.get('to')).toBe('2026-11-07');
    expect(req.request.params.has('channel_kind')).toBeFalse();
    expect(req.request.params.get('limit')).toBe('25');
    expect(req.request.params.get('offset')).toBe('50');
    expect(req.request.headers.get('X-Silent')).toBe('1');
    req.flush({ items: [], total: 0, limit: 25, offset: 50 });
  });
  it('uses the scoped endpoint for client history', () => {
    api.messages({}, { limit: 50, offset: 0 }, 9).subscribe();
    const request = http.expectOne((r) => r.url === '/api/clients/9/messages');
    expect(request.request.method).toBe('GET');
    request.flush({ items: [], total: 0, limit: 50, offset: 0 });
  });
  it('preserves false as the successful-call filter rather than dropping it', () => {
    api.llm({ has_error: false, workflow: 'reply', model: 'm', provider: 'p' }).subscribe();
    const req = http.expectOne((r) => r.url === '/api/activity/llm');
    expect(req.request.params.get('has_error')).toBe('false');
    expect(req.request.params.get('workflow')).toBe('reply');
    expect(req.request.params.get('model')).toBe('m');
    expect(req.request.params.get('provider')).toBe('p');
    req.flush({ items: [], total: 0, limit: 50, offset: 0 });
  });
});
