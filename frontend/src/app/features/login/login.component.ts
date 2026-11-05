import { HttpErrorResponse } from '@angular/common/http';
import {
  ChangeDetectionStrategy, Component, DestroyRef, ElementRef, InjectionToken, NgZone, effect, inject, signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { ActivatedRoute, Router } from '@angular/router';
import { EMPTY, catchError, of } from 'rxjs';
import { AuthService } from '../../core/auth.service';
import { errorMessage } from '../../core/interceptors';
import { AccessDeniedBody, TelegramWidgetUser } from '../../core/models';

export const TELEGRAM_WIDGET_SRC = new InjectionToken<string>('TELEGRAM_WIDGET_SRC', {
  providedIn: 'root',
  factory: () => 'https://telegram.org/js/telegram-widget.js?22',
});

interface TelegramWindow { onTelegramAuth?: (user: TelegramWidgetUser) => void }

@Component({
  selector: 'sim-login',
  imports: [MatButtonModule, MatIconModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="sim-card card">
      <div class="brand"><span class="logo" aria-hidden="true">S</span><h1>Sim</h1></div>
      <p class="sim-muted">Вход в админ-панель</p>

      @if (configLoaded()) {
        @if (botUsername()) {
          <div #widget class="widget" aria-label="Войти через Telegram"></div>
        } @else {
          <div class="not-configured" role="status">
            <strong>Вход через Telegram не настроен</strong>
            <span class="sim-muted">Задайте SIM_TG_LOGIN_BOT_USERNAME и SIM_TG_LOGIN_BOT_TOKEN на сервере.</span>
          </div>
        }
      }

      @if (deniedId() !== null) {
        <div class="denied" role="alert">
          <p>Доступ не выдан. Ваш Telegram ID: <strong>{{ deniedId() }}</strong> — передайте его администратору.</p>
          <button mat-stroked-button type="button" (click)="copyId()">
            <mat-icon class="material-icons-outlined">content_copy</mat-icon>
            {{ copied() ? 'Скопировано' : 'Скопировать ID' }}
          </button>
        </div>
      }

      @if (error()) {
        <p class="error" role="alert">{{ error() }}</p>
      }
    </div>
  `,
  styles: `
    :host { display: grid; place-items: center; min-height: 100dvh; padding: var(--sim-space-4); }
    .card { width: 100%; max-width: 380px; display: flex; flex-direction: column; align-items: stretch; gap: var(--sim-space-3); padding: var(--sim-space-6); text-align: center; }
    .brand { display: flex; align-items: center; justify-content: center; gap: var(--sim-space-3); }
    .logo { width: 36px; height: 36px; border-radius: 10px; display: grid; place-items: center; background: var(--sim-accent); color: var(--mat-sys-on-primary); font-weight: 600; font-size: 18px; }
    .widget { display: flex; justify-content: center; min-height: 44px; }
    .not-configured, .denied { display: flex; flex-direction: column; align-items: center; gap: var(--sim-space-2); }
    .denied p { margin: 0; }
    .error { color: var(--sim-danger); margin: 0; }
  `,
})
export class LoginComponent {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly zone = inject(NgZone);
  private readonly destroyRef = inject(DestroyRef);
  private readonly scriptSrc = inject(TELEGRAM_WIDGET_SRC);

  private readonly widgetHost = viewChild<ElementRef<HTMLElement>>('widget');
  private mountedFor: HTMLElement | null = null;

  protected readonly botUsername = signal('');
  protected readonly configLoaded = signal(false);
  protected readonly deniedId = signal<number | null>(null);
  protected readonly error = signal('');
  protected readonly copied = signal(false);

  constructor() {
    this.auth
      .config()
      .pipe(
        catchError(() => {
          this.error.set('Не удалось загрузить настройки входа');
          return of({ bot_username: '' });
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((c) => {
        this.botUsername.set(c.bot_username);
        this.configLoaded.set(true);
      });

    effect(() => {
      const bot = this.botUsername();
      const host = this.widgetHost()?.nativeElement;
      if (bot && host && this.mountedFor !== host) this.mountWidget(host, bot);
    });

    this.destroyRef.onDestroy(() => {
      delete (window as unknown as TelegramWindow).onTelegramAuth;
      this.mountedFor?.replaceChildren();
      this.mountedFor = null;
    });
  }

  protected copyId(): void {
    const id = this.deniedId();
    if (id === null) return;
    void navigator.clipboard?.writeText(String(id)).then(() => this.copied.set(true));
  }

  private mountWidget(host: HTMLElement, bot: string): void {
    this.mountedFor = host;
    (window as unknown as TelegramWindow).onTelegramAuth = (user) => this.zone.run(() => this.onTelegramAuth(user));
    const script = document.createElement('script');
    script.async = true;
    script.src = this.scriptSrc;
    script.setAttribute('data-telegram-login', bot);
    script.setAttribute('data-size', 'large');
    script.setAttribute('data-radius', '8');
    script.setAttribute('data-onauth', 'onTelegramAuth(user)');
    host.replaceChildren(script);
  }

  private onTelegramAuth(user: TelegramWidgetUser): void {
    this.error.set('');
    this.deniedId.set(null);
    this.auth
      .loginTelegram(user)
      .pipe(
        catchError((e: unknown) => {
          this.showLoginError(e);
          return EMPTY;
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(() => void this.router.navigateByUrl(this.route.snapshot.queryParamMap.get('returnUrl') ?? '/'));
  }

  private showLoginError(e: unknown): void {
    if (e instanceof HttpErrorResponse && e.status === 403) {
      this.deniedId.set((e.error as AccessDeniedBody).telegram_id);
    } else if (e instanceof HttpErrorResponse && e.status === 401) {
      this.error.set('Не удалось подтвердить вход через Telegram. Попробуйте ещё раз.');
    } else {
      this.error.set(errorMessage(e));
    }
  }
}
