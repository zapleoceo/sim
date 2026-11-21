import { api } from '@/lib/api';
import type { BrokerModel, Channel, Items, Media, Persona, PersonaPatch, Voice } from '@/lib/types';

export type MediaSourceFilter = 'all' | 'library' | 'generated';
export type MediaKindFilter = 'all' | 'photo' | 'video';

export interface MediaUpload {
  file: File;
  caption: string;
  tags: string[];
  isReference: boolean;
}

export type MediaPatch = Partial<Pick<Media, 'caption' | 'tags' | 'is_reference'>>;

export const personaApi = {
  get: () => api.get<Persona>('/persona'),
  /** Partial update: only the fields that changed. */
  update: (patch: PersonaPatch) => api.put<Persona>('/persona', patch),
  models: () => api.get<Items<BrokerModel>>('/models'),
  voices: () => api.get<Items<Voice>>('/voices'),
  instructions: () => api.get<{ text: string }>('/instructions'),
  channels: () => api.get<Items<Channel>>('/channels'),
};

export const mediaApi = {
  list: (source: MediaSourceFilter, kind: MediaKindFilter) =>
    api.get<Items<Media>>('/media', {
      params: { source: source === 'all' ? null : source, kind: kind === 'all' ? null : kind },
    }),
  upload: ({ file, caption, tags, isReference }: MediaUpload) => {
    const form = new FormData();
    form.set('file', file);
    form.set('caption', caption);
    form.set('tags', tags.join(','));
    form.set('is_reference', isReference ? 'true' : 'false');
    return api.post<Media>('/media', form);
  },
  update: (id: number, patch: MediaPatch) => api.patch<Media>(`/media/${id}`, patch),
  remove: (id: number) => api.delete(`/media/${id}`),
};
