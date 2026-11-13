/** Типы строго по docs/api.md. */

export type Role = 'superadmin' | 'owner' | 'operator';
export interface User {
  id: number;
  role: Role;
  telegram_id: number | null;
  tg_username: string | null;
  display_name: string;
  photo_url: string;
}
export interface AuthState { user: User; csrf_token: string }
export interface AdminUser extends User { is_active: boolean; created_at: string }
export interface AdminCreate { telegram_id?: number; tg_username?: string; role: Role; display_name?: string }
export interface AdminPatch { role?: Role; is_active?: boolean }

/** Поля, которые Telegram Login Widget отдаёт в onauth (все подписаны hash). */
export interface TelegramWidgetUser {
  id: number;
  first_name?: string;
  last_name?: string;
  username?: string;
  photo_url?: string;
  auth_date: number;
  hash: string;
}
export interface AuthConfig { bot_username: string }
export interface AccessDeniedBody { detail: string; telegram_id: number }

export interface ListResponse<T> { items: T[]; total?: number }

export type ChannelKind = 'telegram' | 'telegram_user' | 'whatsapp' | 'instagram';
export type Stage =
  | 'first_contact' | 'engaged' | 'needs_identified' | 'interested'
  | 'handed_to_partner' | 'registered' | 'paid';
export type ClientStatus = 'active' | 'paused' | 'no_response' | 'refused';

export interface Persona {
  id: number;
  name: string;
  age: number | null;
  /** День рождения (YYYY-MM-DD): возраст, знак зодиака и дни до праздника считает код. */
  birth_date: string | null;
  /** Часовой пояс IANA (Europe/Kyiv): местное время персонажа считает код. */
  timezone: string;
  /** Распорядок недели, строка на дело («пн-пт 09:00-13:00 консультации»): где она сейчас, была и будет, считает код. */
  routine: string;
  gender: string;
  avatar_url: string;
  /** Свободный текст: кто она, характер, биография, как пишет. */
  description: string;
  /** Что персонаж не должен писать и делать: одно правило на строку. */
  prohibitions: string;
  language: string;
  model: string;
  /** Голос Google Gemini TTS (имя, например «Kore»); пусто — без голоса. */
  voice_id: string;
  /** Когда отвечать голосовым: off — никогда, mirror — на голосовое клиента, always — всегда. */
  voice_mode: VoiceMode;
  /** Подсказка манеры речи для синтеза, например «тепло, спокойно». */
  voice_style: string;
  /** Обстановка голосовых: off — чистый звук, room/street/cafe — «запись на смартфон» там, random — случайно. */
  voice_ambience: VoiceAmbience;
  /** Отвечать на входящие звонки в Telegram живым голосом (тот же voice_id, Gemini Live через AIbroker). */
  accept_calls: boolean;
  strategy_id: number | null;
  is_active: boolean;
  created_at: string;
}
export type PersonaIn = Omit<Persona, 'id' | 'created_at'>;

export type MediaKind = 'photo' | 'video' | 'video_note';
/** `POST /api/personas/{id}/video-note`: `cached` = the same request was already generated. */
export interface VideoNoteQueued {
  status: 'queued';
  cached: boolean;
  cache_key: string;
  dialog_id: number;
}
export interface PersonaMedia {
  id: number;
  persona_id: number;
  kind: MediaKind;
  storage_name: string;
  caption: string;
  tags: string[];
  file_url: string;
  created_at: string;
}
export type VoiceMode = 'off' | 'mirror' | 'always';
export type VoiceAmbience = 'off' | 'room' | 'street' | 'cafe' | 'random';

export interface PreviewTurn { role: 'user' | 'assistant'; text: string }
export type GuardVerdict = 'ok' | 'rewritten' | 'blocked';
export interface GuardInfo {
  verdict: GuardVerdict;
  violations: string[];
  model: string | null;
  /** Черновик генератора; задан, если контролер исправил или заблокировал ответ. */
  original_text: string | null;
}
/** The persona's voice note in the preview chat; `error` when synthesis failed (the text stays). */
export interface PreviewVoice { media_url: string | null; duration_s: number | null; error: string | null }
export interface PreviewContext { stage: Stage; status: ClientStatus; needs: string[]; next_step: string }
export interface PreviewResponse {
  reply: string;
  decision: unknown;
  guard: GuardInfo | null;
  context?: PreviewContext;
  model: string;
  latency_ms: number;
  voice?: PreviewVoice | null;
}
export type PreviewAttachmentKind = 'voice' | 'audio' | 'image' | 'file';
/** A voice note or file of the preview chat, already turned into text by the Broker. */
export interface PreviewAttachment { kind: PreviewAttachmentKind; name: string; text: string; media_url: string }

export interface Strategy {
  id: number;
  name: string;
  goal: string;
  /** Свободный промпт стратегии: ориентиры разговора, не скрипт. */
  prompt: string;
  offer: string;
  partner_name: string;
  registration_url: string;
  stage_playbook: Record<string, string>;
  rules: string[];
  followup_hours: number[];
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
  /** The last connect saved its credentials (encrypted): a reconnect may leave the fields empty. */
  has_saved_credentials?: boolean;
}
export interface ChannelIn { kind: ChannelKind; persona_id: number; name: string }
export interface ConnectState {
  status: ChannelStatus;
  qr_data_uri?: string;
  need?: 'code' | '2fa' | 'challenge';
  message?: string;
}

export type Direction = 'in' | 'out';
export type Author = 'client' | 'ai' | 'operator';
export type Delivery = 'queued' | 'sent' | 'failed' | 'cancelled';
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
  provider?: string | null;
  operator_id?: number | null;
  operator_name?: string | null;
  tokens_in?: number | null;
  tokens_out?: number | null;
  cost_usd?: number | null;
  error?: string | null;
  /** Результат контролера; только у сообщений ИИ. */
  guard?: GuardInfo | null;
}
export interface DialogRow {
  id: number;
  channel_id?: number | null;
  channel_kind: ChannelKind;
  persona: { id: number; name: string; avatar_url: string };
  client: { id: number; display_name: string; avatar_url: string; stage: Stage; status: ClientStatus };
  last_message: { text: string; direction: Direction; at: string } | null;
  unread: number;
  ai_paused: boolean;
  /** ИИ не справился или передал диалог человеку — нужен оператор. */
  needs_operator: boolean;
  is_test?: boolean;
}
export interface Dialog {
  id: number;
  channel_kind?: ChannelKind;
  persona_id?: number;
  client_id?: number;
  ai_paused: boolean;
  needs_operator: boolean;
  channel_status?: ChannelStatus;
  send_capabilities?: { text: boolean; voice: boolean; attachment: boolean };
}
export interface DialogDetail { dialog: Dialog; client: Client; messages: Message[] }
/** DELETE /api/dialogs/{id}/messages: the dialog after «Очистить чат» and how many messages went. */
export interface ClearedDialog extends DialogDetail { deleted_messages: number }
export interface DialogFilter {
  source: '' | 'real' | 'test';
  avatar: '' | 'with' | 'without';
  channel_id: number | null;
  channel_kind: ChannelKind | '';
  persona_id: number | null;
  stage: Stage | '';
  status: ClientStatus | '';
  q: string;
  unread_only: boolean;
  needs_operator: boolean;
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
  /** What the persona knows about the lead; the passport keeper agent updates it after every reply. */
  passport?: string;
  passport_updated_at?: string | null;
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

export interface CurrencyTotal { currency: string | null; total: number }

export interface AnalyticsSummary {
  contacts: number;
  new_contacts: number;
  replied: number;
  by_stage: Record<string, number>;
  registrations: number;
  payments: number;
  revenue: number | null;
  partner_payouts: number | null;
  revenue_by_currency: CurrencyTotal[];
  partner_payouts_by_currency: CurrencyTotal[];
  conversion: { contact_to_registration: number; registration_to_payment: number };
  llm: { calls: number; cost_usd: number; tokens_in: number; tokens_out: number };
  guard: { ok: number; rewritten: number; blocked: number };
  by_channel: { kind: ChannelKind; contacts: number; registrations: number; payments: number }[];
  daily: { date: string; contacts: number; registrations: number; payments: number }[];
}

export interface SpendSums { calls: number; cost_usd: number; tokens_in: number; tokens_out: number }
/** Money the whole project spent, as Broker books it (`GET /api/analytics/spend`). */
export type AnalyticsSpend =
  | {
      available: true;
      from: string;
      to: string;
      currency: string;
      total: SpendSums;
      by_capability: (SpendSums & { capability: string })[];
      by_day: (SpendSums & { day: string })[];
      limits: { daily_cap_usd: number | null; monthly_cap_usd: number | null; today_usd: number; month_usd: number };
    }
  | { available: false; error: string };

export interface PartnerImportResult { imported: number; skipped: number; errors: string[] }

export interface SimBranchRow {
  id: number;
  admin: { id: number; display_name: string };
  persona: { id: number; name: string };
  title: string;
  message_count: number;
  stage: Stage;
  status: ClientStatus;
  created_at: string;
  last_active_at: string;
  closed_at: string | null;
}
export interface SimMessage {
  id: number;
  role: 'client' | 'ai' | 'system';
  text: string;
  model: string | null;
  created_at: string;
  guard: GuardInfo | null;
}
export interface SimBranchDetail extends SimBranchRow {
  pending?: boolean;
  can_continue?: boolean;
  needs: string[];
  next_step: string;
  notes: string;
  messages: SimMessage[];
}

/** Owner setting stored in the DB (/api/settings): shared prompt texts and tunables. */
/** Fixed guard contract; persona is empty for backward compatibility. */
export interface FixedPrompts { persona: string; guard: string }
/** One piece of the persona's system prompt and whose field it comes from (`field`: that field or the setting key). */
export interface PromptPart { key: string; title: string; text: string; source: 'persona' | 'strategy' | 'setting'; field: string }
/** A rule fixed in the code: shown read-only. */
export interface FixedRule { key: string; title: string; text: string }
/**
 * The persona's prompt map: `system` is the stable system message as the model gets it, `parts` the same
 * text piece by piece, `context` an example of the per-reply message code builds, `rules` what is fixed in
 * the code. `fixed` is empty for compatibility.
 */
export interface PersonaPrompt {
  system: string;
  fixed: string;
  strategy_id: number;
  context: string;
  parts: PromptPart[];
  rules: FixedRule[];
}
export interface AppSettingItem {
  key: string;
  group: string;
  label: string;
  help: string;
  kind: 'text' | 'int' | 'capability' | 'model';
  min: number | null;
  max: number | null;
  value: string | number;
  default: string | number;
  is_default: boolean;
  updated_at: string | null;
  updated_by_id: number | null;
}
