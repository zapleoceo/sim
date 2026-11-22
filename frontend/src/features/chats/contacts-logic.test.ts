import { describe, expect, it } from 'vitest';
import {
  contactDraftError, contactStatus, defaultDueLocal, localToIso, parseLocalInput, toLocalInput,
} from './contacts-logic';

describe('defaultDueLocal', () => {
  it('is in an hour, rounded up to 5 minutes', () => {
    expect(defaultDueLocal(new Date(2026, 10, 22, 10, 2, 30))).toBe('2026-11-22T11:05');
    expect(defaultDueLocal(new Date(2026, 10, 22, 10, 5, 0))).toBe('2026-11-22T11:05');
    expect(defaultDueLocal(new Date(2026, 10, 22, 10, 5, 1))).toBe('2026-11-22T11:10');
    expect(defaultDueLocal(new Date(2026, 10, 22, 23, 58))).toBe('2026-11-23T01:00');
  });
});

describe('localToIso', () => {
  it('keeps the local moment and adds the browser’s offset', () => {
    const iso = localToIso('2026-11-22T18:30');
    expect(iso).toMatch(/^2026-11-22T18:30:00[+-]\d\d:\d\d$/);
    expect(new Date(iso as string).getTime()).toBe(new Date(2026, 10, 22, 18, 30).getTime());
    expect(localToIso('2026-11-22T18:30:15')).toMatch(/^2026-11-22T18:30:15[+-]\d\d:\d\d$/);
  });

  it('rejects an incomplete value', () => {
    expect(localToIso('')).toBeNull();
    expect(localToIso('2026-11-22')).toBeNull();
    expect(parseLocalInput('завтра')).toBeNull();
  });

  it('round-trips with the input value', () => {
    expect(toLocalInput(parseLocalInput('2026-01-05T07:09') as Date)).toBe('2026-01-05T07:09');
  });
});

describe('contactDraftError', () => {
  const now = new Date(2026, 10, 22, 12, 0);

  it('needs a topic, a full time in the future', () => {
    expect(contactDraftError({ kind: 'call', due: '2026-11-22T13:00', topic: ' ' }, now)).toBe('Напишите, о чём звонок');
    expect(contactDraftError({ kind: 'message', due: '2026-11-22T13:00', topic: '' }, now)).toBe('Напишите, о чём написать');
    expect(contactDraftError({ kind: 'message', due: '2026-11-22T13:00', topic: 'я'.repeat(501) }, now)).toBe('Не длиннее 500 символов');
    expect(contactDraftError({ kind: 'message', due: '', topic: 'привет' }, now)).toBe('Укажите дату и время');
    expect(contactDraftError({ kind: 'message', due: '2026-11-22T11:00', topic: 'привет' }, now)).toBe('Время уже прошло');
    expect(contactDraftError({ kind: 'call', due: '2026-11-22T13:00', topic: 'про встречу' }, now)).toBeNull();
  });
});

describe('contactStatus', () => {
  it('names every status and keeps an unknown one', () => {
    expect(contactStatus('planned').label).toBe('Запланировано');
    expect(contactStatus('started').label).toBe('Выполняется');
    expect(contactStatus('done').label).toBe('Готово');
    expect(contactStatus('failed').label).toBe('Не вышло');
    expect(contactStatus('cancelled').label).toBe('Отменено');
    expect(contactStatus('paused')).toEqual({ label: 'paused', variant: 'muted' });
  });
});
