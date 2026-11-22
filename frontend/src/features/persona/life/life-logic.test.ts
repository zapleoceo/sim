import { describe, expect, it } from 'vitest';
import type { DayEvent, PersonaLife } from '@/lib/types';
import {
  addDays, currentEventIndex, dayErrors, dayTitle, EMPTY_LIFE, EMPTY_LOOK, joinNames, newDayEvent, newRoom, newRoutineSlot, span,
  splitNames, toMinutes, toggleDay, worldErrors,
} from './life-logic';

const ev = (start: string, end: string, title = 'дела'): DayEvent => ({ ...newDayEvent(), start, end, title });

describe('time helpers', () => {
  it('parses HH:MM and spans past midnight', () => {
    expect(toMinutes('07:30')).toBe(450);
    expect(toMinutes('7:30')).toBeNull();
    expect(span('23:00', '06:30')).toEqual([1380, 1830]);
    expect(span('x', '06:30')).toBeNull();
  });

  it('toggles weekdays in order', () => {
    expect(toggleDay([0, 2], 1)).toEqual([0, 1, 2]);
    expect(toggleDay([0, 2], 2)).toEqual([0]);
  });

  it('dates without time zones', () => {
    expect(addDays('2026-11-30', 1)).toBe('2026-12-01');
    expect(dayTitle('2026-11-21')).toBe('суббота, 21 ноября');
  });

  it('names list', () => {
    expect(splitNames(' Андрей,  мама ,')).toEqual(['Андрей', 'мама']);
    expect(joinNames(['Андрей', 'мама'])).toBe('Андрей, мама');
  });
});

describe('checks before saving', () => {
  it('world: days, times, activity and names', () => {
    const life: PersonaLife = {
      routine: [{ ...newRoutineSlot(), days: [], activity: '' }, { ...newRoutineSlot(), activity: 'бег', start: '10:00', end: '10:00' }],
      people: [{ name: ' ', relation: '', traits: '', contact: '', notes: '' }],
      hobbies: [],
      places: [],
      look: EMPTY_LOOK,
      home: { details: '', rooms: [{ ...newRoom(), details: 'кровать' }] },
    };
    expect(worldErrors(life)).toEqual([
      'Распорядок, строка 1: выберите дни',
      'Распорядок, строка 1: что она делает',
      'Распорядок, строка 2: начало и конец совпадают',
      'Люди, строка 1: укажите название',
      'Комнаты, строка 1: укажите название',
    ]);
    expect(worldErrors({ ...EMPTY_LIFE, routine: [{ ...newRoutineSlot(), activity: 'кофе' }] })).toEqual([]);
  });

  it('day: order, overlaps and midnight', () => {
    expect(dayErrors([ev('08:00', '09:00'), ev('09:00', '10:00')])).toEqual([]);
    expect(dayErrors([ev('08:00', '09:30'), ev('09:00', '10:00')])).toEqual(['Событие 2: накладывается на предыдущее']);
    expect(dayErrors([ev('22:00', '01:00'), ev('23:00', '23:30')])[0]).toContain('через полночь');
    expect(dayErrors([ev('22:00', '01:00', ' ')])).toEqual(['Событие 1: что она делает']);
  });

  it('finds the current event', () => {
    const events = [ev('08:00', '09:00'), ev('19:00', '21:00'), ev('23:00', '01:00')];
    expect(currentEventIndex(events, 19 * 60 + 30)).toBe(1);
    expect(currentEventIndex(events, 12 * 60)).toBe(-1);
    expect(currentEventIndex(events, 23 * 60 + 30)).toBe(2);
  });
});
