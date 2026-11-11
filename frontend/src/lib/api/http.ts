/**
 * Single HTTP entry point for the admin.
 * Contract (unchanged from the previous admin): cookie session, `X-CSRF-Token` on mutations,
 * 401 -> sign-in page (except the sign-in call itself), human-readable error toast unless `silent`.
 */

const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
const LOGIN_URL = '/api/auth/telegram';

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, body: unknown, url: string) {
    super(`${status} ${url}`);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

/** Query value; '', null, undefined and false are omitted (filters with defaults). */
export type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  params?: Record<string, QueryValue>;
  /** Do not show an error toast (caller handles the error or it is a background poll). */
  silent?: boolean;
  /** Do not redirect to sign-in on 401 (session probe). */
  noAuthRedirect?: boolean;
  signal?: AbortSignal;
}

interface HttpHooks {
  csrfToken: () => string | null;
  onUnauthorized: () => void;
  onError: (error: unknown) => void;
}

const hooks: HttpHooks = {
  csrfToken: () => null,
  onUnauthorized: () => undefined,
  onError: () => undefined,
};

/** Wired once at start-up (main.tsx) so this module stays free of UI and routing. */
export function configureHttp(next: Partial<HttpHooks>): void {
  Object.assign(hooks, next);
}

export function buildUrl(url: string, params?: Record<string, QueryValue>): string {
  if (!params) return url;
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === '' || value === null || value === undefined || value === false) continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `${url}?${qs}` : url;
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

export async function request<T>(url: string, opts: RequestOptions = {}): Promise<T> {
  const method = opts.method ?? 'GET';
  const headers: Record<string, string> = { Accept: 'application/json' };
  const csrf = hooks.csrfToken();
  if (csrf && MUTATING.has(method)) headers['X-CSRF-Token'] = csrf;

  let body: BodyInit | undefined;
  if (opts.body instanceof FormData) {
    body = opts.body;
  } else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(opts.body);
  }

  const fullUrl = buildUrl(url, opts.params);
  let res: Response;
  try {
    res = await fetch(fullUrl, { method, headers, body, credentials: 'include', signal: opts.signal });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause;
    const error = new ApiError(0, null, fullUrl);
    if (!opts.silent) hooks.onError(error);
    throw error;
  }

  const data = await readBody(res);
  if (res.ok) return data as T;

  const error = new ApiError(res.status, data, fullUrl);
  if (res.status === 401 && url !== LOGIN_URL && !opts.noAuthRedirect) {
    hooks.onUnauthorized();
  } else if (res.status !== 401 && !opts.silent) {
    console.error(`[api] ${method} ${url} -> ${res.status}`, data);
    hooks.onError(error);
  }
  throw error;
}

type Opts = Omit<RequestOptions, 'method' | 'body'>;

export const http = {
  get: <T>(url: string, opts?: Opts) => request<T>(url, opts),
  post: <T>(url: string, body: unknown = {}, opts?: Opts) => request<T>(url, { ...opts, method: 'POST', body }),
  put: <T>(url: string, body: unknown, opts?: Opts) => request<T>(url, { ...opts, method: 'PUT', body }),
  patch: <T>(url: string, body: unknown, opts?: Opts) => request<T>(url, { ...opts, method: 'PATCH', body }),
  delete: <T = void>(url: string, opts?: Opts) => request<T>(url, { ...opts, method: 'DELETE' }),
};

/** Paged list request (API: limit <= 200, offset; reply `{items, total}`). */
export interface PageRequest { limit: number; offset: number }
export const DEFAULT_PAGE_SIZE = 50;
export const DEFAULT_PAGE: PageRequest = { limit: DEFAULT_PAGE_SIZE, offset: 0 };
