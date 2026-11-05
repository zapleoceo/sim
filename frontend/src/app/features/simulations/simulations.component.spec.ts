import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { SimBranchRow } from '../../core/models';
import { SimulationsComponent } from './simulations.component';

const ROW: SimBranchRow = {
  id: 4,
  admin: { id: 1, display_name: 'Дима' },
  persona: { id: 2, name: 'Марина Светлова' },
  title: 'Марина · 10.11 12:00',
  message_count: 2,
  stage: 'engaged',
  status: 'active',
  created_at: '2026-11-10T12:00:00Z',
  last_active_at: '2026-11-10T12:05:00Z',
  closed_at: null,
};

describe('SimulationsComponent', () => {
  it('lists branches and shows a read-only transcript with the guard badge and model', () => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), provideNoopAnimations()],
    });
    const ctrl = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(SimulationsComponent);
    fixture.detectChanges();
    ctrl.expectOne((r) => r.url === '/api/simulations').flush({ items: [ROW], total: 1 });
    fixture.detectChanges();

    const el: HTMLElement = fixture.nativeElement;
    expect(el.textContent).toContain('Марина Светлова');
    expect(el.textContent).toContain('Дима');

    (el.querySelector('button.item') as HTMLButtonElement).click();
    ctrl.expectOne('/api/simulations/4').flush({
      ...ROW,
      needs: ['сменить профессию'],
      next_step: 'уточнить опыт',
      notes: '',
      messages: [
        { id: 1, role: 'client', text: 'Здравствуйте', model: null, created_at: '2026-11-10T12:00:00Z', guard: null },
        {
          id: 2, role: 'ai', text: 'Привет!', model: 'fake-model', created_at: '2026-11-10T12:00:05Z',
          guard: { verdict: 'rewritten', violations: ['тон'], model: null, original_text: 'Привет' },
        },
      ],
    });
    fixture.detectChanges();

    expect(el.querySelectorAll('.msg').length).toBe(2);
    expect(el.textContent).toContain('fake-model');
    expect(el.textContent).toContain('Исправлено контролером');
    expect(el.querySelector('textarea, input')).toBeNull(); // только просмотр
    ctrl.verify();
  });
});
