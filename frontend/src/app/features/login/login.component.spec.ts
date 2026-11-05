import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { TelegramWidgetUser } from '../../core/models';
import { LoginComponent, TELEGRAM_WIDGET_SRC } from './login.component';

const USER: TelegramWidgetUser = { id: 169510539, first_name: 'Dima', auth_date: 1_700_000_000, hash: 'abc' };
const SESSION = {
  user: { id: 1, role: 'superadmin', telegram_id: 169510539, tg_username: null, display_name: 'Dima', photo_url: '' },
  csrf_token: 'csrf',
};

type WidgetWindow = { onTelegramAuth?: (u: TelegramWidgetUser) => void };

describe('LoginComponent (Telegram)', () => {
  let ctrl: HttpTestingController;
  let fixture: ComponentFixture<LoginComponent>;
  let navigate: jasmine.Spy;

  function setup(botUsername: string): HTMLElement {
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: TELEGRAM_WIDGET_SRC, useValue: 'data:text/javascript,' },
      ],
    });
    ctrl = TestBed.inject(HttpTestingController);
    navigate = spyOn(TestBed.inject(Router), 'navigateByUrl').and.resolveTo(true);
    fixture = TestBed.createComponent(LoginComponent);
    fixture.detectChanges();
    ctrl.expectOne('/api/auth/config').flush({ bot_username: botUsername });
    fixture.detectChanges();
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  afterEach(() => ctrl.verify());

  it('mounts the widget script with the bot username', () => {
    const el = setup('sim_login_bot');
    const script = el.querySelector('script');
    expect(script?.getAttribute('data-telegram-login')).toBe('sim_login_bot');
    expect(script?.getAttribute('data-onauth')).toBe('onTelegramAuth(user)');
    expect(script?.getAttribute('data-size')).toBe('large');
  });

  it('shows a hint when the bot is not configured', () => {
    const el = setup('');
    expect(el.textContent).toContain('Вход через Telegram не настроен');
    expect(el.querySelector('script')).toBeNull();
  });

  it('posts the widget payload and navigates on success', () => {
    setup('sim_login_bot');
    (window as unknown as WidgetWindow).onTelegramAuth?.(USER);
    const req = ctrl.expectOne('/api/auth/telegram');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(USER);
    req.flush(SESSION);
    expect(navigate).toHaveBeenCalledWith('/');
  });

  it('shows the Telegram id on 403 and does not navigate', () => {
    const el = setup('sim_login_bot');
    (window as unknown as WidgetWindow).onTelegramAuth?.(USER);
    ctrl.expectOne('/api/auth/telegram').flush(
      { detail: 'Доступ не выдан', telegram_id: 169510539 },
      { status: 403, statusText: 'Forbidden' },
    );
    fixture.detectChanges();
    expect(el.textContent).toContain('Доступ не выдан. Ваш Telegram ID: 169510539');
    expect(el.textContent).toContain('Скопировать ID');
    expect(navigate).not.toHaveBeenCalled();
  });

  it('removes the global callback on destroy', () => {
    setup('sim_login_bot');
    fixture.destroy();
    expect((window as unknown as WidgetWindow).onTelegramAuth).toBeUndefined();
  });
});
