import { fakeApi, reply } from '@/test/fake-api';
import { ApiError, buildUrl, configureHttp, http } from './http';

describe('buildUrl', () => {
  it('drops empty filter values and keeps zero', () => {
    expect(buildUrl('/api/x', { q: '', a: null, b: undefined, c: false, d: 0, e: 'ok', f: true })).toBe('/api/x?d=0&e=ok&f=true');
  });

  it('returns the bare url without params', () => {
    expect(buildUrl('/api/x')).toBe('/api/x');
    expect(buildUrl('/api/x', { q: '' })).toBe('/api/x');
  });
});

describe('http', () => {
  const onError = vi.fn();
  const onUnauthorized = vi.fn();

  beforeEach(() => {
    onError.mockReset();
    onUnauthorized.mockReset();
    configureHttp({ csrfToken: () => 'tok', onError, onUnauthorized });
  });

  it('sends the CSRF header only on mutations and includes cookies', async () => {
    const log = fakeApi({ 'GET /api/a': { ok: 1 }, 'POST /api/a': { ok: 2 } });
    await http.get('/api/a');
    await http.post('/api/a', { x: 1 });
    expect(log[0].headers['X-CSRF-Token']).toBeUndefined();
    expect(log[1].headers['X-CSRF-Token']).toBe('tok');
    expect(log[1].body).toEqual({ x: 1 });
    const init = vi.mocked(fetch).mock.calls[0][1];
    expect(init?.credentials).toBe('include');
  });

  it('passes FormData through without a JSON content type', async () => {
    const log = fakeApi({ 'POST /api/upload': { imported: 1 } });
    const form = new FormData();
    form.append('file', new Blob(['a']), 'a.csv');
    await http.post('/api/upload', form);
    expect(log[0].body).toBeInstanceOf(FormData);
    expect(log[0].headers['Content-Type']).toBeUndefined();
  });

  it('returns undefined for 204', async () => {
    fakeApi({ 'DELETE /api/a/1': reply(204) });
    await expect(http.delete('/api/a/1')).resolves.toBeUndefined();
  });

  it('on 401 calls onUnauthorized, never toasts, and rejects with ApiError', async () => {
    fakeApi({ 'GET /api/a': reply(401, { detail: 'no' }) });
    await expect(http.get('/api/a')).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledOnce();
    expect(onError).not.toHaveBeenCalled();
  });

  it('does not redirect on 401 from the sign-in call or a session probe', async () => {
    fakeApi({ 'POST /api/auth/telegram': reply(401), 'GET /api/auth/me': reply(401) });
    await expect(http.post('/api/auth/telegram', {})).rejects.toBeInstanceOf(ApiError);
    await expect(http.get('/api/auth/me', { noAuthRedirect: true })).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it('reports other errors unless silent', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    fakeApi({ 'GET /api/a': reply(500, { detail: 'boom' }) });
    await expect(http.get('/api/a')).rejects.toMatchObject({ status: 500, body: { detail: 'boom' } });
    expect(onError).toHaveBeenCalledOnce();
    await expect(http.get('/api/a', { silent: true })).rejects.toBeInstanceOf(ApiError);
    expect(onError).toHaveBeenCalledOnce();
  });

  it('maps a network failure to status 0', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')));
    await expect(http.get('/api/a')).rejects.toMatchObject({ status: 0 });
    expect(onError).toHaveBeenCalledOnce();
  });
});
