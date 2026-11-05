import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { Observable, catchError, map, of, tap } from 'rxjs';
import { AuthState, User } from './models';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);

  private readonly userState = signal<User | null>(null);
  private readonly csrfState = signal<string | null>(null);

  readonly user = this.userState.asReadonly();
  readonly csrfToken = this.csrfState.asReadonly();
  readonly isAuthenticated = computed(() => this.userState() !== null);
  readonly isOwner = computed(() => this.userState()?.role === 'owner');

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

  login(username: string, password: string): Observable<AuthState> {
    return this.http
      .post<AuthState>('/api/auth/login', { username, password })
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
