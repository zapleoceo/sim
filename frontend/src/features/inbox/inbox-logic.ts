import { dayKey, dayLabel, formatShort, formatTime } from '@/lib/format';
import type { Delivery, DialogDetail, DialogFilter, DialogRow, GuardInfo, Message } from '@/lib/models';

export const POLL_MS = 5000;
export const PAGE_SIZE = 50;
/** How close (px) to the end of the list the next page starts loading. */
export const LOAD_MORE_THRESHOLD = 240;

export const DEFAULT_DIALOG_FILTER: DialogFilter = {
  channel_kind: '',
  persona_id: null,
  stage: '',
  status: '',
  q: '',
  unread_only: false,
  needs_operator: false,
};

/** How many filters differ from the defaults (badge on «Фильтры»). */
export function activeFilterCount(filter: DialogFilter): number {
  return (Object.keys(DEFAULT_DIALOG_FILTER) as (keyof DialogFilter)[]).filter((k) => {
    const v = filter[k];
    return typeof v === 'string' ? v.trim() !== '' : v !== DEFAULT_DIALOG_FILTER[k];
  }).length;
}

/** Value sent to the API (q trimmed). */
export function normalizeFilter(filter: DialogFilter): DialogFilter {
  return { ...filter, q: filter.q.trim() };
}

/**
 * Background poll refreshes only the first page; the already loaded tail stays (minus rows that moved up).
 * On a filter change (`reset`) the tail is dropped.
 */
export function mergeFirstPage(current: readonly DialogRow[], head: readonly DialogRow[], reset: boolean, pageSize = PAGE_SIZE): DialogRow[] {
  if (reset) return [...head];
  const ids = new Set(head.map((r) => r.id));
  return [...head, ...current.slice(pageSize).filter((r) => !ids.has(r.id))];
}

/** Appends the next page, skipping rows already shown. */
export function appendPage(current: readonly DialogRow[], next: readonly DialogRow[]): DialogRow[] {
  const ids = new Set(current.map((r) => r.id));
  return [...current, ...next.filter((r) => !ids.has(r.id))];
}

export function rowPreview(row: DialogRow): string {
  if (!row.last_message) return 'Нет сообщений';
  return (row.last_message.direction === 'out' ? 'Вы: ' : '') + row.last_message.text;
}

export function rowTime(row: DialogRow, now = new Date()): string {
  return formatShort(row.last_message?.at, now);
}

export interface MessageVm {
  msg: Message;
  time: string;
  side: 'left' | 'right';
  authorLabel: string;
}

export interface DayGroup { key: string; label: string; items: MessageVm[] }

export function toMessageVm(msg: Message): MessageVm {
  return {
    msg,
    time: formatTime(msg.created_at),
    side: msg.author === 'client' ? 'left' : 'right',
    authorLabel: msg.author === 'ai' ? 'ИИ' : msg.author === 'operator' ? 'Оператор' : '',
  };
}

/** Consecutive messages grouped by local day («Сегодня», «Вчера», date). */
export function groupByDay(messages: readonly Message[], now = new Date()): DayGroup[] {
  const out: DayGroup[] = [];
  for (const msg of messages) {
    const key = dayKey(msg.created_at);
    let g = out[out.length - 1];
    if (g?.key !== key) {
      g = { key, label: dayLabel(msg.created_at, now), items: [] };
      out.push(g);
    }
    g.items.push(toMessageVm(msg));
  }
  return out;
}

export type DeliveryIcon = 'clock' | 'check' | 'alert' | 'ban';

/** Icon and tooltip for an outgoing message's delivery state. */
export function deliveryMeta(delivery: Delivery, guard?: GuardInfo | null): { icon: DeliveryIcon; label: string } {
  switch (delivery) {
    case 'queued':
      return { icon: 'clock', label: 'В очереди на отправку' };
    case 'sent':
      return { icon: 'check', label: 'Отправлено' };
    case 'failed':
      return { icon: 'alert', label: 'Ошибка отправки — требуется сверка с каналом' };
    case 'cancelled':
      return {
        icon: 'ban',
        label: guard?.verdict === 'blocked' ? 'Не отправлено: заблокировано контролером' : 'Отменено: диалог перешёл к человеку',
      };
  }
}

/** True when a polled detail differs from what is shown (avoids re-render / scroll jumps). */
export function detailChanged(prev: DialogDetail | null | undefined, next: DialogDetail): boolean {
  if (!prev) return true;
  const a = prev.messages.at(-1);
  const b = next.messages.at(-1);
  return !(
    prev.dialog.id === next.dialog.id &&
    prev.messages.length === next.messages.length &&
    a?.id === b?.id &&
    a?.delivery === b?.delivery &&
    prev.dialog.ai_paused === next.dialog.ai_paused &&
    prev.dialog.needs_operator === next.dialog.needs_operator
  );
}

/** Enter sends, Shift+Enter (or IME composition) inserts a newline. */
export function isSendKey(e: { key: string; shiftKey: boolean; isComposing?: boolean }): boolean {
  return e.key === 'Enter' && !e.shiftKey && !e.isComposing;
}
