import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { DEFAULT_DIALOG_FILTER } from '../../features/inbox/inbox-filters';
import { DialogsApi } from './dialogs.api';

describe('DialogsApi', () => {
  let api: DialogsApi;
  let ctrl: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    api = TestBed.inject(DialogsApi);
    ctrl = TestBed.inject(HttpTestingController);
  });

  afterEach(() => ctrl.verify());

  it('sends needs_operator only when the filter is on', () => {
    api.list({ ...DEFAULT_DIALOG_FILTER, needs_operator: true }).subscribe();
    const on = ctrl.expectOne((r) => r.url === '/api/dialogs');
    expect(on.request.params.get('needs_operator')).toBe('true');
    on.flush({ items: [], total: 0 });

    api.list(DEFAULT_DIALOG_FILTER).subscribe();
    const off = ctrl.expectOne((r) => r.url === '/api/dialogs');
    expect(off.request.params.has('needs_operator')).toBe(false);
    off.flush({ items: [], total: 0 });
  });

  it('resolve() posts to /resolve', () => {
    api.resolve(7).subscribe((d) => expect(d.needs_operator).toBe(false));
    const req = ctrl.expectOne('/api/dialogs/7/resolve');
    expect(req.request.method).toBe('POST');
    req.flush({ id: 7, ai_paused: true, needs_operator: false });
  });
});
