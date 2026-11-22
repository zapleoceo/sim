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
const HOUR_MS = 60 * 60 * 1000;
const STEP_MIN = 5;

const pad = (n: number): string => String(n).padStart(2, '0');

/** A local moment as the value of `<input type="datetime-local">`: «2026-11-22T18:30». */
export function toLocalInput(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** The form's default time: in an hour, rounded up to 5 minutes. */
export function defaultDueLocal(now: Date = new Date()): string {
  const d = new Date(now.getTime() + HOUR_MS);
  const extra = d.getSeconds() > 0 || d.getMilliseconds() > 0 ? 1 : 0;
  d.setSeconds(0, 0);
  d.setMinutes(Math.ceil((d.getMinutes() + extra) / STEP_MIN) * STEP_MIN);
  return toLocalInput(d);
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
  const due = parseLocalInput(draft.due);
  if (!due) return 'Укажите дату и время';
  if (due.getTime() <= now.getTime()) return 'Время уже прошло';
  return null;
}
