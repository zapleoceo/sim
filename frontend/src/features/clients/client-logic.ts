import { formatDateTime } from '@/lib/format';
import type { ClientCard, ClientFilter, FunnelEvent, PartnerEvent } from '@/lib/models';
import { stageMeta } from '@/lib/stages';

export type TimelineIcon = 'stage' | 'payment' | 'registration';

export interface TimelineItem { at: string; icon: TimelineIcon; title: string; detail: string; partner: boolean }

/** Funnel and partner events merged, newest first. */
export function buildTimeline(events: FunnelEvent[], partner: PartnerEvent[]): TimelineItem[] {
  const funnel = events.map<TimelineItem>((e) => ({
    at: e.created_at,
    icon: 'stage',
    title: e.to_stage
      ? `Этап: ${e.from_stage ? stageMeta(e.from_stage).label + ' → ' : ''}${stageMeta(e.to_stage).label}`
      : (e.type ?? 'Событие воронки'),
    detail: [e.actor, e.reason].filter(Boolean).join(' · '),
    partner: false,
  }));
  const fromPartner = partner.map<TimelineItem>((e) => ({
    at: e.occurred_at,
    icon: e.event === 'payment' ? 'payment' : 'registration',
    title: e.event === 'payment' ? 'Оплата у партнёра' : e.event === 'registration' ? 'Регистрация у партнёра' : e.event,
    detail: [e.amount != null ? `${e.amount} ${e.currency ?? ''}`.trim() : '', e.payout != null ? `выплата ${e.payout}` : '']
      .filter(Boolean)
      .join(' · '),
    partner: true,
  }));
  return [...funnel, ...fromPartner].sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
}

export function timelineWithDates(card: ClientCard): (TimelineItem & { when: string })[] {
  return buildTimeline(card.events, card.partner_events).map((t) => ({ ...t, when: formatDateTime(t.at) }));
}

export const DEFAULT_CLIENT_FILTER: ClientFilter = { q: '', stage: '', status: '', persona_id: null };

export function clientFilterCount(f: ClientFilter): number {
  return [f.q.trim() !== '', f.stage !== '', f.status !== '', f.persona_id !== null].filter(Boolean).length;
}

/** Value sent to the API (q trimmed). */
export function clientApiFilter(f: ClientFilter): ClientFilter {
  return { ...f, q: f.q.trim() };
}

export interface ClientFormValues { display_name: string; next_step: string; next_step_at: string; notes: string }
