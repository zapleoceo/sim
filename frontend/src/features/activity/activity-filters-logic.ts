import type { LlmActivityFilter, MessageActivityFilter } from '@/lib/activity-models';
import type { Author, ChannelKind, Delivery, Direction } from '@/lib/models';

export type ActivityFilters = MessageActivityFilter & LlmActivityFilter;

/** Form state of the activity filters (inputs keep raw strings until validated). */
export interface ActivityFormValues {
  q: string;
  channel_kind: ChannelKind | '';
  channel_id: number | null;
  persona_id: number | null;
  client_id: string;
  direction: Direction | '';
  author: Author | '';
  delivery: Delivery | '';
  from: string;
  to: string;
  workflow: string;
  model: string;
  provider: string;
  has_error: boolean | null;
}

export const EMPTY_ACTIVITY_FORM: ActivityFormValues = {
  q: '',
  channel_kind: '',
  channel_id: null,
  persona_id: null,
  client_id: '',
  direction: '',
  author: '',
  delivery: '',
  from: '',
  to: '',
  workflow: '',
  model: '',
  provider: '',
  has_error: null,
};

export const LIMITS = { q: 200, workflow: 32, model: 120, provider: 64 } as const;

export type ActivityFormErrors = Partial<Record<'q' | 'client_id' | 'workflow' | 'model' | 'provider' | 'dateOrder', string>>;

export function validateActivityForm(v: ActivityFormValues): ActivityFormErrors {
  const errors: ActivityFormErrors = {};
  for (const key of ['q', 'workflow', 'model', 'provider'] as const) {
    if (v[key].length > LIMITS[key]) errors[key] = `Не больше ${LIMITS[key]} символов`;
  }
  const client = v.client_id.trim();
  if (client && (!/^\d+$/.test(client) || Number(client) < 1)) errors.client_id = 'Целое число от 1';
  if (v.from && v.to && v.from > v.to) errors.dateOrder = 'Дата «По» должна быть не раньше даты «С».';
  return errors;
}

/** Number of filled filters (badge). */
export function activityFilterCount(v: ActivityFormValues): number {
  return Object.values(v).filter((x) => x !== '' && x !== null && x !== undefined).length;
}

/**
 * Form -> API filter, or null when the form is invalid (nothing is loaded then).
 * Message and LLM feeds get only their own fields; client-scoped feeds ignore client_id.
 */
export function toActivityFilter(
  v: ActivityFormValues,
  { llm, clientScoped }: { llm: boolean; clientScoped: boolean },
): ActivityFilters | null {
  if (Object.keys(validateActivityForm(v)).length) return null;
  const client = v.client_id.trim();
  const common: ActivityFilters = {
    q: v.q.trim(),
    channel_kind: v.channel_kind,
    channel_id: v.channel_id,
    persona_id: v.persona_id,
    client_id: clientScoped || !client ? null : Number(client),
    from: v.from || null,
    to: v.to || null,
  };
  return llm
    ? { ...common, workflow: v.workflow.trim(), model: v.model.trim(), provider: v.provider.trim(), has_error: v.has_error }
    : { ...common, direction: v.direction, author: v.author, delivery: v.delivery };
}
