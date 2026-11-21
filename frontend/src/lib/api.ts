/**
 * The one HTTP entry point (contract: docs/api.md).
 * - every path is relative to `/api`;
 * - cookie session (`credentials: 'include'`), `X-CSRF-Token` on every non-GET request;
 * - an error body `{"error": "текст"}` becomes a thrown `ApiError` with that text as its message;
 * - 401 calls the unauthorized handler (sign-in page) unless the caller is probing the session.
 */

export const API_PREFIX = '/api';

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  /** JSON-serialisable value or FormData (multipart upload). */
  body?: unknown;
  params?: Record<string, QueryValue>;
  /** Do not redirect to sign-in on 401 (session probe, the sign-in call itself). */
  noAuthRedirect?: boolean;
  signal?: AbortSignal;
}

let csrfToken: string | null = null;
let unauthorizedHandler: () => void = () => undefined;

/** Set from `/auth/me` and `/auth/telegram` replies; cleared on sign-out. */
export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

/** Wired once at start-up so this module stays free of routing and UI. */
export function setUnauthorizedHandler(handler: () => void): void {
  unauthorizedHandler = handler;
}

/** `/chats` + `{q: 'x', before: ''}` -> `/api/chats?q=x` (empty values are omitted). */
export function apiUrl(path: string, params?: Record<string, QueryValue>): string {
  const url = `${API_PREFIX}${path}`;
  if (!params) return url;
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === '' || value === null || value === undefined) continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `${url}?${qs}` : url;
}

const FALLBACK: Record<number, string> = {
  0: 'Нет связи с сервером',
  401: 'Нужно войти заново',
  403: 'Недостаточно прав',
  404: 'Не найдено',
  413: 'Файл слишком большой',
  429: 'Слишком много запросов, подождите немного',
};

export function fallbackMessage(status: number): string {
  return FALLBACK[status] ?? (status >= 500 ? 'Ошибка сервера, попробуйте позже' : 'Не удалось выполнить запрос');
}

async function readBody(res: Response): Promise<unknown> {
  if (res.status === 204) return undefined;
  const text = await res.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function errorText(body: unknown): string | null {
  if (body && typeof body === 'object' && 'error' in body) {
    const value = (body as { error: unknown }).error;
    if (typeof value === 'string' && value.trim()) return value;
  }
  return null;
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = opts.method ?? 'GET';
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (method !== 'GET' && csrfToken) headers['X-CSRF-Token'] = csrfToken;

  let body: BodyInit | undefined;
  if (opts.body instanceof FormData) {
    body = opts.body; // the browser sets the multipart boundary itself
  } else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(opts.body);
  }

  let res: Response;
  try {
    res = await fetch(apiUrl(path, opts.params), {
      method, headers, body, credentials: 'include', signal: opts.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause;
    throw new ApiError(0, fallbackMessage(0));
  }

  const data = await readBody(res);
  if (res.ok) return data as T;
  if (res.status === 401 && !opts.noAuthRedirect) unauthorizedHandler();
  throw new ApiError(res.status, errorText(data) ?? fallbackMessage(res.status));
}

type Opts = Omit<RequestOptions, 'method' | 'body'>;

export const api = {
  get: <T>(path: string, opts?: Opts) => request<T>(path, opts),
  post: <T>(path: string, body?: unknown, opts?: Opts) => request<T>(path, { ...opts, method: 'POST', body }),
  put: <T>(path: string, body: unknown, opts?: Opts) => request<T>(path, { ...opts, method: 'PUT', body }),
  patch: <T>(path: string, body: unknown, opts?: Opts) => request<T>(path, { ...opts, method: 'PATCH', body }),
  delete: <T = void>(path: string, opts?: Opts) => request<T>(path, { ...opts, method: 'DELETE' }),
};

/** Human message of any failure (for toasts and inline errors). */
export function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return 'Что-то пошло не так, попробуйте ещё раз';
}
