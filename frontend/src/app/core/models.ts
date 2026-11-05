/** Типы строго по docs/api.md. */

export type Role = 'owner' | 'operator';
export interface User { id: number; username: string; role: Role }
export interface AuthState { user: User; csrf_token: string }

export interface ListResponse<T> { items: T[]; total?: number }

export type ChannelKind = 'telegram' | 'whatsapp' | 'instagram';
export type Stage =
  | 'first_contact' | 'engaged' | 'needs_identified' | 'interested'
  | 'handed_to_partner' | 'registered' | 'paid';
export type ClientStatus = 'active' | 'paused' | 'no_response' | 'refused';

export interface Persona {
  id: number;
  name: string;
  age: number | null;
  gender: string;
  avatar_url: string;
  tagline: string;
  character: string;
  backstory: string;
  communication_style: string;
  language: string;
  model: string;
  voice_id: string;
  strategy_id: number | null;
  is_active: boolean;
  disclosure_text: string;
  created_at: string;
}
export type PersonaIn = Omit<Persona, 'id' | 'created_at'>;

export interface PreviewTurn { role: 'user' | 'assistant'; text: string }
export interface PreviewResponse { reply: string; decision: unknown; model: string; latency_ms: number }

export interface Strategy {
  id: number;
  name: string;
  goal: string;
  offer: string;
  partner_name: string;
  registration_url: string;
  stage_playbook: Record<string, string>;
  rules: string[];
  followup_hours: number[];
  reply_delay_s: [number, number];
  created_at: string;
}
export type StrategyIn = Omit<Strategy, 'id' | 'created_at'>;

export interface BrokerModel { id: string; name: string; subscription: boolean; available: boolean }

export type ChannelStatus = 'new' | 'pending' | 'active' | 'challenge' | 'expired' | 'error';
export interface Channel {
  id: number;
  kind: ChannelKind;
  name: string;
  persona_id: number | null;
  status: ChannelStatus;
  account_label: string | null;
  last_error: string | null;
  last_activity_at: string | null;
}
export interface ChannelIn { kind: ChannelKind; persona_id: number; name: string }
export interface ConnectState {
  status: ChannelStatus;
  qr_data_uri?: string;
  need?: '2fa' | 'challenge';
  message?: string;
}

export type Direction = 'in' | 'out';
export type Author = 'client' | 'ai' | 'operator';
export type Delivery = 'queued' | 'sent' | 'failed';
export interface Message {
  id: number;
  direction: Direction;
  author: Author;
  text: string;
  media_kind?: string | null;
  media_url?: string | null;
  created_at: string;
  delivery: Delivery;
  model?: string | null;
}
export interface DialogRow {
  id: number;
  channel_kind: ChannelKind;
  persona: { id: number; name: string; avatar_url: string };
  client: { id: number; display_name: string; avatar_url: string; stage: Stage; status: ClientStatus };
  last_message: { text: string; direction: Direction; at: string } | null;
  unread: number;
  ai_paused: boolean;
}
export interface Dialog {
  id: number;
  channel_kind?: ChannelKind;
  persona_id?: number;
  client_id?: number;
  ai_paused: boolean;
}
export interface DialogDetail { dialog: Dialog; client: Client; messages: Message[] }
export interface DialogFilter {
  channel_kind: ChannelKind | '';
  persona_id: number | null;
  stage: Stage | '';
  status: ClientStatus | '';
  q: string;
  unread_only: boolean;
}

export interface Client {
  id: number;
  display_name: string;
  avatar_url: string;
  persona_id: number | null;
  stage: Stage;
  status: ClientStatus;
  next_step: string | null;
  next_step_at: string | null;
  notes: string | null;
  needs: string[];
  channels: { kind: ChannelKind; dialog_id: number }[];
  last_contact_at: string | null;
  created_at: string;
}
export type ClientMini = Pick<Client, 'id' | 'display_name' | 'avatar_url' | 'stage' | 'status'> &
  Partial<Pick<Client, 'last_contact_at' | 'needs' | 'channels' | 'next_step'>>;
export interface FunnelEvent {
  id?: number;
  type?: string;
  from_stage?: string | null;
  to_stage?: string | null;
  actor?: string;
  reason?: string | null;
  created_at: string;
}
export interface PartnerEvent {
  id?: number;
  event: string;
  amount?: number | null;
  currency?: string | null;
  payout?: number | null;
  occurred_at: string;
}
export interface ClientCard extends Client { events: FunnelEvent[]; partner_events: PartnerEvent[] }
export interface ClientPatch {
  notes?: string;
  next_step?: string;
  next_step_at?: string | null;
  display_name?: string;
}
export interface StageChange { stage?: Stage; status?: ClientStatus; reason?: string }
export interface ClientFilter { stage: Stage | ''; status: ClientStatus | ''; persona_id: number | null; q: string }

export interface FunnelResponse {
  stages: { stage: Stage; count: number; clients: ClientMini[] }[];
  statuses: Record<ClientStatus, number>;
}

export type ContentKind = 'photo' | 'video' | 'reel' | 'story';
export type ContentStatus = 'draft' | 'approved' | 'scheduled' | 'published' | 'failed';
export interface ContentPost {
  id: number;
  persona_id: number;
  channel_id: number | null;
  kind: ContentKind;
  caption: string;
  media_urls: string[];
  status: ContentStatus;
  scheduled_at: string | null;
  published_at: string | null;
  error: string | null;
}
export type ContentIn = Pick<ContentPost, 'persona_id' | 'channel_id' | 'kind' | 'caption' | 'media_urls' | 'scheduled_at'>;

export interface AnalyticsSummary {
  contacts: number;
  new_contacts: number;
  replied: number;
  by_stage: Record<string, number>;
  registrations: number;
  payments: number;
  revenue: number;
  partner_payouts: number;
  conversion: { contact_to_registration: number; registration_to_payment: number };
  llm: { calls: number; cost_usd: number; tokens_in: number; tokens_out: number };
  by_channel: { kind: ChannelKind; contacts: number; registrations: number; payments: number }[];
  daily: { date: string; contacts: number; registrations: number; payments: number }[];
}

export interface PartnerImportResult { imported: number; skipped: number; errors: string[] }
