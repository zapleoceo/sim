import { vi } from 'vitest';

export interface FakeRequest {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
  headers: Record<string, string>;
}

/** Reply: a JSON body (200), or `{ status, body }` via `reply()`. */
export type FakeReply = unknown | ((req: FakeRequest) => unknown);

const STATUS = Symbol('status');
interface StatusReply { [STATUS]: true; status: number; body: unknown }

export function reply(status: number, body: unknown = null): StatusReply {
  return { [STATUS]: true, status, body };
}

function isStatusReply(v: unknown): v is StatusReply {
  return typeof v === 'object' && v !== null && STATUS in v;
}

/**
 * Fake backend for tests: routes keyed `"GET /api/personas"` (path without query).
 * Unknown routes answer 404, so a test never reaches a real network. Returns the request log.
 */
export function fakeApi(routes: Record<string, FakeReply>): FakeRequest[] {
  const log: FakeRequest[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === 'string' ? input : input.toString(), 'http://localhost');
    const method = (init?.method ?? 'GET').toUpperCase();
    let body: unknown = init?.body;
    if (typeof body === 'string') {
      try {
        body = JSON.parse(body);
      } catch {
        /* keep text */
      }
    }
    const req: FakeRequest = {
      method,
      path: url.pathname,
      query: url.searchParams,
      body,
      headers: (init?.headers ?? {}) as Record<string, string>,
    };
    log.push(req);
    const handler = routes[`${method} ${url.pathname}`];
    let out: unknown = handler === undefined ? reply(404, { detail: 'not found' }) : handler;
    if (typeof out === 'function') out = await (out as (r: FakeRequest) => unknown)(req);
    const { status, payload } = isStatusReply(out) ? { status: out.status, payload: out.body } : { status: 200, payload: out };
    return new Response(payload === undefined || status === 204 ? null : JSON.stringify(payload), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  });
  vi.stubGlobal('fetch', fetchMock);
  return log;
}
