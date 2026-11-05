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

/** ISO UTC -> локальный Date для MatDatepicker/MatTimepicker. */
export function toDate(iso: string | null | undefined): Date | null {
  return iso ? parse(iso) : null;
}

/** Date из пикеров -> ISO UTC (или null, если значение пустое/невалидное). */
export function fromDate(value: Date | null): string | null {
  return value && !Number.isNaN(value.getTime()) ? value.toISOString() : null;
}

const intFmt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });

/** Целое число с разделителями разрядов (один общий форматтер). */
export function formatInt(v: number): string {
  return intFmt.format(v);
}

const moneyFmt = new Map<string, Intl.NumberFormat>();

/** Деньги с валютой (по умолчанию USD — API не отдаёт валюту сводки). Форматтеры кешируются. */
export function formatMoney(v: number, currency = 'USD', fractionDigits = 0): string {
  const key = `${currency}:${fractionDigits}`;
  let f = moneyFmt.get(key);
  if (!f) {
    f = new Intl.NumberFormat('ru-RU', { style: 'currency', currency, minimumFractionDigits: fractionDigits, maximumFractionDigits: fractionDigits });
    moneyFmt.set(key, f);
  }
  return f.format(v);
}

/** «2026-10-08» -> «08.10». */
export function formatDayMonth(isoDate: string): string {
  const [, m, d] = isoDate.split('-');
  return d && m ? `${d}.${m}` : isoDate;
}
