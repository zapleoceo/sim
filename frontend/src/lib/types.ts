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
  | 'memory_updated'
  | 'attention'
  | 'initiative';

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
  /** When the file was added (UTC ISO). */
  created_at: string;
  /** Media library only: the scene its generation was made in; null for an uploaded file. */
  scene?: GenerationScene | null;
}

/** `generation.scene` (sim/domain/scene.py): where, in what, how the picture was made. Older
 * generations keep fewer keys. */
export interface GenerationScene {
  day?: string;
  place?: string;
  activity?: string;
  outfit?: string;
  light?: string;
  shot?: string;
  camera_reason?: string;
  /** The room of her apartment (sim/services/life/home.py). */
  room?: string;
  /** The owner pinned this photo as the reference of its room. */
  room_ref?: boolean;
}

// --- leads (clients) and the passport (docs/leads.md) ------------------------------------------

export type LeadStage = 'new' | 'acquainted' | 'interested' | 'warm' | 'hot' | 'client' | 'lost';

/** Structured passport fields: the operator may edit (and so lock) each of them. */
export type PassportField =
  | 'name' | 'age' | 'gender' | 'city' | 'country' | 'timezone' | 'occupation' | 'stage' | 'temperature' | 'next_step';

export interface Passport {
  /** The living profile in Russian by sections. */
  text: string;
  name: string;
  age: number | null;
  gender: string;
  city: string;
  country: string;
  timezone: string;
  occupation: string;
  stage: LeadStage;
  temperature: number;
  next_step: string;
  updated_at: string | null;
  locked_fields: PassportField[];
}

export type PassportPatch = Partial<Omit<Passport, 'updated_at'>>;

export interface PersonaBrief {
  id: number;
  name: string;
  avatar_media_id: number | null;
}

export interface Client {
  id: number;
  /** The persona of the channel the client first wrote to. */
  persona_id: number;
  name: string;
  username: string | null;
  phone: string | null;
  timezone: string | null;
  notes: string;
  ai_enabled: boolean;
  passport: Passport;
  created_at: string;
  last_seen_at: string | null;
}

/** A row of «Лиды» (GET /clients). */
export interface Lead extends Client {
  persona: PersonaBrief | null;
  chats_count: number;
  messages_count: number;
}

export interface ClientPatch {
  name?: string;
  phone?: string | null;
  notes?: string;
  timezone?: string | null;
  ai_enabled?: boolean;
  passport?: PassportPatch;
}

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

/** ok — answered or answering now; wait — an intended pause; busy — her life; warn — a person is needed; off — AI off. */
export type PersonaStatusTone = 'ok' | 'wait' | 'busy' | 'warn' | 'off';

/** «Статус Марины»: what she does about a chat now and why it waits. Team-only (docs/attention.md). */
export interface PersonaStatus {
  code: string;
  tone: PersonaStatusTone;
  /** One short Russian line: «Прочитала, ответит около 20:40». */
  title: string;
  /** The reason or explanation; may be empty. */
  detail: string;
  /** When the pause ends (UTC ISO), if known. */
  until: string | null;
  /** The same moment as «HH:MM» in her time zone. */
  local_until: string | null;
}

export interface ChatRow {
  id: number;
  client: ChatRowClient;
  /** The persona that answers in this chat (the persona of its channel). */
  persona: PersonaBrief | null;
  /** The messenger of the chat (Telegram, Instagram, WhatsApp). */
  channel?: ChatChannel | null;
  ai_enabled: boolean;
  /** «Отвечать сразу»: this chat skips her phone checks (docs/attention.md). */
  attention_instant: boolean;
  /** «Без отказов» in this chat (docs/writing.md). */
  no_refusals: boolean;
  /** «Звонит сама»: she may call this client herself (docs/calls.md). */
  calls_out: boolean;
  needs_operator: boolean;
  unread_count: number;
  /** What she is doing about this chat now — visible to the team only. */
  persona_status: PersonaStatus;
  last_message_at: string | null;
  last_message: LastMessage | null;
}

export type ChatDetail = Omit<ChatRow, 'client'> & { client: Client };

export interface ClientDetail extends Client {
  persona: PersonaBrief | null;
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
  /** `/api/calls/{id}/recording` (stereo OGG: the client left, her right); null — no recording. */
  recording_url?: string | null;
  recording_s?: number | null;
}

export interface TimelineGeneration {
  id: number;
  kind: 'photo' | 'video' | 'voice' | 'video_note';
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

/** A planned contact: she writes or calls this client at `due_at` about `topic` (docs/calls.md). */
export type ContactKind = 'message' | 'call';
export type ContactSource = 'manager' | 'persona';
export type ContactStatus = 'planned' | 'started' | 'done' | 'failed' | 'cancelled';

export interface Contact {
  id: number;
  chat_id: number;
  kind: ContactKind;
  /** `persona` — she decided it herself; `manager` — planned from the dashboard. */
  source: ContactSource;
  topic: string;
  due_at: string;
  status: ContactStatus;
  /** What came of it (empty while planned). */
  result: string;
  created_by: string;
  created_at: string;
  finished_at: string | null;
  call_id: number | null;
}

/** POST /chats/{id}/contacts. `due_at` is ISO 8601 with the time zone offset. */
export interface ContactCreate {
  kind: ContactKind;
  topic: string;
  due_at: string;
}

/** The chronicler's summary of one persona-local day of a chat (docs/memory.md). */
export interface ChatDay {
  day: string;
  summary: string;
  facts: string[];
  /** `edited`: the owner changed it, the night job leaves it alone. */
  source: 'model' | 'edited';
  model: string;
  error: string | null;
  first_event_id: number | null;
  last_event_id: number | null;
  updated_at: string;
}

/** The rollup of a week (`week` = its Monday). */
export type ChatWeek = Omit<ChatDay, 'day'> & { week: string };

export interface ChatDaysResponse {
  /** Her local today (YYYY-MM-DD): today has no summary until her midnight. */
  today: string;
  items: ChatDay[];
  weeks: ChatWeek[];
}

export interface ChatDayPatch {
  summary: string;
  facts: string[];
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
  /** false = archived: answers nobody, plans no days, its Telegram accounts go offline. */
  is_active: boolean;
  /** Exact look in English for image models (eyes, hair, face, build). */
  appearance: string;
  voice_ambience_enabled: boolean;
  voice_id: string;
  voice_style: string;
  /** Speech speed of voice notes and calls, 0.8–1.5 (1.0 — as the model speaks). */
  voice_speed: number;
  ai_enabled: boolean;
  voice_enabled: boolean;
  photo_enabled: boolean;
  video_enabled: boolean;
  calls_enabled: boolean;
  /** Keep a stereo recording of each call; recordings older than `call_recording_days` are purged. */
  call_recording: boolean;
  call_recording_days: number;
  reply_delay_s: number;
}

export type PersonaPatch = Partial<Omit<Persona, 'id' | 'is_active'>>;

/** A row of «Персонажи» (GET /personas). */
export interface PersonaRow {
  id: number;
  name: string;
  avatar_media_id: number | null;
  is_active: boolean;
  ai_enabled: boolean;
  leads_count: number;
  channels: Channel[];
}

/** What «Клонировать» may copy (docs/personas.md). */
export type ClonePart =
  | 'basics' | 'prompt' | 'agents' | 'voice' | 'guard' | 'appearance' | 'world' | 'arcs' | 'media' | 'switches'
  | 'canon';

export interface BrokerModel {
  id: string;
  /** null = Broker does not know yet. */
  available: boolean | null;
  input_modalities: string[];
  output_modalities: string[];
  capabilities: string[];
}

// --- the persona's agents (docs/agents.md) -----------------------------------------------------

/** Which Broker keys pay for an agent's calls. */
export type KeyTier = 'auto' | 'paid' | 'free';

/** A model fits when it has all of these; an agent's filter is a list of alternatives. */
export interface ModelMatch {
  input: string[];
  output: string[];
  capabilities: string[];
}

/** One AI function of a persona as the code defines it (GET /agents/registry). */
export interface AgentSpec {
  key: string;
  title: string;
  group: string;
  description: string;
  port: string;
  model_filter: ModelMatch[];
  /** Defaults. */
  key_tier: KeyTier;
  model: string;
  /** Whose model is used when this agent has none. */
  fallback: string | null;
  /** The default editable prompt; null = the agent has no prompt to tune. */
  prompt: string | null;
  /** Fixed rules the code adds (shown read-only). */
  readonly_prompt: string | null;
  placeholders: string[];
  /** The default answer limit; null = not tunable. */
  max_tokens: number | null;
}

export interface AgentGroupInfo {
  key: string;
  title: string;
  description: string;
  agents: string[];
}

export interface AgentRegistry {
  groups: AgentGroupInfo[];
  agents: AgentSpec[];
  key_tiers: { value: KeyTier; label: string }[];
  max_tokens_range: { min: number; max: number };
}

/** One agent's settings of a persona: null / '' = by default. */
export type VideoProvider = 'google' | 'heygen';

export interface AgentSettings {
  video_provider?: VideoProvider;
  /** null = the default model; '' = not used (only where the default is a model). */
  model: string | null;
  key_tier: KeyTier;
  /** '' = the default prompt. */
  prompt: string;
  max_tokens: number | null;
}

/** How she treats notifications (docs/attention.md). */
export type AttentionMode = 'human' | 'free_only' | 'instant';

/** «Внимание» settings of a persona, edited on its agent card. */
export interface AttentionSettings {
  mode: AttentionMode;
  /** One multiplier on every sampled delay, 0.5–2. */
  pace: number;
  may_leave_read: boolean;
  after_missed_call: boolean;
  night_dnd: boolean;
}

/** GET /personas/{id}/agents. */
export interface PersonaAgents {
  agents: Record<string, AgentSettings & { effective_model: string }>;
  guard_rules: string;
  attention: AttentionSettings;
}

/** PUT /personas/{id}/agents. */
export interface PersonaAgentsPatch {
  agents: Record<string, AgentSettings>;
  guard_rules: string;
  attention: AttentionSettings;
}

export type VoiceGender = 'female' | 'male';

/** A TTS voice (GET /voices); its sample is GET /voices/{id}/sample. */
export interface Voice {
  id: string;
  label: string;
  gender: VoiceGender;
  /** A short Russian description: «уверенный», «мягкий». */
  tone: string;
}

// --- persona life (docs/persona-life.md) -------------------------------------------------------

export type BusyLevel = 'free' | 'partial' | 'away';
export type Ambience =
  | 'none' | 'quiet_home' | 'kitchen' | 'street' | 'park' | 'cafe' | 'car' | 'transport' | 'office';

export interface RoutineSlot {
  /** 0 = Monday … 6 = Sunday. */
  days: number[];
  start: string;
  end: string;
  activity: string;
  place: string;
  with_whom: string[];
  busy: BusyLevel;
  outfit: string;
  ambience: Ambience | null;
}

export interface LifePerson {
  name: string;
  relation: string;
  traits: string;
  contact: string;
  notes: string;
}

export interface Hobby {
  name: string;
  details: string;
  frequency: string;
}

export interface Place {
  name: string;
  kind: string;
  details: string;
}

/** Constant details of her look (sim/domain/life.Look). */
export interface Look {
  nails: string;
  earrings: string;
  jewelry: string;
  hair: string;
  other: string;
}

export interface Room {
  name: string;
  details: string;
  /** Where the furniture stands. */
  layout: string;
  /** Exactly which lamps. */
  lamps: string;
  /** What is outside the window, always the same. */
  window_view: string;
}

/** Her one apartment (sim/domain/life.Home). */
export interface Home {
  details: string;
  rooms: Room[];
}

export interface PersonaLife {
  routine: RoutineSlot[];
  people: LifePerson[];
  hobbies: Hobby[];
  places: Place[];
  look: Look;
  home: Home;
}

export type ArcStatus = 'active' | 'paused' | 'done';

export interface Arc {
  id: number;
  title: string;
  summary: string;
  stage: string;
  status: ArcStatus;
  started_on: string;
  ends_on: string | null;
  updated_at: string;
}

/** Her mood during an event of the day (docs/persona-life.md, «Настроение дня»). */
export type Mood = 'good' | 'calm' | 'tired' | 'irritated' | 'upset' | 'anxious';

export interface DayEvent {
  start: string;
  end: string;
  title: string;
  place: string;
  with_whom: string[];
  busy: BusyLevel;
  details: string;
  outfit: string;
  ambience: Ambience;
  /** Older plans have none: calm. */
  mood?: Mood;
  mood_why?: string;
}

export interface PersonaDay {
  day: string;
  events: DayEvent[];
  summary: string;
  source: 'model' | 'edited';
  model: string;
  error: string | null;
  updated_at: string;
}

export interface DaysResponse {
  /** Her local today (YYYY-MM-DD) and minute of the day. */
  today: string;
  now_minute: number;
  items: PersonaDay[];
}

export interface CanonFact {
  id: number;
  fact: string;
  status: 'active' | 'rejected';
  chat_id: number | null;
  event_id: number | null;
  created_at: string;
}

/** The messengers a persona can speak through (docs/channels.md). */
export type ChannelKind = 'telegram_user' | 'instagram' | 'whatsapp';

/** `pending` — a login in progress; `challenge` — the messenger wants a new login. */
export type ChannelStatus = 'new' | 'connected' | 'error' | 'disabled' | 'pending' | 'challenge';

export interface Channel {
  id: number;
  persona_id: number;
  kind: ChannelKind | string;
  title: string;
  status: ChannelStatus | string;
  account_label: string;
  last_error: string | null;
  last_activity_at: string | null;
}

/** What «Подключить» may offer on this server (`reason` says why a kind is not available). */
export interface ChannelKindInfo {
  kind: ChannelKind;
  label: string;
  available: boolean;
  reason: string;
}

export interface ChannelList extends Items<Channel> {
  kinds?: ChannelKindInfo[];
}

/** One sticker of her own Telegram account: from an installed set or her favourites (`faved`). */
export interface Sticker {
  id: number;
  channel_id: number;
  emoji: string;
  set_name: string;
  set_title: string;
  faved: boolean;
}

/** `POST …/stickers/sync` (202): the Telegram channels whose stickers are being re-read. */
export interface StickerSync {
  channels: number[];
}

/** The messenger of a chat (chat rows and the chat header show its icon). */
export interface ChatChannel {
  id: number;
  kind: ChannelKind | string;
  title: string;
}

/**
 * One step of connecting an account (no secrets ever come back): `qr` (Telegram, WhatsApp),
 * `password` (Telegram 2FA), `code` (Instagram 2FA, `detail` = totp | sms), `device` (approve in
 * the Instagram app, retried by itself), `challenge` (a code Instagram sent, `detail` = email |
 * sms), `manual` (check in the app, then «Повторить»).
 */
export type LoginStateName =
  | 'qr' | 'password' | 'code' | 'device' | 'challenge' | 'manual' | 'connected' | 'error' | 'none';

export interface LoginView {
  state: LoginStateName;
  message: string;
  /** Data URI of the QR code while `state` is `qr`. */
  qr: string | null;
  detail?: string | null;
}

export interface LoginResponse {
  channel: Channel;
  login: LoginView;
}
