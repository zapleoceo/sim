import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { AuthService } from './auth.service';
import { authInterceptor, errorMessage } from './interceptors';
import { ToastService } from './toast.service';

describe('authInterceptor', () => {
  let http: HttpClient;
  let ctrl: HttpTestingController;
  let auth: AuthService;
  let router: Router;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        { provide: ToastService, useValue: { error: () => undefined, info: () => undefined } },
      ],
    });
    http = TestBed.inject(HttpClient);
    ctrl = TestBed.inject(HttpTestingController);
    auth = TestBed.inject(AuthService);
    router = TestBed.inject(Router);
  });

  afterEach(() => ctrl.verify());

  function signIn(): void {
    auth.restore().subscribe();
    ctrl.expectOne('/api/auth/me').flush({ user: { id: 1, role: 'owner', telegram_id: 1, tg_username: null, display_name: 'a', photo_url: '' }, csrf_token: 'tok-123' });
  }

  it('adds X-CSRF-Token and credentials to mutating requests', () => {
    signIn();
    for (const method of ['post', 'put', 'patch', 'delete'] as const) {
      http.request(method, '/api/things', { body: {} }).subscribe();
      const req = ctrl.expectOne('/api/things');
      expect(req.request.headers.get('X-CSRF-Token')).withContext(method).toBe('tok-123');
      expect(req.request.withCredentials).toBeTrue();
      req.flush({});
    }
  });

  it('does not add X-CSRF-Token to GET requests but keeps cookies', () => {
    signIn();
    http.get('/api/things').subscribe();
    const req = ctrl.expectOne('/api/things');
    expect(req.request.headers.has('X-CSRF-Token')).toBeFalse();
    expect(req.request.withCredentials).toBeTrue();
    req.flush({});
  });

  it('does not touch foreign URLs', () => {
    http.post('https://example.com/x', {}).subscribe();
    const req = ctrl.expectOne('https://example.com/x');
    expect(req.request.withCredentials).toBeFalse();
    req.flush({});
  });

  it('on 401 clears auth state and redirects to /login', () => {
    signIn();
    const navigate = spyOn(router, 'navigate').and.resolveTo(true);
    http.get('/api/dialogs').subscribe({ error: () => undefined });
    ctrl.expectOne('/api/dialogs').flush({ detail: 'unauthorized' }, { status: 401, statusText: 'Unauthorized' });
    expect(auth.isAuthenticated()).toBeFalse();
    expect(navigate).toHaveBeenCalledWith(['/login'], jasmine.anything());
  });

  it('does not redirect when the login request itself returns 401', () => {
    const navigate = spyOn(router, 'navigate').and.resolveTo(true);
    http.post('/api/auth/telegram', {}).subscribe({ error: () => undefined });
    ctrl.expectOne('/api/auth/telegram').flush({ detail: 'bad' }, { status: 401, statusText: 'Unauthorized' });
    expect(navigate).not.toHaveBeenCalled();
  });
});

describe('errorMessage', () => {
  it('maps status to a human message and never leaks the backend detail', async () => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    const http = TestBed.inject(HttpClient);
    const ctrl = TestBed.inject(HttpTestingController);
    let message = '';
    http.get('/x').subscribe({ error: (e: unknown) => (message = errorMessage(e)) });
    ctrl.expectOne('/x').flush({ detail: 'Broker error: HTTP 401' }, { status: 502, statusText: 'Bad Gateway' });
    expect(message).toBe('Сервис временно недоступен. Попробуйте позже');
    expect(message).not.toContain('Broker');
  });
});
