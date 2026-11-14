import type { QueryValue } from '@/lib/api/http';
import { localInputToIso } from '@/lib/format';
import type { TraceKind, TraceSortField, TraceStatus } from '@/lib/trace-models';

export const KIND_LABELS: Record<TraceKind, string> = {
  http: 'Запрос API',
  webhook: 'Вебхук',
  inbound: 'Входящее',
  poll: 'Опрос канала',
  job: 'Задача',
  llm: 'Модель',
  guard: 'Контролёр',
  send: 'Отправка',
  partner: 'Партнёр',
  watch: 'Контроль ответов',
};
export const KINDS = Object.keys(KIND_LABELS) as TraceKind[];

export const STATUS_LABELS: Record<TraceStatus, string> = {
  ok: 'Успешно',
  error: 'Ошибка',
  retry: 'Повтор',
  skipped: 'Пропущено',
  warning: 'Предупреждение',
};
export const STATUSES = Object.keys(STATUS_LABELS) as TraceStatus[];

/** Column filters; text inputs keep raw strings until serialized. */
export interface LogFilters {
  q: string;
  kinds: TraceKind[];
  statuses: TraceStatus[];
  name: string;
  actor: string;
  client: string;
  trace_id: string;
  from: string; // datetime-local, local time
  to: string;
  min_ms: string;
  max_ms: string;
  min_attempt: string;
  http_status: string;
  channel_id: number | null;
  errors_only: boolean;
}

export const EMPTY_LOG_FILTERS: LogFilters = {
  q: '',
  kinds: [],
  statuses: [],
  name: '',
  actor: '',
  client: '',
  trace_id: '',
  from: '',
  to: '',
  min_ms: '',
  max_ms: '',
  min_attempt: '',
  http_status: '',
  channel_id: null,
  errors_only: false,
};

export interface LogSort {
  field: TraceSortField;
  order: 'asc' | 'desc';
}
export const DEFAULT_SORT: LogSort = { field: 'started_at', order: 'desc' };

/** Click on a column header: a new column starts descending, the same one flips. */
export function nextSort(current: LogSort, field: TraceSortField): LogSort {
  if (current.field !== field) return { field, order: 'desc' };
  return { field, order: current.order === 'desc' ? 'asc' : 'desc' };
}

function wholeNumber(raw: string): number | null | 'invalid' {
  const v = raw.trim();
  if (!v) return null;
  return /^\d+$/.test(v) ? Number(v) : 'invalid';
}

export type LogFilterErrors = Partial<
  Record<'min_ms' | 'max_ms' | 'min_attempt' | 'http_status' | 'range' | 'dates' | 'q', string>
>;

export function validateLogFilters(f: LogFilters): LogFilterErrors {
  const errors: LogFilterErrors = {};
  for (const key of ['min_ms', 'max_ms', 'min_attempt', 'http_status'] as const) {
    if (wholeNumber(f[key]) === 'invalid') errors[key] = 'Целое число';
  }
  if (wholeNumber(f.min_attempt) === 0) errors.min_attempt = 'От 1';
  const status = wholeNumber(f.http_status);
  if (typeof status === 'number' && (status < 100 || status > 599)) errors.http_status = 'От 100 до 599';
  const lo = wholeNumber(f.min_ms);
  const hi = wholeNumber(f.max_ms);
  if (typeof lo === 'number' && typeof hi === 'number' && lo > hi) errors.range = 'Минимум больше максимума';
  if (f.from && f.to && f.from >= f.to) errors.dates = '«С» должно быть раньше «По»';
  if (f.q.length > 200) errors.q = 'Не больше 200 символов';
  return errors;
}

/** Filters + sort + page -> query params, or null while a filter is invalid (nothing is loaded then). */
export function toLogParams(
  f: LogFilters,
  sort: LogSort,
  page: { page: number; size: number },
): Record<string, QueryValue> | null {
  if (Object.keys(validateLogFilters(f)).length) return null;
  const num = (raw: string): number | null => {
    const v = wholeNumber(raw);
    return typeof v === 'number' ? v : null;
  };
  return {
    q: f.q.trim(),
    kind: f.kinds.join(','),
    status: f.statuses.join(','),
    name: f.name.trim(),
    actor: f.actor.trim(),
    client: f.client.trim(),
    trace_id: f.trace_id.trim().toLowerCase(),
    from: localInputToIso(f.from),
    to: localInputToIso(f.to),
    min_ms: num(f.min_ms),
    max_ms: num(f.max_ms),
    min_attempt: num(f.min_attempt),
    http_status: num(f.http_status),
    channel_id: f.channel_id,
    has_error: f.errors_only ? 'true' : null,
    sort: sort.field,
    order: sort.order,
    page: page.page,
    size: page.size,
  };
}

export type FilterSlot = TraceSortField | 'q' | 'errors_only' | 'channel';

/** Which filters are set, per column (header marker) and in total (badge). */
export function activeFilters(f: LogFilters): Partial<Record<FilterSlot, boolean>> {
  return {
    q: !!f.q.trim(),
    started_at: !!(f.from || f.to),
    kind: f.kinds.length > 0,
    status: f.statuses.length > 0,
    name: !!f.name.trim(),
    actor: !!f.actor.trim(),
    client: !!f.client.trim(),
    trace_id: !!f.trace_id.trim(),
    duration_ms: !!(f.min_ms.trim() || f.max_ms.trim()),
    attempt: !!f.min_attempt.trim(),
    http_status: !!f.http_status.trim(),
    errors_only: f.errors_only,
    channel: f.channel_id !== null,
  };
}

export function activeFilterCount(f: LogFilters): number {
  return Object.values(activeFilters(f)).filter(Boolean).length;
}

export function toggle<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

/** 850 мс · 1,2 с · 15 с · 2 мин 5 с; null -> «—». */
export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—';
  if (ms < 1000) return `${ms} мс`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0).replace('.', ',')} с`;
  const min = Math.floor(ms / 60_000);
  const sec = Math.round((ms % 60_000) / 1000);
  return sec ? `${min} мин ${sec} с` : `${min} мин`;
}

/** Bar of a step on the trace timeline, in percent of the whole trace (a minimum width keeps 0 ms visible). */
export function timelineBar(offsetMs: number, durationMs: number | null, totalMs: number): { left: number; width: number } {
  const total = Math.max(totalMs, 1);
  const left = Math.min(99.2, Math.max(0, (offsetMs / total) * 100));
  const width = Math.max(0.8, Math.min(100 - left, ((durationMs ?? 0) / total) * 100));
  return { left, width };
}

export function shortTrace(id: string): string {
  return id.slice(0, 8);
}

const DETAIL_LABELS: Record<string, string> = {
  queue_ms: 'Ожидание в очереди',
  outcome: 'Итог',
  guard: 'Вердикт контролёра',
  max_attempts: 'Максимум попыток',
  gave_up: 'Передано оператору',
  fenced: 'Перехвачено новой попыткой',
  query: 'Параметры запроса',
  messages: 'Сообщений',
  duplicate: 'Дубликат',
  reply_queued: 'Ответ поставлен в очередь',
  own: 'Своё сообщение',
  edit: 'Правка',
  media: 'Вложение',
  chars: 'Символов',
  source: 'Источник',
  amount: 'Сумма',
  currency: 'Валюта',
  model: 'Модель',
  provider: 'Провайдер',
  tokens_in: 'Токены на вход',
  tokens_out: 'Токены на выход',
  cost_usd: 'Стоимость, $',
  finish_reason: 'Завершение',
  message_id: 'Сообщение №',
  reason: 'Причина',
  unanswered: 'Без ответа, сообщений',
  age_min: 'Возраст сообщения, мин',
  window_min: 'Окно ответа, мин',
  waiting_s: 'Ждёт ответа, с',
  threshold_s: 'Порог, с',
};

/** Small, safe detail values for the trace panel, with Russian labels (objects and empty values are skipped). */
export function detailPairs(detail: Record<string, unknown>): [string, string][] {
  return Object.entries(detail)
    .filter(([, v]) => v !== null && v !== undefined && v !== '' && typeof v !== 'object')
    .map(([k, v]) => {
      const value = typeof v === 'boolean' ? (v ? 'да' : 'нет') : k.endsWith('_ms') ? formatMs(Number(v)) : String(v);
      return [DETAIL_LABELS[k] ?? k, value];
    });
}
