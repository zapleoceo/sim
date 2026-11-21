/** Shapes of the dashboard API (docs/api.md). Times are ISO 8601 UTC strings. */

export type EventKind =
  | 'message_in'
  | 'message_out'
  | 'message_edited'
  | 'message_deleted'
  | 'media_understood'
  | 'send_failed'
  | 'call'
  | 'generation_started'
  | 'generation_failed'
  | 'guard_blocked'
  | 'operator_needed'
  | 'ai_toggled'
  | 'operator_note'
  | 'client_updated'
  | 'memory_updated';

export type Direction = 'in' | 'out';
export type Author = 'client' | 'persona' | 'operator';
export type MessageKind = 'text' | 'photo' | 'video' | 'voice' | 'video_note' | 'file' | 'sticker' | 'call';
export type MessageStatus = 'pending' | 'sent' | 'failed' | 'received';
export type MediaKind = 'photo' | 'video' | 'audio' | 'file';
export type MediaSource = 'library' | 'generated' | 'incoming';

// --- auth ---------------------------------------------------------------------------------------

export interface Admin {
  id: number;
  name: string;
  username: string | null;
}

export interface AuthState {
  admin: Admin;
  csrf: string;
}

export interface AuthConfig {
  bot_username: string;
}

/** Fields the Telegram Login Widget passes to `onauth`. */
export interface TelegramWidgetUser {
  id: number;
  first_name?: string;
  last_name?: string;
  username?: string;
  photo_url?: string;
  auth_date: number;
  hash: string;
}

// --- media --------------------------------------------------------------------------------------

export interface Media {
  id: number;
  kind: MediaKind;
  mime: string;
  url: string;
  caption: string;
  tags: string[];
  description: string;
  source: MediaSource;
  is_reference: boolean;
  width: number | null;
  height: number | null;
  duration_s: number | null;
  file_name: string;
}

// --- clients ------------------------------------------------------------------------------------

export interface MemoryFact {
  fact: string;
  event_id: number | null;
  at: string | null;
}

export interface Client {
  id: number;
  name: string;
  username: string | null;
  phone: string | null;
  timezone: string | null;
  notes: string;
  ai_enabled: boolean;
  memory: MemoryFact[];
  created_at: string;
  last_seen_at: string | null;
  /** Number of chats; optional until the list endpoint returns it. */
  chats_count?: number;
}

export type ClientPatch = Partial<Pick<Client, 'name' | 'phone' | 'notes' | 'timezone' | 'ai_enabled' | 'memory'>>;

// --- chats --------------------------------------------------------------------------------------

export interface ChatRowClient {
  id: number;
  name: string;
  username: string | null;
  ai_enabled: boolean;
}

export interface LastMessage {
  kind: MessageKind;
  direction: Direction;
  author: Author;
  text: string | null;
}

export interface ChatRow {
  id: number;
  client: ChatRowClient;
  ai_enabled: boolean;
  needs_operator: boolean;
  unread_count: number;
  last_message_at: string | null;
  last_message: LastMessage | null;
}

export type ChatDetail = Omit<ChatRow, 'client'> & { client: Client };

export interface ClientDetail extends Client {
  chats: ChatRow[];
}

export interface TimelineMessage {
  id: number;
  direction: Direction;
  author: Author;
  kind: MessageKind;
  text: string | null;
  status: MessageStatus;
  error: string | null;
  sent_at: string | null;
  media?: Media | null;
}

export interface TranscriptLine {
  who: string;
  text: string;
  t: number | null;
}

export interface TimelineCall {
  id: number;
  direction: Direction;
  status: string;
  duration_s: number | null;
  transcript: TranscriptLine[];
}

export interface TimelineGeneration {
  id: number;
  kind: 'photo' | 'video' | 'voice';
  status: 'queued' | 'running' | 'done' | 'failed';
  caption: string;
  error: string | null;
}

export interface TimelineItem {
  id: number;
  kind: EventKind;
  actor: string;
  created_at: string;
  in_context: boolean;
  reply_run_id: number | null;
  message?: TimelineMessage | null;
  call?: TimelineCall | null;
  generation?: TimelineGeneration | null;
  data: Record<string, unknown>;
}

export interface LlmCall {
  id: number;
  purpose: string;
  model: string;
  request: unknown;
  response: unknown;
  tokens_in: number | null;
  tokens_out: number | null;
  cost_usd: number | null;
  latency_ms: number | null;
  error: string | null;
  created_at: string;
}

export interface ReplyRun {
  id: number;
  status: string;
  context: Record<string, unknown> | null;
  plan: unknown;
  guard_verdicts: unknown[];
  error: string | null;
  llm_calls: LlmCall[];
}

export interface Items<T> {
  items: T[];
}

// --- persona ------------------------------------------------------------------------------------

export interface Persona {
  id: number;
  name: string;
  gender: string;
  language: string;
  timezone: string;
  birth_date: string | null;
  avatar_media_id: number | null;
  prompt: string;
  model_reply: string;
  model_guard: string;
  model_vision: string;
  model_transcribe: string;
  model_memory: string;
  model_tts: string;
  model_image: string;
  model_video: string;
  model_live: string;
  voice_id: string;
  voice_style: string;
  guard_rules: string;
  ai_enabled: boolean;
  voice_enabled: boolean;
  photo_enabled: boolean;
  video_enabled: boolean;
  calls_enabled: boolean;
  reply_delay_s: number;
}

export type PersonaPatch = Partial<Omit<Persona, 'id'>>;

export interface BrokerModel {
  id: string;
  /** null = Broker does not know yet. */
  available: boolean | null;
  input_modalities: string[];
  output_modalities: string[];
  capabilities: string[];
}

export interface Voice {
  id: string;
  label: string;
}

export interface Channel {
  id: number;
  kind: string;
  title: string;
  status: 'new' | 'connected' | 'error' | 'disabled' | string;
  account_label: string;
  last_error: string | null;
  last_activity_at: string | null;
}
