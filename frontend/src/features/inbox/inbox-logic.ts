import { dayKey, dayLabel, formatShort, formatTime } from '@/lib/format';
import type { Delivery, DialogDetail, DialogFilter, DialogRow, GuardInfo, Message, Persona, SimBranchRow } from '@/lib/models';

/** Fallback only: changes arrive live through /api/events (lib/live.ts). */
export const POLL_MS = 30_000;
export const PAGE_SIZE = 50;
/** How close (px) to the end of the list the next page starts loading. */
export const LOAD_MORE_THRESHOLD = 240;

export const DEFAULT_DIALOG_FILTER: DialogFilter = {
  source: '',
  avatar: '',
  channel_id: null,
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

/** Test branches use negative IDs in this view; they never point at a real channel. */
export function testDialogRow(branch: SimBranchRow, persona?: Persona): DialogRow {
  return {
    id: -branch.id,
    is_test: true,
    channel_id: null,
    channel_kind: 'telegram',
    persona: { id: branch.persona.id, name: branch.persona.name, avatar_url: persona?.avatar_url ?? '' },
    client: { id: -branch.id, display_name: branch.title || 'Тестовый диалог',
      avatar_url: persona?.avatar_url ?? '', stage: branch.stage, status: branch.status },
    last_message: { text: `Тестовый диалог · ${branch.message_count} сообщений`,
      direction: 'in', at: branch.last_active_at },
    unread: 0,
    ai_paused: true,
    needs_operator: false,
  };
}

export function visibleTestRows(
  branches: readonly SimBranchRow[], personas: readonly Persona[], filter: DialogFilter,
): DialogRow[] {
  if (filter.source === 'real' || filter.channel_kind || filter.channel_id || filter.unread_only || filter.needs_operator) return [];
  const byId = new Map(personas.map((p) => [p.id, p]));
  const q = filter.q.trim().toLowerCase();
  return branches.map((b) => testDialogRow(b, byId.get(b.persona.id))).filter((row) =>
    (!filter.persona_id || row.persona.id === filter.persona_id) &&
    (!filter.stage || row.client.stage === filter.stage) &&
    (!filter.status || row.client.status === filter.status) &&
    (!q || row.client.display_name.toLowerCase().includes(q) || row.persona.name.toLowerCase().includes(q)) &&
    (!filter.avatar || (filter.avatar === 'with') === Boolean(row.client.avatar_url))
  );
}

export function combineDialogRows(real: readonly DialogRow[], test: readonly DialogRow[]): DialogRow[] {
  return [...real, ...test].sort((a, b) =>
    (b.last_message?.at ?? '').localeCompare(a.last_message?.at ?? '') || b.id - a.id
  );
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
