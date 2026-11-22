import type { Ambience, BusyLevel, DayEvent, Hobby, Home, LifePerson, Look, PersonaLife, Place, Room, RoutineSlot } from '@/lib/types';

/** Pure helpers of the «Жизнь» section (docs/frontend.md); mirrors sim/domain/life.py checks. */

export const DAY_CODES = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'] as const;
export const DAY_MINUTES = 24 * 60;

export const BUSY_OPTIONS: readonly { value: BusyLevel; label: string }[] = [
  { value: 'free', label: 'свободна' },
  { value: 'partial', label: 'занята' },
  { value: 'away', label: 'недоступна' },
];

export const AMBIENCE_OPTIONS: readonly { value: Ambience; label: string }[] = [
  { value: 'none', label: 'тишина' },
  { value: 'quiet_home', label: 'дома' },
  { value: 'kitchen', label: 'кухня' },
  { value: 'street', label: 'улица' },
  { value: 'park', label: 'парк' },
  { value: 'cafe', label: 'кафе' },
  { value: 'car', label: 'машина' },
  { value: 'transport', label: 'транспорт' },
  { value: 'office', label: 'офис' },
];

export function busyLabel(busy: BusyLevel): string {
  return BUSY_OPTIONS.find((o) => o.value === busy)?.label ?? busy;
}

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

/** "07:30" -> 450, or null when it is not a time. */
export function toMinutes(hhmm: string): number | null {
  if (!HHMM.test(hhmm)) return null;
  const [h, m] = hhmm.split(':').map(Number);
  return (h ?? 0) * 60 + (m ?? 0);
}

/** [start, end] in minutes; an end not after the start is on the next day. */
export function span(start: string, end: string): [number, number] | null {
  const a = toMinutes(start);
  const b = toMinutes(end);
  if (a === null || b === null) return null;
  return [a, b > a ? b : b + DAY_MINUTES];
}

export function toggleDay(days: readonly number[], day: number): number[] {
  return days.includes(day) ? days.filter((d) => d !== day) : [...days, day].sort((a, b) => a - b);
}

/** "Андрей, мама" <-> ["Андрей", "мама"]. */
export function splitNames(text: string): string[] {
  return text.split(',').map((s) => s.trim()).filter(Boolean);
}

export function joinNames(names: readonly string[]): string {
  return names.join(', ');
}

export const newRoutineSlot = (): RoutineSlot => ({
  days: [0, 1, 2, 3, 4], start: '09:00', end: '10:00', activity: '', place: '', with_whom: [], busy: 'free',
  outfit: '', ambience: null,
});
export const newPerson = (): LifePerson => ({ name: '', relation: '', traits: '', contact: '', notes: '' });
export const newHobby = (): Hobby => ({ name: '', details: '', frequency: '' });
export const newPlace = (): Place => ({ name: '', kind: '', details: '' });
export const newDayEvent = (): DayEvent => ({
  start: '09:00', end: '10:00', title: '', place: '', with_whom: [], busy: 'free', details: '', outfit: '',
  ambience: 'quiet_home',
});

export const newRoom = (): Room => ({ name: '', details: '', layout: '', lamps: '', window_view: '' });
export const EMPTY_LOOK: Look = { nails: '', earrings: '', jewelry: '', hair: '', other: '' };
export const EMPTY_HOME: Home = { details: '', rooms: [] };
export const EMPTY_LIFE: PersonaLife = { routine: [], people: [], hobbies: [], places: [], look: EMPTY_LOOK, home: EMPTY_HOME };

/** The «Облик» fields, in the order of the form. */
export const LOOK_FIELDS: readonly { key: keyof Look; label: string; placeholder: string }[] = [
  { key: 'nails', label: 'Маникюр', placeholder: 'короткие миндалевидные, нюдово-розовый лак' },
  { key: 'earrings', label: 'Серьги', placeholder: 'маленькие золотые кольца' },
  { key: 'jewelry', label: 'Украшения и часы', placeholder: 'тонкая золотая цепочка, часы на левой руке' },
  { key: 'hair', label: 'Укладка', placeholder: 'каре, пробор сбоку' },
  { key: 'other', label: 'Прочее', placeholder: 'очки для чтения, тату, родинка…' },
];

/** What would make PUT /personas/{id}/life fail, in Russian, before sending it. */
export function worldErrors(life: PersonaLife): string[] {
  const errors: string[] = [];
  life.routine.forEach((slot, i) => {
    const n = i + 1;
    if (!slot.days.length) errors.push(`Распорядок, строка ${n}: выберите дни`);
    if (!span(slot.start, slot.end)) errors.push(`Распорядок, строка ${n}: время ЧЧ:ММ`);
    else if (slot.start === slot.end) errors.push(`Распорядок, строка ${n}: начало и конец совпадают`);
    if (!slot.activity.trim()) errors.push(`Распорядок, строка ${n}: что она делает`);
  });
  const named: [string, { name: string }[]][] = [['Люди', life.people], ['Увлечения', life.hobbies], ['Места', life.places], ['Комнаты', life.home.rooms]];
  for (const [title, rows] of named) {
    rows.forEach((row, i) => {
      if (!row.name.trim()) errors.push(`${title}, строка ${i + 1}: укажите название`);
    });
  }
  return errors;
}

/** Times in order, no overlaps, only the last event past midnight (sim/domain/life.day_errors). */
export function dayErrors(events: readonly DayEvent[]): string[] {
  const errors: string[] = [];
  let previousEnd: number | null = null;
  events.forEach((event, i) => {
    const n = i + 1;
    const range = span(event.start, event.end);
    if (!event.title.trim()) errors.push(`Событие ${n}: что она делает`);
    if (!range || event.start === event.end) {
      errors.push(`Событие ${n}: время ЧЧ:ММ, начало и конец разные`);
      return;
    }
    if (range[1] > DAY_MINUTES && i !== events.length - 1) errors.push(`Событие ${n}: через полночь может идти только последнее`);
    if (previousEnd !== null && range[0] < previousEnd) errors.push(`Событие ${n}: накладывается на предыдущее`);
    previousEnd = range[1];
  });
  return errors;
}

/** Index of the event she is in at `minute` (minutes since her midnight), or -1. */
export function currentEventIndex(events: readonly DayEvent[], minute: number): number {
  return events.findIndex((event) => {
    const range = span(event.start, event.end);
    return range !== null && range[0] <= minute && minute < range[1];
  });
}

/** "2026-11-21" + 1 -> "2026-11-22" (calendar dates, no time zone involved). */
export function addDays(iso: string, days: number): string {
  const [y, m, d] = iso.split('-').map(Number);
  const date = new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, (d ?? 1) + days));
  return date.toISOString().slice(0, 10);
}

const WEEKDAYS = ['воскресенье', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота'];
const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

/** "2026-11-21" -> «суббота, 21 ноября». */
export function dayTitle(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  const date = new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1));
  return `${WEEKDAYS[date.getUTCDay()]}, ${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]}`;
}
