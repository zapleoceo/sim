import { parseIso } from '@/lib/format';
import type { PersonaStatus, PersonaStatusTone } from '@/lib/types';

/** How often the status and its relative time are re-read (docs/attention.md, «Статус для команды»). */
export const STATUS_REFRESH_MS = 30_000;

export const TEAM_ONLY = 'Видно только команде: клиенту и агентам не показывается';

/** Tinted background and readable text per tone, light and dark. */
export const TONE_CLASS: Record<PersonaStatusTone, string> = {
  ok: 'bg-success/15 text-success',
  wait: 'bg-primary/10 text-primary',
  busy: 'bg-cyan-500/15 text-cyan-700 dark:text-cyan-300',
  warn: 'bg-warning/15 text-warning',
  off: 'bg-muted text-muted-foreground',
};

/** «через 12 мин», «через 1 ч 5 мин»; '' without a time; «уже сейчас» once it has passed. */
export function relativeUntil(until: string | null, now: Date): string {
  if (!until) return '';
  const ms = parseIso(until).getTime() - now.getTime();
  if (ms <= 0) return 'уже сейчас';
  if (ms < 60_000) return 'меньше чем через минуту';
  const minutes = Math.ceil(ms / 60_000);
  if (minutes < 60) return `через ${minutes} мин`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `через ${hours} ч ${rest} мин` : `через ${hours} ч`;
}

/** «до 20:40 по её времени · через 12 мин»; '' when the pause has no known end. */
export function untilLine(status: Pick<PersonaStatus, 'until' | 'local_until'>, now: Date): string {
  if (!status.local_until) return '';
  const relative = relativeUntil(status.until, now);
  const local = `до ${status.local_until} по её времени`;
  return relative ? `${local} · ${relative}` : local;
}

/** The full status in one line: title — detail. */
export function statusText(status: Pick<PersonaStatus, 'title' | 'detail'>): string {
  return status.detail ? `${status.title} — ${status.detail}` : status.title;
}
