const timeFmt = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' });
const dayFmt = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: 'short' });
const fullFmt = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
});
const longDayFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', weekday: 'long' });

/** API sends UTC ISO 8601; a value without a zone suffix is treated as UTC. */
export function parseIso(iso: string): Date {
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
}

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** Today — «14:05», otherwise «05 нояб.». */
export function formatShort(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return '';
  const d = parseIso(iso);
  return sameDay(d, now) ? timeFmt.format(d) : dayFmt.format(d);
}

export function formatTime(iso: string | null | undefined): string {
  return iso ? timeFmt.format(parseIso(iso)) : '';
}

export function formatDateTime(iso: string | null | undefined): string {
  return iso ? fullFmt.format(parseIso(iso)) : '';
}

/** Day key for grouping the timeline (local date). */
export function dayKey(iso: string): string {
  const d = parseIso(iso);
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
}

export function dayLabel(iso: string, now = new Date()): string {
  const d = parseIso(iso);
  if (sameDay(d, now)) return 'Сегодня';
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  return sameDay(d, yesterday) ? 'Вчера' : longDayFmt.format(d);
}

/** Seconds -> «3:07» (calls, voice notes); null -> ''. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '';
  const total = Math.max(0, Math.round(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

const intFmt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });

export function formatInt(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : intFmt.format(v);
}

/** Model call cost: small amounts need 4 decimals. */
export function formatUsd(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  return `$${v.toFixed(v < 0.01 ? 4 : 3)}`;
}

export function formatMs(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  return v >= 1000 ? `${(v / 1000).toFixed(1)} с` : `${Math.round(v)} мс`;
}
