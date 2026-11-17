/** System trace log (GET /api/activity/log, GET /api/activity/traces/{id}). */
export type TraceKind = 'http' | 'webhook' | 'inbound' | 'poll' | 'job' | 'llm' | 'guard' | 'send' | 'partner' | 'watch' | 'call';
export type TraceStatus = 'ok' | 'error' | 'retry' | 'skipped' | 'warning';
export type TraceSortField =
  | 'started_at'
  | 'kind'
  | 'name'
  | 'status'
  | 'duration_ms'
  | 'attempt'
  | 'http_status'
  | 'actor'
  | 'client'
  | 'trace_id';

export interface TraceStep {
  id: number;
  trace_id: string;
  kind: TraceKind;
  name: string;
  status: TraceStatus;
  started_at: string;
  duration_ms: number | null;
  attempt: number | null;
  http_status: number | null;
  actor: string;
  error: string | null;
  detail: Record<string, unknown>;
  client: { id: number; display_name: string } | null;
  admin_id: number | null;
  dialog_id: number | null;
  channel_id: number | null;
  job_id: number | null;
  message_id: number | null;
  llm_call_id: number | null;
  llm?: TraceLlmUsage;
}

export interface TraceLogPage {
  items: TraceStep[];
  total: number;
  page: number;
  size: number;
  retention_days: number;
}

export interface TraceLlmUsage {
  workflow?: string;
  model: string | null;
  provider: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cached_tokens: number | null;
  cost_usd: number | null;
  finish_reason: string | null;
}

/** A stored payload as the server projects it: secrets already hidden, maybe cut by the storage limit. */
export interface TracePayload {
  content: unknown;
  truncated: boolean;
  redacted: boolean;
}

/** The body of an http/webhook request: the kept payload, or why it was not kept. */
export type TraceRequestBody = TracePayload | { omitted: string };

export interface TraceTimelineStep extends TraceStep {
  broker_exchange?: Record<string, TracePayload> | null;
  request_body?: TraceRequestBody | null;
  offset_ms: number;
  in_trace: boolean;
}

export interface TraceDetail {
  trace_id: string;
  started_at: string;
  finished_at: string;
  duration_ms: number;
  status: TraceStatus;
  cost_usd: number;
  steps: TraceTimelineStep[];
}
