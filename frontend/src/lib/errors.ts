import { ApiError } from './api/http';

/**
 * Human message for a failed request. Raw backend/exception text is not shown to the user;
 * details go to the console (see http.ts).
 */
export function errorMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return 'Что-то пошло не так. Попробуйте ещё раз';
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

/** Short detail from a FastAPI error body (`{detail: "..."}`), if it is a plain string. */
export function errorDetail(err: unknown): string {
  if (!(err instanceof ApiError)) return '';
  const body = err.body as { detail?: unknown } | null;
  return body && typeof body.detail === 'string' ? body.detail : '';
}
