import { formatTime } from '@/lib/format';
import type { DialogEvent, Message } from '@/lib/models';
import { STATUS_LABELS, stageMeta } from '@/lib/stages';

/** The type tag of a journal line, by the kind the server writes (sim.services.journal). */
export const EVENT_TAGS: Record<string, string> = {
  stage: 'Этап',
  status: 'Статус',
  ai_pause: 'ИИ',
  reply_delay: 'Время ответа',
  urgent: 'Срочность',
  handoff: 'Передача оператору',
  resolved: 'Обработано',
  chat_cleared: 'Чат очищен',
  passport_cleared: 'Паспорт очищен',
  reset: 'Сброс /reset',
  persona: 'Персона',
  strategy: 'Стратегия',
  model: 'Модель',
  passport: 'Паспорт',
  needs: 'Потребности',
  notes: 'Заметки',
  next_step: 'Следующий шаг',
};

export const ACTOR_LABELS: Record<string, string> = {
  owner: 'владелец',
  operator: 'оператор',
  system: 'система',
  model: 'модель',
};

export const NO_REASON = 'причина не указана';

const AI_VALUES: Record<string, string> = { on: 'включён', paused: 'на паузе' };

/** A stored value as the operator reads it: stage and status ids become their names. */
export function eventValue(kind: string, value: string): string {
  if (!value) return '';
  if (kind === 'stage') return stageMeta(value).label;
  if (kind === 'status') return STATUS_LABELS[value as keyof typeof STATUS_LABELS] ?? value;
  if (kind === 'ai_pause') return AI_VALUES[value] ?? value;
  if (kind === 'handoff' && value === 'operator') return 'диалог ждёт оператора';
  return value;
}

export interface EventVm {
  event: DialogEvent;
  tag: string;
  /** What changed: «было → стало», or the one value there is; '' when the tag says it all. */
  change: string;
  who: string;
  /** The given reason, or «причина не указана»: nothing is made up. */
  reason: string;
  hasReason: boolean;
  time: string;
}

export function toEventVm(event: DialogEvent): EventVm {
  const from = eventValue(event.kind, event.from);
  const to = eventValue(event.kind, event.to);
  const reason = event.reason.trim();
  return {
    event,
    tag: EVENT_TAGS[event.kind] ?? event.kind,
    change: from && to ? `${from} → ${to}` : to || from,
    who: [ACTOR_LABELS[event.actor] ?? event.actor, event.actor_name].filter(Boolean).join(' '),
    reason: reason || NO_REASON,
    hasReason: reason !== '',
    time: formatTime(event.created_at),
  };
}

export type FeedEntry = { type: 'message'; at: string; message: Message } | { type: 'event'; at: string; event: DialogEvent };

/**
 * Messages and journal lines as one feed in time order. The messages keep their own order (the
 * server sorts them); each journal line goes before the first message that is later than it.
 */
export function mergeFeed(messages: readonly Message[], events: readonly DialogEvent[] = []): FeedEntry[] {
  const pending = [...events].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at) || a.id - b.id);
  const out: FeedEntry[] = [];
  let next = 0;
  for (const message of messages) {
    const at = Date.parse(message.created_at);
    while (next < pending.length && Date.parse(pending[next].created_at) < at) {
      out.push({ type: 'event', at: pending[next].created_at, event: pending[next] });
      next += 1;
    }
    out.push({ type: 'message', at: message.created_at, message });
  }
  for (; next < pending.length; next += 1) out.push({ type: 'event', at: pending[next].created_at, event: pending[next] });
  return out;
}
