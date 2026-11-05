import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { SimulationsApi } from './simulations.api';

describe('SimulationsApi', () => {
  let api: SimulationsApi;
  let ctrl: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    api = TestBed.inject(SimulationsApi);
    ctrl = TestBed.inject(HttpTestingController);
  });

  afterEach(() => ctrl.verify());

  it('lists branches, sending persona_id only when set', () => {
    api.list(null).subscribe();
    const all = ctrl.expectOne((r) => r.url === '/api/simulations');
    expect(all.request.params.has('persona_id')).toBe(false);
    all.flush({ items: [], total: 0 });

    api.list(3).subscribe();
    const one = ctrl.expectOne((r) => r.url === '/api/simulations');
    expect(one.request.params.get('persona_id')).toBe('3');
    one.flush({ items: [], total: 0 });
  });

  it('loads a transcript by id', () => {
    api.get(9).subscribe((d) => expect(d.messages.length).toBe(0));
    ctrl.expectOne('/api/simulations/9').flush({ id: 9, messages: [] });
  });
});
