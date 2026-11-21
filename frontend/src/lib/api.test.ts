import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeApi, reply } from '@/test/fake-api';
import { ApiError, api, apiUrl, setCsrfToken, setUnauthorizedHandler } from './api';

afterEach(() => {
  setCsrfToken(null);
  setUnauthorizedHandler(() => undefined);
});

describe('apiUrl', () => {
  it('prefixes /api and drops empty params', () => {
    expect(apiUrl('/chats', { q: '', limit: 50, before: null, x: undefined })).toBe('/api/chats?limit=50');
    expect(apiUrl('/persona')).toBe('/api/persona');
  });
});

describe('api', () => {
  it('sends the cookie and puts X-CSRF-Token on every non-GET request only', async () => {
    const log = fakeApi({
      'GET /api/persona': { id: 1 },
      'PUT /api/persona': { id: 1 },
      'POST /api/chats/5/read': reply(204),
      'DELETE /api/media/3': reply(204),
    });
    setCsrfToken('tok-1');

    await api.get('/persona');
    await api.put('/persona', { name: 'Марина' });
    await expect(api.post('/chats/5/read')).resolves.toBeUndefined();
    await api.delete('/media/3');

    expect(log.map((r) => r.credentials)).toEqual(['include', 'include', 'include', 'include']);
    expect(log[0]?.headers['X-CSRF-Token']).toBeUndefined();
    expect(log[1]?.headers['X-CSRF-Token']).toBe('tok-1');
    expect(log[1]?.headers['Content-Type']).toBe('application/json');
    expect(log[1]?.body).toEqual({ name: 'Марина' });
    expect(log[2]?.headers['X-CSRF-Token']).toBe('tok-1');
    expect(log[3]?.headers['X-CSRF-Token']).toBe('tok-1');
  });

  it('sends FormData as is (multipart), without a JSON content type', async () => {
    const log = fakeApi({ 'POST /api/media': { id: 9 } });
    setCsrfToken('tok-2');
    const form = new FormData();
    form.set('caption', 'утро');
    await api.post('/media', form);
    expect(log[0]?.body).toBeInstanceOf(FormData);
    expect(log[0]?.headers['Content-Type']).toBeUndefined();
    expect(log[0]?.headers['X-CSRF-Token']).toBe('tok-2');
  });

  it('turns {error} into a thrown ApiError with that text', async () => {
    fakeApi({ 'PATCH /api/clients/1': reply(422, { error: 'Телефон в неверном формате' }) });
    const err = await api.patch('/clients/1', { phone: 'x' }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe('Телефон в неверном формате');
    expect((err as ApiError).status).toBe(422);
  });

  it('falls back to a readable message without {error}', async () => {
    fakeApi({ 'GET /api/persona': reply(500, null) });
    await expect(api.get('/persona')).rejects.toThrow('Ошибка сервера, попробуйте позже');
  });

  it('calls the unauthorized handler on 401 unless asked not to', async () => {
    fakeApi({ 'GET /api/chats': reply(401, { error: 'Нужно войти' }), 'GET /api/auth/me': reply(401, { error: 'Нужно войти' }) });
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    await expect(api.get('/chats')).rejects.toThrow('Нужно войти');
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    await expect(api.get('/auth/me', { noAuthRedirect: true })).rejects.toThrow();
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it('reports a network failure as status 0', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    }));
    const err = await api.get('/persona').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(0);
    expect((err as ApiError).message).toBe('Нет связи с сервером');
  });
});
