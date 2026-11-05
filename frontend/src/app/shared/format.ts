const timeFmt = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' });
const dayFmt = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: 'short' });
const fullFmt = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
const longDayFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', weekday: 'long' });

function parse(iso: string): Date {
  // API отдаёт UTC; если суффикса зоны нет — считаем UTC.
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
}

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** Сегодня — «14:05», иначе — «05 нояб.». */
export function formatShort(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return '';
  const d = parse(iso);
  return sameDay(d, now) ? timeFmt.format(d) : dayFmt.format(d);
}

export function formatTime(iso: string): string {
  return timeFmt.format(parse(iso));
}

export function formatDateTime(iso: string | null | undefined): string {
  return iso ? fullFmt.format(parse(iso)) : '';
}

/** Ключ дня для группировки сообщений (локальная дата). */
export function dayKey(iso: string): string {
  const d = parse(iso);
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
}

export function dayLabel(iso: string, now = new Date()): string {
  const d = parse(iso);
  if (sameDay(d, now)) return 'Сегодня';
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  return sameDay(d, yesterday) ? 'Вчера' : longDayFmt.format(d);
}

/** Значение для <input type="datetime-local"> из ISO UTC. */
export function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = parse(iso);
  const p = (n: number): string => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** Из значения datetime-local (локальное время) — в ISO UTC. */
export function fromLocalInput(value: string): string | null {
  return value ? new Date(value).toISOString() : null;
}
