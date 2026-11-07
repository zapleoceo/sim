import { Author, ChannelKind, Delivery, Direction, Message } from './models';

export interface ActivityClient { id: number; display_name: string; avatar_url: string }
export interface ActivityPersona { id: number; name: string }
export interface ActivityChannel { id: number; name: string; kind: ChannelKind; account_label: string | null }
export interface ActivityPage<T> { items: T[]; total: number; limit: number; offset: number }
export interface MessageActivity extends Message {
  dialog_id: number;
  client: ActivityClient;
  persona: ActivityPersona;
  channel: ActivityChannel;
  sent_at: string | null;
}
export interface LlmActivity {
  id: number; created_at: string; dialog_id: number | null;
  client: ActivityClient | null; persona: ActivityPersona | null; channel: ActivityChannel | null;
  workflow: string; capability: string; model: string | null; provider: string | null;
  tokens_in: number | null; tokens_out: number | null; cached_tokens: number | null;
  cost_usd: number | null; latency_ms: number; finish_reason: string | null; has_error: boolean;
}
export interface ActivityFilter {
  q?: string; client_id?: number | null; persona_id?: number | null; channel_id?: number | null;
  channel_kind?: ChannelKind | ''; from?: string | null; to?: string | null;
}
export interface MessageActivityFilter extends ActivityFilter {
  direction?: Direction | ''; author?: Author | ''; delivery?: Delivery | '';
}
export interface LlmActivityFilter extends ActivityFilter {
  workflow?: string; model?: string; provider?: string; has_error?: boolean | null;
}
