import { http } from './http';

export interface StudioState {
  photo_url: string | null;
  video_url: string | null;
  models: string[];
  text_available: boolean;
  media_available: boolean;
  detail: string;
}
export interface StudioMessage { role: 'user' | 'assistant'; content: string }

export const studioApi = {
  /** Silent: the studio page renders its own error block. */
  get: () => http.get<StudioState>('/api/studio', { silent: true }),
  chat: (model: string, messages: StudioMessage[]) =>
    http.post<{ text: string; model: string }>('/api/studio/chat', { model, messages }, { silent: true }),
};
