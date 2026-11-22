import { api } from '@/lib/api';
import type {
  AgentRegistry, BrokerModel, Channel, ChannelList, ClonePart, Items, LoginResponse, Media, Persona, PersonaAgents,
  PersonaAgentsPatch, PersonaPatch, PersonaRow, Voice,
} from '@/lib/types';

export type MediaSourceFilter = 'all' | 'library' | 'generated';
export type MediaKindFilter = 'all' | 'photo' | 'video';

export interface MediaUpload {
  file: File;
  caption: string;
  tags: string[];
  isReference: boolean;
}

/** PATCH /api/media/{id}: `is_room_ref` pins a generated photo as the reference of its room. */
export type MediaPatch = Partial<Pick<Media, 'caption' | 'tags' | 'is_reference'> & { is_room_ref: boolean }>;

export interface TelegramLogin {
  api_id: number;
  api_hash: string;
  proxy: string;
}

/** `/personas/{id}<path>`: every persona-scoped route (docs/api.md). */
export const personaPath = (id: number, path = ''): string => `/personas/${id}${path}`;

export const personaApi = {
  list: () => api.get<Items<PersonaRow>>('/personas'),
  get: (id: number) => api.get<Persona>(personaPath(id)),
  /** Partial update: only the fields that changed. */
  update: (id: number, patch: PersonaPatch) => api.put<Persona>(personaPath(id), patch),
  /** Archive (false) or bring back (true). */
  setActive: (id: number, isActive: boolean) => api.patch<Persona>(personaPath(id), { is_active: isActive }),
  clone: (id: number, name: string, parts: ClonePart[]) =>
    api.post<Persona>(personaPath(id, '/clone'), { name, parts }),
  models: () => api.get<Items<BrokerModel>>('/models'),
  /** Delete call recordings: `days` null — older than her keeping period, 0 — all of them. */
  purgeCallRecordings: (id: number, days: number | null) =>
    api.post<{ deleted: number }>(personaPath(id, '/call-recordings/purge'), { days }),
  voices: () => api.get<Items<Voice>>('/voices'),
  /** A short WAV of the voice, as this persona would say it (synthesized once, cached by the server). */
  voiceSample: (voiceId: string, personaId: number) =>
    api.blob(`/voices/${encodeURIComponent(voiceId)}/sample`, { params: { persona_id: personaId } }),
};

/** The persona's agents (docs/agents.md): the registry, her settings, save and reset. */
export const agentApi = {
  registry: () => api.get<AgentRegistry>('/agents/registry'),
  get: (personaId: number) => api.get<PersonaAgents>(personaPath(personaId, '/agents')),
  save: (personaId: number, patch: PersonaAgentsPatch) =>
    api.put<PersonaAgents>(personaPath(personaId, '/agents'), patch),
  reset: (personaId: number, key: string) =>
    api.post<PersonaAgents>(personaPath(personaId, `/agents/${key}/reset`)),
};

/** One way into Instagram (docs/channels.md): password, the browser's sessionid or a session JSON. */
export interface InstagramLogin {
  proxy: string;
  username?: string;
  password?: string;
  sessionid?: string;
  settings_json?: string;
}

export interface WhatsAppLogin {
  phone: string;
}

/** The persona's channels and connecting a Telegram, Instagram or WhatsApp account. */
export const channelApi = {
  list: (personaId: number) => api.get<ChannelList>(personaPath(personaId, '/channels')),
  startTelegram: (personaId: number, body: TelegramLogin) =>
    api.post<LoginResponse>(personaPath(personaId, '/channels/telegram/login'), body),
  startInstagram: (personaId: number, body: InstagramLogin) =>
    api.post<LoginResponse>(personaPath(personaId, '/channels/instagram/login'), body),
  startWhatsApp: (personaId: number, body: WhatsAppLogin) =>
    api.post<LoginResponse>(personaPath(personaId, '/channels/whatsapp/login'), body),
  loginState: (personaId: number, channelId: number) =>
    api.get<LoginResponse>(personaPath(personaId, `/channels/${channelId}/login`)),
  password: (personaId: number, channelId: number, password: string) =>
    api.post<LoginResponse>(personaPath(personaId, `/channels/${channelId}/login/password`), { password }),
  /** An Instagram 2FA / challenge code; '' = «Повторить» after approving in the app. */
  code: (personaId: number, channelId: number, code: string) =>
    api.post<LoginResponse>(personaPath(personaId, `/channels/${channelId}/login/code`), { code }),
  cancel: (personaId: number, channelId: number) =>
    api.delete(personaPath(personaId, `/channels/${channelId}/login`)),
  disable: (personaId: number, channelId: number) =>
    api.post<Channel>(personaPath(personaId, `/channels/${channelId}/disable`)),
};

export const mediaApi = {
  list: (personaId: number, source: MediaSourceFilter, kind: MediaKindFilter) =>
    api.get<Items<Media>>(personaPath(personaId, '/media'), {
      params: { source: source === 'all' ? null : source, kind: kind === 'all' ? null : kind },
    }),
  upload: (personaId: number, { file, caption, tags, isReference }: MediaUpload) => {
    const form = new FormData();
    form.set('file', file);
    form.set('caption', caption);
    form.set('tags', tags.join(','));
    form.set('is_reference', isReference ? 'true' : 'false');
    return api.post<Media>(personaPath(personaId, '/media'), form);
  },
  update: (id: number, patch: MediaPatch) => api.patch<Media>(`/media/${id}`, patch),
  remove: (id: number) => api.delete(`/media/${id}`),
};
