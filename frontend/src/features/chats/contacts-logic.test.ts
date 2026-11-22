import { describe, expect, it } from 'vitest';
import {
  DUE_NOW, QUICK_DUES, contactDraftError, contactStatus, dueIso, dueText, joinDue, localToIso, parseLocalInput, splitDue,
  toLocalInput,
} from './contacts-logic';

const quick = (id: string, now: Date) => QUICK_DUES.find((q) => q.id === id)?.due(now);

describe('when it goes', () => {
  const now = new Date(2026, 10, 22, 18, 30, 40);

  it('is «now» by default and the usual times are one tap', () => {
    expect(quick('now', now)).toBe(DUE_NOW);
    expect(quick('15m', now)).toBe('2026-11-22T18:45');
    expect(quick('1h', now)).toBe('2026-11-22T19:30');
    expect(quick('evening', now)).toBe('2026-11-22T19:00');
    expect(quick('evening', new Date(2026, 10, 22, 20, 0))).toBe('2026-11-23T19:00');   // past: tomorrow
    expect(quick('tomorrow', now)).toBe('2026-11-23T10:00');
  });

  it('«now» is the moment of sending', () => {
    expect(new Date(dueIso(DUE_NOW, now) as string).getTime()).toBe(now.getTime());
    expect(dueIso('2026-11-22T19:00', now)).toBe(localToIso('2026-11-22T19:00'));
    expect(dueText(DUE_NOW)).toContain('сразу');
    expect(dueText('2026-11-22T19:05')).toBe('22.11 в 19:05');
  });

  it('splits into the date and the time fields and back', () => {
    expect(splitDue('2026-11-23T10:00')).toEqual({ date: '2026-11-23', time: '10:00' });
    expect(splitDue(DUE_NOW, now)).toEqual({ date: '2026-11-22', time: '18:30' });
    expect(joinDue('2026-11-23', '07:15')).toBe('2026-11-23T07:15');
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
    expect(contactDraftError({ kind: 'message', due: DUE_NOW, topic: 'привет' }, now)).toBeNull();
    expect(contactDraftError({ kind: 'message', due: '2026-11-22', topic: 'привет' }, now)).toBe('Укажите дату и время');
    expect(contactDraftError({ kind: 'message', due: '2026-11-22T11:00', topic: 'привет' }, now)).toBe('Время уже прошло');
    expect(contactDraftError({ kind: 'message', due: '2026-11-22T12:00', topic: 'привет' }, now)).toBeNull();   // a minute is «now»
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
