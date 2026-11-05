import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { AuthService } from './auth.service';
import { ToastService } from './toast.service';

const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
const LOGIN_URL = '/api/auth/login';

/** Cookie-сессия, CSRF-заголовок на мутациях, редирект на /login при 401. */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  if (!req.url.startsWith('/api') && !req.url.startsWith('/hooks')) return next(req);

  const csrf = auth.csrfToken();
  const prepared = req.clone({
    withCredentials: true,
    setHeaders: csrf && MUTATING.has(req.method) ? { 'X-CSRF-Token': csrf } : {},
  });

  return next(prepared).pipe(
    catchError((err: unknown) => {
      if (err instanceof HttpErrorResponse && err.status === 401 && req.url !== LOGIN_URL) {
        auth.clear();
        if (!router.url.startsWith('/login')) {
          void router.navigate(['/login'], { queryParams: { returnUrl: router.url } });
        }
      }
      return throwError(() => err);
    }),
  );
};

/** Показывает понятный тост на ошибки API (кроме 401 — им занимается authInterceptor). */
export const errorToastInterceptor: HttpInterceptorFn = (req, next) => {
  const toast = inject(ToastService);
  return next(req).pipe(
    catchError((err: unknown) => {
      if (err instanceof HttpErrorResponse && err.status !== 401 && !req.headers.has('X-Silent')) {
        toast.error(errorMessage(err));
      }
      return throwError(() => err);
    }),
  );
};

export function errorMessage(err: unknown): string {
  if (err instanceof HttpErrorResponse) {
    const body: unknown = err.error;
    if (body && typeof body === 'object' && 'detail' in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === 'string') return detail;
    }
    if (err.status === 0) return 'Нет связи с сервером';
    return `Ошибка ${err.status}`;
  }
  return 'Неизвестная ошибка';
}
