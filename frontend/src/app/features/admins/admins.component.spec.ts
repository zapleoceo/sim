import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { AuthService } from '../../core/auth.service';
import { superadminGuard } from '../../core/guards';
import { AdminUser, Role } from '../../core/models';
import { AdminsComponent, parseAdminTarget } from './admins.component';

function admin(id: number, role: Role): AdminUser {
  return {
    id, role, telegram_id: 1000 + id, tg_username: null, display_name: `Admin ${id}`, photo_url: '',
    is_active: true, created_at: '2026-11-01T00:00:00Z',
  };
}

function session(role: Role, id = 1): void {
  const auth = TestBed.inject(AuthService);
  const ctrl = TestBed.inject(HttpTestingController);
  auth.restore().subscribe();
  ctrl.expectOne('/api/auth/me').flush({
    user: { id, role, telegram_id: 1000 + id, tg_username: null, display_name: 'Me', photo_url: '' },
    csrf_token: 't',
  });
}

describe('admins page', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
  });

  afterEach(() => TestBed.inject(HttpTestingController).verify());

  it('superadminGuard lets only superadmin through', () => {
    const router = TestBed.inject(Router);
    for (const [role, allowed] of [['superadmin', true], ['owner', false], ['operator', false]] as const) {
      session(role);
      let result: unknown;
      TestBed.runInInjectionContext(() => {
        const out = superadminGuard({} as never, {} as never);
        (out as import('rxjs').Observable<unknown>).subscribe((v) => (result = v));
      });
      if (allowed) expect(result).toBe(true);
      else expect(result).toEqual(router.createUrlTree(['/inbox']));
    }
  });

  it('parses telegram id vs @username', () => {
    expect(parseAdminTarget(' 169510539 ')).toEqual({ telegram_id: 169510539 });
    expect(parseAdminTarget('@Some_One')).toEqual({ tg_username: 'Some_One' });
    expect(parseAdminTarget('plain')).toEqual({ tg_username: 'plain' });
  });

  it('disables actions on the current admin only', () => {
    session('superadmin', 1);
    const fixture = TestBed.createComponent(AdminsComponent);
    TestBed.inject(HttpTestingController)
      .expectOne('/api/admins')
      .flush({ items: [admin(1, 'superadmin'), admin(2, 'operator')] });
    fixture.detectChanges();
    const rows = Array.from((fixture.nativeElement as HTMLElement).querySelectorAll('tbody tr'));
    expect(rows.length).toBe(2);
    const disabled = (row: Element): boolean[] =>
      Array.from(row.querySelectorAll('button')).map((b) => (b as HTMLButtonElement).disabled);
    expect(disabled(rows[0]).every(Boolean)).toBeTrue();
    expect(disabled(rows[1]).some(Boolean)).toBeFalse();
  });

  it('adds an admin by username', () => {
    session('superadmin', 1);
    const ctrl = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(AdminsComponent);
    ctrl.expectOne('/api/admins').flush({ items: [admin(1, 'superadmin')] });
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const input = el.querySelector('input') as HTMLInputElement;
    input.value = '@newbie';
    input.dispatchEvent(new Event('input'));
    (el.querySelector('form') as HTMLFormElement).dispatchEvent(new Event('submit'));
    const req = ctrl.expectOne('/api/admins');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ tg_username: 'newbie', role: 'operator' });
    req.flush({ ...admin(3, 'operator'), telegram_id: null, tg_username: 'newbie' });
  });
});
