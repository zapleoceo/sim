import type { VariantProps } from 'class-variance-authority';
import type { SegmentedOption } from '@/components/segmented';
import type { badgeVariants } from '@/components/ui/badge';
import type { ContactKind, ContactStatus } from '@/lib/types';

type BadgeVariant = NonNullable<VariantProps<typeof badgeVariants>['variant']>;

/** «Запланировать» (docs/calls.md): she writes or calls the client at a time, about a topic. */
export const CONTACT_KINDS: readonly SegmentedOption<ContactKind>[] = [
  { value: 'message', label: 'Сообщение' },
  { value: 'call', label: 'Звонок' },
];

export const CONTACT_STATUS: Record<ContactStatus, { label: string; variant: BadgeVariant }> = {
  planned: { label: 'Запланировано', variant: 'secondary' },
  started: { label: 'Выполняется', variant: 'warning' },
  done: { label: 'Готово', variant: 'success' },
  failed: { label: 'Не вышло', variant: 'destructive' },
  cancelled: { label: 'Отменено', variant: 'muted' },
};

/** Unknown statuses (a newer server) are shown as they are. */
export function contactStatus(status: string): { label: string; variant: BadgeVariant } {
  return CONTACT_STATUS[status as ContactStatus] ?? { label: status, variant: 'muted' };
}

export const TOPIC_MAX = 500;
const MINUTE_MS = 60 * 1000;
/** A time this much in the past still counts as «now» (the form was open a little). */
const PAST_GRACE_MS = MINUTE_MS;
/** `ContactDraft.due` of «Сейчас»: the moment of sending, not of opening the form. */
export const DUE_NOW = '';

const pad = (n: number): string => String(n).padStart(2, '0');

/** A local moment as the value of `<input type="datetime-local">`: «2026-11-22T18:30». */
export function toLocalInput(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export interface QuickDue {
  id: string;
  label: string;
  /** The local `datetime-local` value it sets; `DUE_NOW` for «Сейчас». */
  due: (now: Date) => string;
}

const inMinutes = (minutes: number) => (now: Date): string =>
  toLocalInput(new Date(now.getTime() + minutes * MINUTE_MS));

/** Today at `hour` if that is still ahead, else tomorrow at it. */
const atHour = (hour: number, dayOffset = 0) => (now: Date): string => {
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + dayOffset, hour, 0);
  if (dayOffset === 0 && d.getTime() <= now.getTime()) d.setDate(d.getDate() + 1);
  return toLocalInput(d);
};

/** One tap for the usual times; the date and time fields stay for anything else. */
export const QUICK_DUES: readonly QuickDue[] = [
  { id: 'now', label: 'Сейчас', due: () => DUE_NOW },
  { id: '15m', label: 'Через 15 мин', due: inMinutes(15) },
  { id: '1h', label: 'Через час', due: inMinutes(60) },
  { id: 'evening', label: 'Вечером в 19:00', due: atHour(19) },
  { id: 'tomorrow', label: 'Завтра в 10:00', due: atHour(10, 1) },
];

/** The date and the time fields of a `datetime-local` value (`DUE_NOW` shows `now`). */
export function splitDue(due: string, now: Date = new Date()): { date: string; time: string } {
  const value = due === DUE_NOW ? toLocalInput(now) : due;
  const [date = '', time = ''] = value.split('T');
  return { date, time: time.slice(0, 5) };
}

export function joinDue(date: string, time: string): string {
  return `${date}T${time}`;
}

/** What the form will send, said in words: «сразу» or «22.11 в 18:30». */
export function dueText(due: string): string {
  if (due === DUE_NOW) return 'сразу, как нажмёте «Запланировать»';
  const d = parseLocalInput(due);
  return d ? `${pad(d.getDate())}.${pad(d.getMonth() + 1)} в ${pad(d.getHours())}:${pad(d.getMinutes())}` : '';
}

/** A `datetime-local` value as a Date in the browser's zone; null when it is not a full date and time. */
export function parseLocalInput(value: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value.trim());
  if (!m) return null;
  const [, y, mo, d, h, mi, s] = m;
  const date = new Date(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s ?? 0));
  return Number.isNaN(date.getTime()) ? null : date;
}

/** A local moment as ISO 8601 with the browser's offset: «2026-11-22T18:30:00+02:00». */
export function toIsoWithOffset(d: Date): string {
  const east = -d.getTimezoneOffset();
  const sign = east >= 0 ? '+' : '-';
  const abs = Math.abs(east);
  return `${toLocalInput(d)}:${pad(d.getSeconds())}${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

/** `datetime-local` value -> ISO with offset (what POST /chats/{id}/contacts takes), or null. */
export function localToIso(value: string): string | null {
  const d = parseLocalInput(value);
  return d ? toIsoWithOffset(d) : null;
}

/** The `due_at` to send: «Сейчас» is the moment of sending. */
export function dueIso(due: string, now: Date = new Date()): string | null {
  return due === DUE_NOW ? toIsoWithOffset(now) : localToIso(due);
}

export interface ContactDraft {
  kind: ContactKind;
  due: string;
  topic: string;
}

/** The first problem of the form, or null when it can be sent. */
export function contactDraftError(draft: ContactDraft, now: Date = new Date()): string | null {
  const topic = draft.topic.trim();
  if (!topic) return draft.kind === 'call' ? 'Напишите, о чём звонок' : 'Напишите, о чём написать';
  if (topic.length > TOPIC_MAX) return `Не длиннее ${TOPIC_MAX} символов`;
  if (draft.due === DUE_NOW) return null;
  const due = parseLocalInput(draft.due);
  if (!due) return 'Укажите дату и время';
  if (due.getTime() < now.getTime() - PAST_GRACE_MS) return 'Время уже прошло';
  return null;
}
