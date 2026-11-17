import { http } from './http';

export interface StudioState {
  photo_url: string | null;
  video_url: string | null;
  models: string[];
  text_available: boolean;
  media_available: boolean;
  speech_available?: boolean;
  video_available?: boolean;
  capabilities_verified?: boolean;
  media_status?: 'disabled';
  personal_access?: boolean;
  draft_scope?: string | null;
  detail: string;
}
export interface StudioMessage { role: 'user' | 'assistant'; content: string }
export interface StudioDraft { scope: string; revision: number; text: string }
export interface StudioOperation {
  operation_id: string;
  status: 'unknown' | 'complete';
  artifact_url?: string | null;
  kind?: 'speech' | 'video';
  created_at?: string;
}

export const studioApi = {
  draft: () => http.get<StudioDraft>('/api/studio/media/draft/video', { silent: true }),
  saveDraft: (draft: StudioDraft) => http.put<StudioDraft>('/api/studio/media/draft/video', draft, { silent: true }),
  /** Silent: the studio page renders its own error block. */
  get: () => http.get<StudioState>('/api/studio', { silent: true }),
  media: (operation_id: string, kind: 'speech' | 'video', text: string) =>
    http.post<StudioOperation>('/api/studio/media', { operation_id, kind, text }, { silent: true }),
  operation: (id: string) => http.get<StudioOperation>(`/api/studio/media/${id}`, { silent: true }),
  operations: () => http.get<{ operations: StudioOperation[] }>('/api/studio/media', { silent: true }),
  reconcile: (id: string) => http.post<StudioOperation>(`/api/studio/media/${id}/reconcile`, {}, { silent: true }),
  chat: (model: string, messages: StudioMessage[]) =>
    http.post<{ text: string; model: string }>('/api/studio/chat', { model, messages }, { silent: true }),
};
