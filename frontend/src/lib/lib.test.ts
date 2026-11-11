import { ApiError } from './api/http';
import { authStore, defaultLanding, hasOwnerRights } from './auth';
import { errorDetail, errorMessage } from './errors';
import {
  dayLabel, formatDayMonth, formatShort, isoToLocalInput, localInputToIso, toIsoDate,
} from './format';
import { checkStageDrop } from './funnel-rules';
import type { Stage } from './models';
import { NAV, visibleNav } from './nav';
import { shouldRetry } from './query';
import { parseActivitySearch, parseInboxSearch, parseLoginSearch, safeReturnUrl } from './search';
import { resolveDark } from './theme';
import { initials, positiveInt, upsertById } from './utils';
import { fakeApi, reply } from '@/test/fake-api';

describe('errorMessage', () => {
  it.each([
    [0, 'Нет связи с сервером'],
    [422, 'Проверьте введённые данные'],
    [403, 'Недостаточно прав для этого действия'],
    [404, 'Не найдено — возможно, запись уже удалена'],
    [409, 'Данные изменились. Обновите страницу и повторите'],
    [429, 'Слишком много запросов. Подождите немного'],
    [503, 'Сервис временно недоступен. Попробуйте позже'],
    [500, 'Ошибка сервера. Попробуйте позже'],
    [418, 'Не удалось выполнить действие'],
  ])('status %i', (status, text) => {
    expect(errorMessage(new ApiError(status, null, '/x'))).toBe(text);
  });

  it('falls back for non-API errors and reads FastAPI detail', () => {
    expect(errorMessage(new Error('x'))).toBe('Что-то пошло не так. Попробуйте ещё раз');
    expect(errorDetail(new ApiError(502, { detail: 'Broker unavailable' }, '/x'))).toBe('Broker unavailable');
    expect(errorDetail(new ApiError(422, { detail: [{ loc: [] }] }, '/x'))).toBe('');
  });
});

describe('roles and navigation', () => {
  it('owner rights for owner and superadmin only', () => {
    expect(hasOwnerRights('owner')).toBe(true);
    expect(hasOwnerRights('superadmin')).toBe(true);
    expect(hasOwnerRights('operator')).toBe(false);
    expect(hasOwnerRights(undefined)).toBe(false);
    expect(defaultLanding('owner')).toBe('/studio');
    expect(defaultLanding('operator')).toBe('/inbox');
  });

  it('operator sees work sections only, superadmin sees everything', () => {
    const operator = visibleNav('operator').map((i) => i.path);
    expect(operator).toEqual(['/inbox', '/clients', '/funnel', '/dashboard', '/activity']);
    expect(visibleNav('owner').map((i) => i.path)).toContain('/settings');
    expect(visibleNav('owner').map((i) => i.path)).toContain('/guard');
    expect(visibleNav('owner').map((i) => i.path)).not.toContain('/admins');
    expect(visibleNav('superadmin')).toHaveLength(NAV.length);
  });
});

describe('authStore', () => {
  it('restores the session once from the cookie', async () => {
    const log = fakeApi({
      'GET /api/auth/me': {
        user: { id: 1, role: 'owner', telegram_id: 1, tg_username: null, display_name: 'D', photo_url: '' },
        csrf_token: 'c',
      },
    });
    await expect(authStore.restore()).resolves.toBe(true);
    await expect(authStore.restore()).resolves.toBe(true);
    expect(log).toHaveLength(1);
    expect(authStore.get().csrf).toBe('c');
  });

  it('reports signed-out on 401 and clears on logout', async () => {
    fakeApi({ 'GET /api/auth/me': reply(401), 'POST /api/auth/logout': reply(204) });
    await expect(authStore.restore()).resolves.toBe(false);
    authStore.apply({ user: { id: 1, role: 'operator', telegram_id: 1, tg_username: null, display_name: 'D', photo_url: '' }, csrf_token: 'c' });
    await authStore.logout();
    expect(authStore.get().user).toBeNull();
  });
});

describe('format', () => {
  const now = new Date(2026, 10, 11, 15, 0);

  it('short time today, day-month otherwise; UTC without suffix', () => {
    expect(formatShort(null)).toBe('');
    expect(formatShort(new Date(2026, 10, 11, 9, 5).toISOString(), now)).toMatch(/09:05/);
    expect(formatShort(new Date(2026, 10, 1, 9, 5).toISOString(), now)).toMatch(/01/);
  });

  it('day labels', () => {
    expect(dayLabel(new Date(2026, 10, 11, 1).toISOString(), now)).toBe('Сегодня');
    expect(dayLabel(new Date(2026, 10, 10, 23).toISOString(), now)).toBe('Вчера');
  });

  it('datetime-local round trip and invalid input', () => {
    const iso = new Date(2026, 10, 11, 14, 5).toISOString();
    expect(isoToLocalInput(iso)).toBe('2026-11-11T14:05');
    expect(localInputToIso('2026-11-11T14:05')).toBe(iso);
    expect(localInputToIso('')).toBeNull();
    expect(localInputToIso('garbage')).toBeNull();
    expect(isoToLocalInput(null)).toBe('');
  });

  it('iso date and day-month', () => {
    expect(toIsoDate(new Date(2026, 0, 5))).toBe('2026-01-05');
    expect(formatDayMonth('2026-10-08')).toBe('08.10');
  });
});

describe('checkStageDrop', () => {
  const reachable: Stage[] = ['first_contact', 'engaged', 'needs_identified', 'interested', 'handed_to_partner'];

  it('allows moves between AI-reachable stages, forward and back', () => {
    for (const a of reachable) for (const b of reachable) if (a !== b) expect(checkStageDrop(a, b).ok).toBe(true);
  });

  it('rejects partner-only targets and moves out of them; same column is a silent no-op', () => {
    const v = checkStageDrop('interested', 'paid');
    expect(v.ok).toBe(false);
    if (!v.ok) expect(v.reason).toContain('подтверждается только данными партнёра');
    expect(checkStageDrop('registered', 'first_contact').ok).toBe(false);
    const same = checkStageDrop('engaged', 'engaged');
    expect(same.ok === false && same.reason === '').toBe(true);
  });
});

describe('search params', () => {
  it('accepts only same-app return paths', () => {
    expect(safeReturnUrl('/inbox?d=3')).toBe('/inbox?d=3');
    expect(safeReturnUrl('//evil.example')).toBeUndefined();
    expect(safeReturnUrl('https://evil.example')).toBeUndefined();
    expect(safeReturnUrl('/login')).toBeUndefined();
    expect(parseLoginSearch({ returnUrl: '/clients' })).toEqual({ returnUrl: '/clients' });
  });

  it('parses dialog and activity params defensively', () => {
    expect(parseInboxSearch({ d: '12' })).toEqual({ d: 12 });
    expect(parseInboxSearch({ d: '-1' })).toEqual({});
    expect(parseActivitySearch({ tab: 'llm', channel_id: 4 })).toEqual({ tab: 'llm', channel_id: 4 });
    expect(parseActivitySearch({ tab: 'x', channel_id: 'abc' })).toEqual({});
  });
});

describe('utils', () => {
  it('initials, upsert and positive ints', () => {
    expect(initials('Анна Петрова Иванова')).toBe('АП');
    expect(initials('  ')).toBe('?');
    expect(upsertById([{ id: 1, v: 'a' }], { id: 1, v: 'b' })).toEqual([{ id: 1, v: 'b' }]);
    expect(upsertById([{ id: 1 }], { id: 2 }, true)).toEqual([{ id: 2 }, { id: 1 }]);
    expect(positiveInt('7')).toBe(7);
    expect(positiveInt('1.5')).toBeNull();
    expect(positiveInt(undefined)).toBeNull();
  });

  it('retries only network and server errors, once', () => {
    expect(shouldRetry(0, new ApiError(404, null, '/x'))).toBe(false);
    expect(shouldRetry(0, new ApiError(503, null, '/x'))).toBe(true);
    expect(shouldRetry(1, new ApiError(503, null, '/x'))).toBe(false);
  });

  it('theme resolution', () => {
    expect(resolveDark('dark', false)).toBe(true);
    expect(resolveDark('system', true)).toBe(true);
    expect(resolveDark('light', true)).toBe(false);
  });
});
