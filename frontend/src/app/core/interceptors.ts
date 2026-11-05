import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { AuthService } from './auth.service';
import { ToastService } from './toast.service';

const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
const LOGIN_URL = '/api/auth/telegram';

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
        console.error(`[api] ${req.method} ${req.url} -> ${err.status}`, err.error);
        toast.error(errorMessage(err));
      }
      return throwError(() => err);
    }),
  );
};

/**
 * Человеческое сообщение по статусу ответа. Сырой текст бэкенда/исключений пользователю не показываем:
 * подробности уходят в console (см. errorToastInterceptor).
 */
export function errorMessage(err: unknown): string {
  if (!(err instanceof HttpErrorResponse)) return 'Что-то пошло не так. Попробуйте ещё раз';
  switch (err.status) {
    case 0: return 'Нет связи с сервером';
    case 400:
    case 422: return 'Проверьте введённые данные';
    case 403: return 'Недостаточно прав для этого действия';
    case 404: return 'Не найдено — возможно, запись уже удалена';
    case 409: return 'Данные изменились. Обновите страницу и повторите';
    case 413: return 'Файл слишком большой';
    case 429: return 'Слишком много запросов. Подождите немного';
    case 502:
    case 503:
    case 504: return 'Сервис временно недоступен. Попробуйте позже';
    default: return err.status >= 500 ? 'Ошибка сервера. Попробуйте позже' : 'Не удалось выполнить действие';
  }
}
