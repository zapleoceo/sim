import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { Observable, catchError, map, of, tap } from 'rxjs';
import { AuthConfig, AuthState, Role, TelegramWidgetUser, User } from './models';

export function hasOwnerRights(role: Role | undefined): boolean {
  return role === 'owner' || role === 'superadmin';
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);

  private readonly userState = signal<User | null>(null);
  private readonly csrfState = signal<string | null>(null);

  readonly user = this.userState.asReadonly();
  readonly csrfToken = this.csrfState.asReadonly();
  readonly isAuthenticated = computed(() => this.userState() !== null);
  /** Права владельца: owner и superadmin (зеркало Role.has_owner_rights на бэкенде). */
  readonly isOwner = computed(() => hasOwnerRights(this.userState()?.role));
  readonly isSuperadmin = computed(() => this.userState()?.role === 'superadmin');

  /** Восстанавливает сессию по cookie. Возвращает true, если пользователь авторизован. */
  restore(): Observable<boolean> {
    return this.http.get<AuthState>('/api/auth/me').pipe(
      tap((s) => this.apply(s)),
      map(() => true),
      catchError(() => {
        this.clear();
        return of(false);
      }),
    );
  }

  config(): Observable<AuthConfig> {
    return this.http.get<AuthConfig>('/api/auth/config');
  }

  /** Вход через Telegram Login Widget. 403 обрабатывает вызывающий (X-Silent: без тоста). */
  loginTelegram(user: TelegramWidgetUser): Observable<AuthState> {
    return this.http
      .post<AuthState>('/api/auth/telegram', user, { headers: { 'X-Silent': '1' } })
      .pipe(tap((s) => this.apply(s)));
  }

  logout(): void {
    this.http
      .post<void>('/api/auth/logout', {})
      .pipe(catchError(() => of(undefined)))
      .subscribe(() => {
        this.clear();
        void this.router.navigateByUrl('/login');
      });
  }

  /** Сбрасывает локальное состояние (вызывается интерцептором при 401). */
  clear(): void {
    this.userState.set(null);
    this.csrfState.set(null);
  }

  private apply(state: AuthState): void {
    this.userState.set(state.user);
    this.csrfState.set(state.csrf_token);
  }
}
