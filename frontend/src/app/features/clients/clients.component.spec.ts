import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { fakeAsync, TestBed, tick } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { provideRouter } from '@angular/router';
import { ClientsComponent } from './clients.component';

describe('ClientsComponent paging', () => {
  it('requests a server-side page and shows the paginator when total exceeds one page', fakeAsync(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), provideNoopAnimations(), provideRouter([])],
    });
    const ctrl = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(ClientsComponent);
    fixture.detectChanges();
    tick(300);
    ctrl.match((r) => r.url === '/api/personas').forEach((r) => r.flush({ items: [] }));

    const req = ctrl.expectOne((r) => r.url === '/api/clients');
    expect(req.request.params.get('limit')).toBe('50');
    expect(req.request.params.get('offset')).toBe('0');
    req.flush({ items: [], total: 230 });
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('mat-paginator')).not.toBeNull();
    fixture.destroy();
  }));
});
