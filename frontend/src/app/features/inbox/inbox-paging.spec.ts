import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { provideRouter } from '@angular/router';
import { DialogRow } from '../../core/models';
import { InboxComponent } from './inbox.component';

function row(id: number): DialogRow {
  return {
    id, channel_kind: 'telegram', ai_paused: false, needs_operator: false, unread: 0, last_message: null,
    client: { id, display_name: `Клиент ${id}`, avatar_url: null, stage: 'engaged', status: 'active' },
    persona: { id: 1, name: 'Марина' },
  } as unknown as DialogRow;
}

describe('InboxComponent paging', () => {
  it('shows all pages: first page limit=50, "show more" requests the next offset', () => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), provideNoopAnimations(), provideRouter([])],
    });
    const ctrl = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(InboxComponent);
    fixture.detectChanges();
    ctrl.match((r) => r.url === '/api/personas').forEach((r) => r.flush({ items: [] }));

    const first = ctrl.expectOne((r) => r.url === '/api/dialogs');
    expect(first.request.params.get('limit')).toBe('50');
    expect(first.request.params.get('offset')).toBe('0');
    first.flush({ items: Array.from({ length: 50 }, (_, i) => row(i + 1)), total: 120 });
    fixture.detectChanges();

    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelectorAll('button.row').length).toBe(50);
    const more = el.querySelector('li.more button') as HTMLButtonElement;
    expect(more.textContent).toContain('70');
    more.click();

    const next = ctrl.expectOne((r) => r.url === '/api/dialogs');
    expect(next.request.params.get('offset')).toBe('50');
    next.flush({ items: Array.from({ length: 50 }, (_, i) => row(i + 51)), total: 120 });
    fixture.detectChanges();
    expect(el.querySelectorAll('button.row').length).toBe(100);
    fixture.destroy();
  });
});
