import { api } from '@/lib/api';
import type {
  AgentRegistry, BrokerModel, Channel, ClonePart, Items, LoginResponse, Media, Persona, PersonaAgents,
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

export type MediaPatch = Partial<Pick<Media, 'caption' | 'tags' | 'is_reference'>>;

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
  voices: () => api.get<Items<Voice>>('/voices'),
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

/** The persona's channels and the QR login of a Telegram account. */
export const channelApi = {
  list: (personaId: number) => api.get<Items<Channel>>(personaPath(personaId, '/channels')),
  startLogin: (personaId: number, body: TelegramLogin) =>
    api.post<LoginResponse>(personaPath(personaId, '/channels/telegram/login'), body),
  loginState: (personaId: number, channelId: number) =>
    api.get<LoginResponse>(personaPath(personaId, `/channels/${channelId}/login`)),
  password: (personaId: number, channelId: number, password: string) =>
    api.post<LoginResponse>(personaPath(personaId, `/channels/${channelId}/login/password`), { password }),
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
